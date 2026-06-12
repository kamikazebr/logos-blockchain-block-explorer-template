from random import randint
from typing import List, Optional, Self

from pydantic import Field

from core.models import NbeSerializer
from node.api.serializers.fields import BytesFromHex
from node.api.serializers.operation import LedgerOpSerializer, MantleOpSerializerField
from utils.protocols import FromRandom


class TransactionSerializer(NbeSerializer, FromRandom):
    ops: List[MantleOpSerializerField]
    # Newer nodes include the canonical tx hash in mantle_tx; older ones don't.
    hash: Optional[BytesFromHex] = Field(default=None, description="Canonical tx hash (newer nodes only).")
    # Gas prices were dropped from mantle_tx on newer nodes; default to 0 there.
    execution_gas_price: int = Field(default=0, description="Integer in u64 format.")
    storage_gas_price: int = Field(default=0, description="Integer in u64 format.")

    @classmethod
    def from_random(cls) -> Self:
        n = 1 if randint(0, 1) <= 0.5 else randint(1, 3)
        ops = [LedgerOpSerializer.from_random() for _ in range(n)]
        return cls.model_validate(
            {
                "ops": ops,
                "execution_gas_price": randint(1, 10_000),
                "storage_gas_price": randint(1, 10_000),
            }
        )
