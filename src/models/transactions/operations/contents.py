from enum import Enum
from typing import Any, List, Literal, Optional

from core.models import NbeSchema
from core.types import HexBytes
from models.transactions.notes import Note


class ContentType(Enum):
    LEDGER_TRANSFER = "LedgerTransfer"
    CHANNEL_INSCRIBE = "ChannelInscribe"
    CHANNEL_BLOB = "ChannelBlob"
    CHANNEL_SET_KEYS = "ChannelSetKeys"
    SDP_DECLARE = "SDPDeclare"
    SDP_WITHDRAW = "SDPWithdraw"
    SDP_ACTIVE = "SDPActive"
    LEADER_CLAIM = "LeaderClaim"


class NbeContent(NbeSchema):
    type: str


class LedgerTransfer(NbeContent):
    type: Literal["LedgerTransfer"] = "LedgerTransfer"
    inputs: List[HexBytes]
    outputs: List[Note]


class ChannelInscribe(NbeContent):
    type: Literal["ChannelInscribe"] = "ChannelInscribe"
    channel_id: HexBytes
    inscription: HexBytes
    parent: HexBytes
    signer: HexBytes


class ChannelBlob(NbeContent):
    type: Literal["ChannelBlob"] = "ChannelBlob"
    channel: HexBytes
    blob: HexBytes
    blob_size: int
    da_storage_gas_price: int
    parent: HexBytes
    signer: HexBytes


class ChannelSetKeys(NbeContent):
    type: Literal["ChannelSetKeys"] = "ChannelSetKeys"
    channel: HexBytes
    keys: List[bytes]


class SDPDeclareServiceType(Enum):
    BN = "BN"
    DA = "DA"


class SDPDeclare(NbeContent):
    type: Literal["SDPDeclare"] = "SDPDeclare"
    service_type: SDPDeclareServiceType
    locators: List[str]
    provider_id: HexBytes
    zk_id: HexBytes
    locked_note_id: HexBytes


class SDPWithdraw(NbeContent):
    type: Literal["SDPWithdraw"] = "SDPWithdraw"
    declaration_id: HexBytes
    nonce: HexBytes


class SDPActive(NbeContent):
    type: Literal["SDPActive"] = "SDPActive"
    declaration_id: HexBytes
    nonce: int
    metadata: Optional[Any] = None


class LeaderClaim(NbeContent):
    type: Literal["LeaderClaim"] = "LeaderClaim"
    rewards_root: HexBytes
    voucher_nullifier: HexBytes
    mantle_tx_hash: HexBytes


class UnknownContent(NbeContent):
    """Fallback for mantle ops this explorer doesn't have a typed model for.

    Preserves the opcode and raw payload verbatim so new node op types never
    break block ingestion; typed support can be added later.
    """

    type: Literal["Unknown"] = "Unknown"
    opcode: int
    payload: Optional[Any] = None


OperationContent = (
    LedgerTransfer
    | ChannelInscribe
    | ChannelBlob
    | ChannelSetKeys
    | SDPDeclare
    | SDPWithdraw
    | SDPActive
    | LeaderClaim
    | UnknownContent
)
