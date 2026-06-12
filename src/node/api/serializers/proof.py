from abc import ABC, abstractmethod
from typing import Annotated, Any, Optional, Self, Union

from pydantic import BeforeValidator, Field, RootModel

from core.models import NbeSerializer
from models.transactions.operations.proofs import (
    Ed25519Signature,
    NbeSignature,
    UnknownProof as UnknownSignature,
    ZkAndEd25519Signature,
    ZkSignature,
)
from node.api.serializers.fields import BytesFromHex, BytesFromHexOrIntArray
from utils.protocols import EnforceSubclassFromRandom
from utils.random import random_bytes


class OperationProofSerializer(EnforceSubclassFromRandom, ABC):
    @abstractmethod
    def into_operation_proof(cls) -> NbeSignature:
        raise NotImplementedError


class Ed25519SignatureSerializer(OperationProofSerializer, RootModel[bytes]):
    root: BytesFromHexOrIntArray

    def into_operation_proof(self) -> NbeSignature:
        return Ed25519Signature.model_validate(
            {
                "signature": self.root,
            }
        )

    @classmethod
    def from_random(cls, *args, **kwargs) -> Self:
        return cls.model_validate(list(random_bytes(64)))


class ZkSignatureSerializer(OperationProofSerializer, NbeSerializer):
    """Groth16 ZK proof: pi_a (32B) + pi_b (64B) + pi_c (32B) = 128 bytes total."""

    pi_a: BytesFromHexOrIntArray
    pi_b: BytesFromHexOrIntArray
    pi_c: BytesFromHexOrIntArray

    def to_bytes(self) -> bytes:
        return self.pi_a + self.pi_b + self.pi_c

    def into_operation_proof(self) -> NbeSignature:
        return ZkSignature.model_validate(
            {
                "signature": self.to_bytes(),
            }
        )

    @classmethod
    def from_random(cls, *args, **kwargs) -> Self:
        return cls.model_validate(
            {
                "pi_a": list(random_bytes(32)),
                "pi_b": list(random_bytes(64)),
                "pi_c": list(random_bytes(32)),
            }
        )


class ZkAndEd25519SignaturesSerializer(OperationProofSerializer, NbeSerializer):
    zk_signature: ZkSignatureSerializer = Field(alias="zk_sig")
    ed25519_signature: BytesFromHex = Field(alias="ed25519_sig")

    def into_operation_proof(self) -> NbeSignature:
        return ZkAndEd25519Signature.model_validate(
            {
                "zk_signature": self.zk_signature.to_bytes(),
                "ed25519_signature": self.ed25519_signature,
            }
        )

    @classmethod
    def from_random(cls, *args, **kwargs) -> Self:
        return ZkAndEd25519SignaturesSerializer.model_validate(
            {
                "zk_sig": {
                    "pi_a": list(random_bytes(32)),
                    "pi_b": list(random_bytes(64)),
                    "pi_c": list(random_bytes(32)),
                },
                "ed25519_sig": random_bytes(64).hex(),
            }
        )


class UnknownProofSerializer(OperationProofSerializer, NbeSerializer):
    """Fallback for proof variants without a typed serializer (e.g. NoProof).

    Preserves the raw value verbatim so unknown proof types never break block
    ingestion.
    """

    raw: Optional[Any] = None

    def into_operation_proof(self) -> NbeSignature:
        return UnknownSignature.model_validate({"raw": self.raw})

    @classmethod
    def from_random(cls, *args, **kwargs) -> Self:
        return cls.model_validate({"raw": "NoProof"})


PROOF_TAG_TO_SERIALIZER = {
    "Ed25519Sig": Ed25519SignatureSerializer,
    "ZkSig": ZkSignatureSerializer,
    "ZkAndEd25519Sigs": ZkAndEd25519SignaturesSerializer,
}


def _parse_proof(data: Any) -> OperationProofSerializer:
    if isinstance(data, OperationProofSerializer):
        return data
    if isinstance(data, dict):
        for tag, serializer_class in PROOF_TAG_TO_SERIALIZER.items():
            if tag in data:
                return serializer_class.model_validate(data[tag])
    # Unit variants (e.g. "NoProof") arrive as plain strings; unknown tagged
    # variants arrive as dicts that matched no known tag. Keep them verbatim.
    return UnknownProofSerializer.model_validate({"raw": data})


OperationProofSerializerVariants = Union[
    Ed25519SignatureSerializer, ZkSignatureSerializer, ZkAndEd25519SignaturesSerializer, UnknownProofSerializer
]
OperationProofSerializerField = Annotated[
    OperationProofSerializerVariants,
    BeforeValidator(_parse_proof),
]
