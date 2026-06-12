"""Tests for node API serializers across node versions.

Node API formats drifted between releases:
- 0.1.2 nodes: flat cryptarchia/info, byte fields as int arrays, mantle_tx
  carries gas prices and no hash.
- Newer nodes: info nested under "cryptarchia_info" with mode as an object,
  byte fields as hex strings, mantle_tx carries its canonical hash and no gas
  prices.

The serializers must accept both. The new-format block fixture
(fixtures/block_new_format.json) is a real block captured from a node.
"""

import json
from pathlib import Path

import pytest

from node.api.http import adapt_storage_block_payload, normalize_info_payload
from node.api.serializers.block import BlockSerializer
from node.api.serializers.fields import bytes_from_hex_or_intarray
from node.api.serializers.health import HealthSerializer
from node.api.serializers.info import InfoSerializer
from node.api.serializers.operation import ChannelSetKeysOpSerializer, UnknownOpSerializer
from node.api.serializers.proof import Ed25519SignatureSerializer, ZkSignatureSerializer

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def new_format_block() -> dict:
    return json.loads((FIXTURES / "block_new_format.json").read_text())


@pytest.fixture
def old_format_storage_block() -> dict:
    """A real POST storage/block response captured from a 0.1.2 node.

    The header carries no id (storage/block does not echo it); the requested
    hash is stored under "_requested_hash" alongside the payload.
    """
    return json.loads((FIXTURES / "block_old_format_storage.json").read_text())


@pytest.fixture
def old_format_signed_tx() -> dict:
    """A signed tx as serialized by 0.1.2 nodes: int arrays, gas prices, no hash."""
    return {
        "mantle_tx": {
            "ops": [
                {
                    "opcode": 17,
                    "payload": {
                        "channel_id": "01" * 32,
                        "inscription": [104, 101, 108, 108, 111],  # b"hello"
                        "parent": "02" * 32,
                        "signer": "03" * 32,
                    },
                }
            ],
            "execution_gas_price": 7,
            "storage_gas_price": 11,
        },
        "ops_proofs": [{"Ed25519Sig": list(range(64))}],
    }


class TestInfoNormalization:
    def test_old_flat_format(self):
        payload = {"lib": "aa", "tip": "bb", "slot": 1, "height": 2, "mode": "Online"}
        info = InfoSerializer.model_validate(normalize_info_payload(payload))
        assert info.mode == "Online"
        assert info.height == 2

    def test_new_nested_format(self):
        payload = {
            "cryptarchia_info": {"lib": "aa", "lib_slot": 9, "tip": "bb", "slot": 10, "height": 3},
            "mode": {"Started": "Online"},
        }
        info = InfoSerializer.model_validate(normalize_info_payload(payload))
        assert info.mode == "Online"
        assert info.height == 3
        assert info.tip == "bb"


class TestHexOrIntArrayField:
    def test_accepts_hex_string(self):
        assert bytes_from_hex_or_intarray("68656c6c6f") == b"hello"

    def test_accepts_int_array(self):
        assert bytes_from_hex_or_intarray([104, 101, 108, 108, 111]) == b"hello"

    def test_rejects_other_types(self):
        with pytest.raises(ValueError):
            bytes_from_hex_or_intarray(42)


class TestNewFormatBlock:
    def test_parses_real_block(self, new_format_block):
        block = BlockSerializer.model_validate(new_format_block)
        assert block.header.slot == new_format_block["header"]["slot"]
        assert len(block.transactions) == len(new_format_block["transactions"])

    def test_tx_hash_comes_from_node(self, new_format_block):
        block = BlockSerializer.model_validate(new_format_block)
        tx = block.transactions[0].into_transaction()
        expected = bytes.fromhex(new_format_block["transactions"][0]["mantle_tx"]["hash"])
        assert tx.hash == expected

    def test_inscription_decodes_from_hex(self, new_format_block):
        block = BlockSerializer.model_validate(new_format_block)
        op = block.transactions[0].transaction.ops[0]
        raw_hex = new_format_block["transactions"][0]["mantle_tx"]["ops"][0]["payload"]["inscription"]
        assert op.inscription == bytes.fromhex(raw_hex)
        # Sequencer inscriptions reference LEZ program accounts.
        assert b"/LEZ/" in op.inscription

    def test_ed25519_proof_from_hex(self, new_format_block):
        block = BlockSerializer.model_validate(new_format_block)
        proof = block.transactions[0].operations_proofs[0]
        assert isinstance(proof, Ed25519SignatureSerializer)
        assert len(proof.root) == 64

    def test_into_block_roundtrip(self, new_format_block):
        block = BlockSerializer.model_validate(new_format_block).into_block()
        assert block.hash == bytes.fromhex(new_format_block["header"]["id"])
        assert block.transactions[0].operations[0].content.type == "ChannelInscribe"

    def test_missing_gas_prices_default_to_zero(self, new_format_block):
        block = BlockSerializer.model_validate(new_format_block)
        tx = block.transactions[0].into_transaction()
        assert tx.execution_gas_price == 0
        assert tx.storage_gas_price == 0


class TestOldFormatTransaction:
    def test_parses(self, old_format_signed_tx):
        block = BlockSerializer.model_validate(
            {"header": _minimal_header(), "transactions": [old_format_signed_tx]}
        )
        tx = block.transactions[0].into_transaction()
        assert tx.execution_gas_price == 7
        assert tx.storage_gas_price == 11

    def test_inscription_from_int_array(self, old_format_signed_tx):
        block = BlockSerializer.model_validate(
            {"header": _minimal_header(), "transactions": [old_format_signed_tx]}
        )
        assert block.transactions[0].transaction.ops[0].inscription == b"hello"

    def test_hash_fallback_is_deterministic(self, old_format_signed_tx):
        def parse():
            return BlockSerializer.model_validate(
                {"header": _minimal_header(), "transactions": [old_format_signed_tx]}
            ).transactions[0]

        first, second = parse().into_transaction(), parse().into_transaction()
        assert first.hash == second.hash
        assert len(first.hash) == 32


class TestUnknownOps:
    def test_unknown_opcode_is_preserved_not_fatal(self, new_format_block):
        new_format_block["transactions"][0]["mantle_tx"]["ops"][0]["opcode"] = 99
        block = BlockSerializer.model_validate(new_format_block)
        op = block.transactions[0].transaction.ops[0]
        assert isinstance(op, UnknownOpSerializer)
        assert op.opcode == 99
        content = block.transactions[0].into_transaction().operations[0].content
        assert content.type == "Unknown"
        assert content.opcode == 99
        assert content.payload is not None  # raw payload preserved verbatim

    def test_unknown_op_with_noproof_is_preserved_not_fatal(self, new_format_block):
        # e.g. a LeaderClaim (opcode 48) carries no proof; neither the op nor
        # the "NoProof" unit variant should break ingestion.
        tx = new_format_block["transactions"][0]
        tx["mantle_tx"]["ops"][0] = {"opcode": 48, "payload": {"rewards_root": "aa" * 32}}
        tx["ops_proofs"][0] = "NoProof"
        block = BlockSerializer.model_validate(new_format_block)
        operation = block.transactions[0].into_transaction().operations[0]
        assert operation.content.type == "Unknown"
        assert operation.content.opcode == 48
        assert operation.proof.type == "Unknown"
        assert operation.proof.raw == "NoProof"


class TestChannelSetKeysOp:
    @pytest.fixture
    def setkeys_sample(self) -> dict:
        """Real opcode 16 op + proof captured from the public testnet."""
        samples = json.loads((FIXTURES / "ops_samples_testnet.json").read_text())
        return samples["16"]

    def test_real_sample_parses(self, new_format_block, setkeys_sample):
        tx = new_format_block["transactions"][0]
        tx["mantle_tx"]["ops"] = [{"opcode": 16, "payload": setkeys_sample["payload"]}]
        tx["ops_proofs"] = [setkeys_sample["proof"]]
        block = BlockSerializer.model_validate(new_format_block)
        op = block.transactions[0].transaction.ops[0]
        assert isinstance(op, ChannelSetKeysOpSerializer)
        assert op.channel == bytes.fromhex(setkeys_sample["payload"]["channel"])
        assert len(op.keys) == len(setkeys_sample["payload"]["keys"])
        content = block.transactions[0].into_transaction().operations[0].content
        assert content.type == "ChannelSetKeys"


class TestLegacyStorageBlock:
    def test_adapt_injects_requested_hash(self):
        payload = {"header": {"slot": 1}, "transactions": []}
        adapted = adapt_storage_block_payload(payload, "ab" * 32)
        assert adapted["header"]["id"] == "ab" * 32
        assert payload["header"] == {"slot": 1}  # original untouched

    def test_adapt_keeps_existing_id(self):
        payload = {"header": {"id": "cc" * 32, "slot": 1}}
        adapted = adapt_storage_block_payload(payload, "ab" * 32)
        assert adapted["header"]["id"] == "cc" * 32

    def test_real_storage_response_parses(self, old_format_storage_block):
        requested_hash = old_format_storage_block.pop("_requested_hash")
        adapted = adapt_storage_block_payload(old_format_storage_block, requested_hash)
        block = BlockSerializer.model_validate(adapted)
        assert block.header.hash == bytes.fromhex(requested_hash)
        assert block.header.slot == old_format_storage_block["header"]["slot"]

    def test_real_storage_tx_parses_with_gas_and_zk_proof(self, old_format_storage_block):
        requested_hash = old_format_storage_block.pop("_requested_hash")
        mantle_tx = old_format_storage_block["transactions"][0]["mantle_tx"]
        adapted = adapt_storage_block_payload(old_format_storage_block, requested_hash)
        block = BlockSerializer.model_validate(adapted)
        signed_tx = block.transactions[0]
        assert isinstance(signed_tx.operations_proofs[0], ZkSignatureSerializer)
        tx = signed_tx.into_transaction()
        assert tx.execution_gas_price == mantle_tx["execution_gas_price"]
        assert tx.storage_gas_price == mantle_tx["storage_gas_price"]
        assert len(tx.hash) == 32  # computed fallback (no node-provided hash)


class TestHealthNodeApi:
    def test_carries_detected_generation(self):
        health = HealthSerializer.from_healthy(node_api="legacy (<= 0.1.2)").into_health()
        assert health.healthy is True
        assert health.node_api == "legacy (<= 0.1.2)"

    def test_defaults_to_none(self):
        health = HealthSerializer.from_unhealthy().into_health()
        assert health.node_api is None


def _minimal_header() -> dict:
    return {
        "id": "aa" * 32,
        "parent_block": "bb" * 32,
        "slot": 1,
        "block_root": "cc" * 32,
        "proof_of_leadership": {
            "proof": "dd" * 32,
            "entropy_contribution": "ee" * 32,
            "leader_key": "ff" * 32,
            "voucher_cm": "ab" * 32,
        },
    }
