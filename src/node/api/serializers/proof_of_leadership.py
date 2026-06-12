from abc import ABC, abstractmethod
from typing import Annotated, Optional, Self, Union

from pydantic import Field
from rusty_results import Option

from core.models import NbeSerializer
from models.header.proof_of_leadership import (
    Groth16ProofOfLeadership,
    ProofOfLeadership,
)
from node.api.serializers.fields import BytesFromHexOrIntArray
from utils.protocols import EnforceSubclassFromRandom
from utils.random import random_bytes


class ProofOfLeadershipSerializer(NbeSerializer, EnforceSubclassFromRandom, ABC):
    @abstractmethod
    def into_proof_of_leadership(self) -> ProofOfLeadership:
        raise NotImplementedError


class Groth16LeaderProofSerializer(ProofOfLeadershipSerializer, NbeSerializer):
    entropy_contribution: BytesFromHexOrIntArray = Field(description="Fr integer.")
    leader_key: BytesFromHexOrIntArray = Field(description="Hash in hex format.")
    proof: BytesFromHexOrIntArray = Field(description="Groth16 proof bytes (128B) in hex format.")
    voucher_cm: BytesFromHexOrIntArray = Field(description="Hash.")

    def into_proof_of_leadership(self) -> ProofOfLeadership:
        return Groth16ProofOfLeadership.model_validate(
            {
                "entropy_contribution": self.entropy_contribution,
                "leader_key": self.leader_key,
                "proof": self.proof,
                "voucher_cm": self.voucher_cm,
            }
        )

    @classmethod
    def from_random(cls, *, slot: Option[int]) -> Self:
        return cls.model_validate(
            {
                "entropy_contribution": random_bytes(32).hex(),
                "leader_key": random_bytes(32).hex(),
                "proof": random_bytes(128).hex(),
                "voucher_cm": random_bytes(32).hex(),
            }
        )


# Fake Variant that never resolves to allow union type checking to work
# TODO: Remove this when another Variant is added
from pydantic import BeforeValidator


def _always_fail(_):
    raise ValueError("Never matches.")


_NeverType = Annotated[object, BeforeValidator(_always_fail)]
#


ProofOfLeadershipVariants = Union[
    Groth16LeaderProofSerializer, _NeverType
]  # TODO: Remove _NeverType when another Variant is added
ProofOfLeadershipSerializerField = Annotated[ProofOfLeadershipVariants, Field(union_mode="left_to_right")]
