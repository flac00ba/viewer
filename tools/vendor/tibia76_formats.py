from __future__ import annotations

import struct
from enum import IntEnum
from typing import BinaryIO, Union

U32_MAX = 0xFFFFFFFF
U16_MAX = 0xFFFF


class SpriteIdSize(IntEnum):
    U16 = 16
    U32 = 32

    @classmethod
    def coerce(cls, value: Union['SpriteIdSize', int, str, None], *, default: 'SpriteIdSize' = None) -> 'SpriteIdSize':
        if default is None:
            default = cls.U16
        if value is None:
            return default
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            if value == 16:
                return cls.U16
            if value == 32:
                return cls.U32
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {'16', 'u16', 'uint16', 'uint16_t', 'legacy'}:
                return cls.U16
            if text in {'32', 'u32', 'uint32', 'uint32_t', 'extended', 'gamespritesu32'}:
                return cls.U32
        raise ValueError(f'Unsupported sprite id size: {value!r}')

    def label(self) -> str:
        return 'U32' if self == SpriteIdSize.U32 else 'U16'

    @property
    def byte_size(self) -> int:
        return 4 if self == SpriteIdSize.U32 else 2


# In SPR this controls only the sprite COUNT field. Offsets stay uint32.
SprCountFormat = SpriteIdSize

# In DAT this controls sprite references inside each thing record.
DatSpriteIdFormat = SpriteIdSize


def _read_exact(reader: BinaryIO, size: int, what: str) -> bytes:
    data = reader.read(size)
    if len(data) != size:
        raise ValueError(f'Unexpected EOF while reading {what}')
    return data


def read_sprite_count(reader: BinaryIO, spr_count_format: Union[SprCountFormat, int, str]) -> int:
    fmt = SprCountFormat.coerce(spr_count_format)
    if fmt == SprCountFormat.U32:
        return struct.unpack('<I', _read_exact(reader, 4, 'SPR sprite count U32'))[0]
    return struct.unpack('<H', _read_exact(reader, 2, 'SPR sprite count U16'))[0]


def write_sprite_count(out: bytearray, count: int, spr_count_format: Union[SprCountFormat, int, str]) -> None:
    fmt = SprCountFormat.coerce(spr_count_format)
    count = int(count)
    if count < 0:
        raise ValueError('SPR sprite count cannot be negative')
    if fmt == SprCountFormat.U16:
        if count > U16_MAX:
            raise ValueError(f'SPR sprite count {count} exceeds U16 limit {U16_MAX}; save as U32.')
        out += struct.pack('<H', count)
    else:
        if count > U32_MAX:
            raise ValueError(f'SPR sprite count {count} exceeds U32 limit {U32_MAX}')
        out += struct.pack('<I', count)


def read_sprite_id(reader: BinaryIO, dat_sprite_id_format: Union[DatSpriteIdFormat, int, str]) -> int:
    fmt = DatSpriteIdFormat.coerce(dat_sprite_id_format)
    if fmt == DatSpriteIdFormat.U32:
        return struct.unpack('<I', _read_exact(reader, 4, 'DAT sprite id U32'))[0]
    return struct.unpack('<H', _read_exact(reader, 2, 'DAT sprite id U16'))[0]


def write_sprite_id(out: bytearray, value: int, dat_sprite_id_format: Union[DatSpriteIdFormat, int, str]) -> None:
    fmt = DatSpriteIdFormat.coerce(dat_sprite_id_format)
    value = int(value)
    if value < 0:
        raise ValueError(f'DAT sprite id cannot be negative: {value}')
    if fmt == DatSpriteIdFormat.U16:
        if value > U16_MAX:
            raise ValueError(f'DAT sprite id {value} exceeds U16 limit {U16_MAX}; save DAT as U32.')
        out += struct.pack('<H', value)
    else:
        if value > U32_MAX:
            raise ValueError(f'DAT sprite id {value} exceeds U32 limit {U32_MAX}')
        out += struct.pack('<I', value)


def pack_sprite_ids(values, dat_sprite_id_format: Union[DatSpriteIdFormat, int, str]) -> bytes:
    out = bytearray()
    for value in values:
        write_sprite_id(out, int(value), dat_sprite_id_format)
    return bytes(out)


def format_label(value: Union[SpriteIdSize, int, str, None]) -> str:
    return SpriteIdSize.coerce(value).label()
