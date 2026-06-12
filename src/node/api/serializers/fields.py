from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer, ValidationError


def bytes_from_intarray(data: list[int]) -> bytes:
    if not isinstance(data, list):
        raise ValueError(f"Unsupported data type for bytes deserialization. Expected list, got {type(data).__name__}.")
    elif not all(isinstance(item, int) for item in data):
        raise ValueError("List items must be integers.")
    else:
        return bytes(data)


def bytes_from_hex(data: str) -> bytes:
    if not isinstance(data, str):
        raise ValueError(
            f"Unsupported data type for bytes deserialization. Expected string, got {type(data).__name__}."
        )
    return bytes.fromhex(data)


def bytes_from_int(data: int) -> bytes:
    if not isinstance(data, int):
        raise ValueError(
            f"Unsupported data type for bytes deserialization. Expected integer, got {type(data).__name__}."
        )
    return data.to_bytes((data.bit_length() + 7) // 8)  # TODO: Ensure endianness is correct.


def bytes_from_hex_or_intarray(data: str | list[int]) -> bytes:
    """Node versions drifted between int arrays and hex strings for byte fields.

    Older nodes (<= 0.1.2) serialize bytes as int arrays; newer nodes serialize
    them as hex strings. Accept both so the explorer works across node versions.
    """
    if isinstance(data, str):
        return bytes_from_hex(data)
    return bytes_from_intarray(data)


def bytes_into_hex(data: bytes) -> str:
    return data.hex()


BytesFromIntArray = Annotated[bytes, BeforeValidator(bytes_from_intarray), PlainSerializer(bytes_into_hex)]
BytesFromHex = Annotated[bytes, BeforeValidator(bytes_from_hex), PlainSerializer(bytes_into_hex)]
BytesFromInt = Annotated[bytes, BeforeValidator(bytes_from_int), PlainSerializer(bytes_into_hex)]
BytesFromHexOrIntArray = Annotated[
    bytes, BeforeValidator(bytes_from_hex_or_intarray), PlainSerializer(bytes_into_hex)
]
