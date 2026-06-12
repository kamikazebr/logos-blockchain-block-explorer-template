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

from node.api.http import normalize_info_payload
from node.api.serializers.block import BlockSerializer
from node.api.serializers.fields import bytes_from_hex_or_intarray
from node.api.serializers.info import InfoSerializer
from node.api.serializers.proof import Ed25519SignatureSerializer

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def new_format_block() -> dict:
    return json.loads((FIXTURES / "block_new_format.json").read_text())


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


class TestUnsupportedOpcode:
    def test_raises_with_known_opcodes_listed(self, new_format_block):
        new_format_block["transactions"][0]["mantle_tx"]["ops"][0]["opcode"] = 99
        with pytest.raises(ValueError, match="opcode 99"):
            BlockSerializer.model_validate(new_format_block)


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
