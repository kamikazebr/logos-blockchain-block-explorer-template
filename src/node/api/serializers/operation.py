import logging
from random import randint
from typing import Annotated, Any, List, Optional, Self, Union

from pydantic import BeforeValidator, Field

from core.models import NbeSerializer
from models.transactions.operations.contents import SDPDeclareServiceType
from node.api.serializers.fields import BytesFromHex, BytesFromHexOrIntArray
from node.api.serializers.note import NoteSerializer
from utils.protocols import FromRandom
from utils.random import random_bytes

logger = logging.getLogger(__name__)

# Mantle op opcodes.
OPCODE_LEDGER = 0
OPCODE_CHANNEL_SET_KEYS = 16
OPCODE_CHANNEL_INSCRIBE = 17
OPCODE_SDP_DECLARE = 32
OPCODE_SDP_ACTIVE = 34


class LedgerOpSerializer(NbeSerializer, FromRandom):
    """Mantle ledger op (opcode 0): consumes input notes and produces outputs."""

    inputs: List[BytesFromHex] = Field(description="Input note IDs (Fr).")
    outputs: List[NoteSerializer]

    @classmethod
    def from_random(cls) -> Self:
        n_inputs = 0 if randint(0, 1) <= 0.5 else randint(1, 5)
        n_outputs = 0 if randint(0, 1) <= 0.5 else randint(1, 5)
        return cls.model_validate(
            {
                "inputs": [random_bytes(32).hex() for _ in range(n_inputs)],
                "outputs": [NoteSerializer.from_random() for _ in range(n_outputs)],
            }
        )


class ChannelSetKeysOpSerializer(NbeSerializer, FromRandom):
    """Channel set-keys op (opcode 16): sets the signing keys of a channel."""

    channel: BytesFromHexOrIntArray = Field(description="Channel ID.")
    keys: List[BytesFromHexOrIntArray] = Field(description="Channel signing public keys.")

    @classmethod
    def from_random(cls) -> Self:
        return cls.model_validate(
            {
                "channel": random_bytes(32).hex(),
                "keys": [random_bytes(32).hex()],
            }
        )


class ChannelInscribeOpSerializer(NbeSerializer, FromRandom):
    """Channel inscribe op (opcode 17): writes an inscription to a channel."""

    channel_id: BytesFromHex = Field(description="Channel ID in hex format.")
    inscription: BytesFromHexOrIntArray = Field(
        description="Inscription bytes (int array on older nodes, hex string on newer ones)."
    )
    parent: BytesFromHex = Field(description="Parent inscription hash in hex format.")
    signer: BytesFromHex = Field(description="Signer public key in hex format.")

    @classmethod
    def from_random(cls) -> Self:
        return cls.model_validate(
            {
                "channel_id": random_bytes(32).hex(),
                "inscription": list(random_bytes(32)),
                "parent": random_bytes(32).hex(),
                "signer": random_bytes(32).hex(),
            }
        )


class SDPDeclareOpSerializer(NbeSerializer, FromRandom):
    """SDP declare op (opcode 32): registers a service provider."""

    service_type: SDPDeclareServiceType
    locators: List[str] = Field(description="Multiaddr strings, e.g. /ip4/.../udp/.../quic-v1.")
    provider_id: BytesFromHex = Field(description="Provider ID in hex format.")
    zk_id: BytesFromHex = Field(description="ZK ID in hex format.")
    locked_note_id: BytesFromHex = Field(description="Locked note ID in hex format.")

    @classmethod
    def from_random(cls) -> Self:
        return cls.model_validate(
            {
                "service_type": "BN",
                "locators": ["/ip4/127.0.0.1/udp/3400/quic-v1"],
                "provider_id": random_bytes(32).hex(),
                "zk_id": random_bytes(32).hex(),
                "locked_note_id": random_bytes(32).hex(),
            }
        )


class SDPActiveOpSerializer(NbeSerializer, FromRandom):
    """SDP active op (opcode 34): proves a declared provider is online."""

    declaration_id: BytesFromHex = Field(description="Declaration ID in hex format.")
    nonce: int = Field(description="Activity nonce in u64 format.")
    metadata: Optional[Any] = Field(
        default=None,
        description="Service-specific metadata (e.g. Blend session/proofs). Stored verbatim.",
    )

    @classmethod
    def from_random(cls) -> Self:
        return cls.model_validate(
            {
                "declaration_id": random_bytes(32).hex(),
                "nonce": randint(0, 1_000),
                "metadata": None,
            }
        )


class UnknownOpSerializer(NbeSerializer):
    """Fallback for opcodes without a typed serializer.

    Preserves the opcode and raw payload verbatim so unknown (e.g. newly
    introduced) op types never break block ingestion.
    """

    opcode: int
    payload: Optional[Any] = None


OPCODE_TO_SERIALIZER: dict[int, type] = {
    OPCODE_LEDGER: LedgerOpSerializer,
    OPCODE_CHANNEL_SET_KEYS: ChannelSetKeysOpSerializer,
    OPCODE_CHANNEL_INSCRIBE: ChannelInscribeOpSerializer,
    OPCODE_SDP_DECLARE: SDPDeclareOpSerializer,
    OPCODE_SDP_ACTIVE: SDPActiveOpSerializer,
}


MantleOpSerializerVariants = Union[
    LedgerOpSerializer,
    ChannelSetKeysOpSerializer,
    ChannelInscribeOpSerializer,
    SDPDeclareOpSerializer,
    SDPActiveOpSerializer,
    UnknownOpSerializer,
]
_MANTLE_OP_SERIALIZER_CLASSES = tuple(OPCODE_TO_SERIALIZER.values()) + (UnknownOpSerializer,)


def _parse_mantle_op(data: Any) -> MantleOpSerializerVariants:
    if isinstance(data, _MANTLE_OP_SERIALIZER_CLASSES):
        return data
    if isinstance(data, dict) and "opcode" in data:
        opcode = data["opcode"]
        serializer_class = OPCODE_TO_SERIALIZER.get(opcode)
        if serializer_class is None:
            logger.warning(f"No typed serializer for mantle op opcode {opcode}; storing it verbatim.")
            return UnknownOpSerializer.model_validate(data)
        return serializer_class.model_validate(data["payload"])
    raise ValueError(f"Cannot parse mantle op from {type(data).__name__}.")


MantleOpSerializerField = Annotated[MantleOpSerializerVariants, BeforeValidator(_parse_mantle_op)]
