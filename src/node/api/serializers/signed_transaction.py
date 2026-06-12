import hashlib
import json
from typing import List, Self

from pydantic import Field

from core.models import NbeSerializer
from models.transactions.transaction import Transaction
from node.api.serializers.operation import (
    ChannelInscribeOpSerializer,
    LedgerOpSerializer,
    SDPActiveOpSerializer,
    SDPDeclareOpSerializer,
)
from node.api.serializers.proof import (
    Ed25519SignatureSerializer,
    OperationProofSerializerField,
    ZkAndEd25519SignaturesSerializer,
    ZkSignatureSerializer,
)
from node.api.serializers.transaction import TransactionSerializer
from utils.protocols import FromRandom


def _proof_to_internal(proof) -> dict:
    if isinstance(proof, ZkSignatureSerializer):
        return {"type": "Zk", "signature": proof.to_bytes()}
    if isinstance(proof, Ed25519SignatureSerializer):
        return {"type": "Ed25519", "signature": proof.root}
    if isinstance(proof, ZkAndEd25519SignaturesSerializer):
        return {
            "type": "ZkAndEd25519",
            "zk_signature": proof.zk_signature.to_bytes(),
            "ed25519_signature": proof.ed25519_signature,
        }
    raise ValueError(f"Unsupported proof type: {type(proof).__name__}")


class SignedTransactionSerializer(NbeSerializer, FromRandom):
    transaction: TransactionSerializer = Field(alias="mantle_tx", description="Transaction.")
    operations_proofs: List[OperationProofSerializerField] = Field(
        alias="ops_proofs",
        description="List of OperationProof. Order should match `Self::transaction::ops`.",
    )

    def _compute_hash(self) -> bytes:
        # Prefer the canonical hash reported by the node (newer nodes include it
        # in mantle_tx). A locally computed JSON hash will NOT match the chain's
        # real tx hash, so it is only a last-resort fallback for older nodes.
        if self.transaction.hash is not None:
            return self.transaction.hash
        data = self.transaction.model_dump(mode="json", exclude={"hash"})
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).digest()

    def into_transaction(self) -> Transaction:
        ops = self.transaction.ops
        if len(ops) != len(self.operations_proofs):
            raise ValueError(
                f"Number of ops ({len(ops)}) does not match number of op proofs "
                f"({len(self.operations_proofs)})."
            )

        operations: List[dict] = []
        for op, proof in zip(ops, self.operations_proofs):
            if isinstance(op, LedgerOpSerializer):
                if not isinstance(proof, ZkSignatureSerializer):
                    raise ValueError(
                        f"Expected a ZkSig (Groth16) proof for the ledger op, got {type(proof).__name__}."
                    )
                operations.append(
                    {
                        "content": {
                            "type": "LedgerTransfer",
                            "inputs": list(op.inputs),
                            "outputs": [o.into_note() for o in op.outputs],
                        },
                        "proof": _proof_to_internal(proof),
                    }
                )
            elif isinstance(op, ChannelInscribeOpSerializer):
                if not isinstance(proof, Ed25519SignatureSerializer):
                    raise ValueError(
                        f"Expected an Ed25519Sig proof for the channel inscribe op, got {type(proof).__name__}."
                    )
                operations.append(
                    {
                        "content": {
                            "type": "ChannelInscribe",
                            "channel_id": op.channel_id,
                            "inscription": op.inscription,
                            "parent": op.parent,
                            "signer": op.signer,
                        },
                        "proof": _proof_to_internal(proof),
                    }
                )
            elif isinstance(op, SDPDeclareOpSerializer):
                operations.append(
                    {
                        "content": {
                            "type": "SDPDeclare",
                            "service_type": op.service_type,
                            "locators": list(op.locators),
                            "provider_id": op.provider_id,
                            "zk_id": op.zk_id,
                            "locked_note_id": op.locked_note_id,
                        },
                        "proof": _proof_to_internal(proof),
                    }
                )
            elif isinstance(op, SDPActiveOpSerializer):
                operations.append(
                    {
                        "content": {
                            "type": "SDPActive",
                            "declaration_id": op.declaration_id,
                            "nonce": op.nonce,
                            "metadata": op.metadata,
                        },
                        "proof": _proof_to_internal(proof),
                    }
                )
            else:
                raise ValueError(f"Unsupported mantle op type: {type(op).__name__}")

        return Transaction.model_validate(
            {
                "hash": self._compute_hash(),
                "operations": operations,
                "execution_gas_price": self.transaction.execution_gas_price,
                "storage_gas_price": self.transaction.storage_gas_price,
            }
        )

    @classmethod
    def from_random(cls) -> Self:
        transaction = TransactionSerializer.from_random()
        operations_proofs = [ZkSignatureSerializer.from_random() for _ in range(len(transaction.ops))]
        return cls.model_validate(
            {
                "mantle_tx": transaction,
                "ops_proofs": operations_proofs,
            }
        )
