"""Binary map chunk format shared conceptually with docs/data.js."""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass, field

MAGIC = b"YMC1"
VERSION = 1
ENTRY_ITEM = 0
ENTRY_CREATURE = 1


@dataclass(slots=True)
class ChunkEntry:
    kind: int
    identifier: int
    value: int = 1


@dataclass(slots=True)
class ChunkTile:
    local_x: int
    local_y: int
    flags: int = 0
    house_id: int = 0
    entries: list[ChunkEntry] = field(default_factory=list)


def encode(chunk_size: int, tiles: list[ChunkTile]) -> bytes:
    if not 1 <= chunk_size <= 255:
        raise ValueError("chunk_size must fit uint8")
    if len(tiles) > 0xFFFF:
        raise ValueError("too many tiles in one chunk")

    output = bytearray(MAGIC)
    output += struct.pack("<BBH", VERSION, chunk_size, len(tiles))
    for tile in tiles:
        if not 0 <= tile.local_x < chunk_size or not 0 <= tile.local_y < chunk_size:
            raise ValueError("tile lies outside its chunk")
        if len(tile.entries) > 0xFFFF:
            raise ValueError("too many entries on one tile")
        output += struct.pack("<BBIIH", tile.local_x, tile.local_y, tile.flags, tile.house_id, len(tile.entries))
        for entry in tile.entries:
            if entry.kind not in (ENTRY_ITEM, ENTRY_CREATURE):
                raise ValueError(f"unknown entry kind {entry.kind}")
            output += struct.pack("<BHH", entry.kind, entry.identifier, entry.value)
    return bytes(output)


def encode_gzip(chunk_size: int, tiles: list[ChunkTile]) -> bytes:
    return gzip.compress(encode(chunk_size, tiles), compresslevel=9, mtime=0)
