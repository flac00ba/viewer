"""Small, strict OTBM reader for the data needed by the web map viewer."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

NODE_START = 0xFE
NODE_END = 0xFF
NODE_ESCAPE = 0xFD

NODE_ROOT_LEGACY = 0
NODE_ROOT_V1 = 1
NODE_MAP_DATA = 2
NODE_TILE_AREA = 4
NODE_TILE = 5
NODE_ITEM = 6
NODE_HOUSE_TILE = 14

ATTR_DESCRIPTION = 1
ATTR_EXT_FILE = 2
ATTR_TILE_FLAGS = 3
ATTR_ACTION_ID = 4
ATTR_UNIQUE_ID = 5
ATTR_TEXT = 6
ATTR_DESC = 7
ATTR_TELE_DEST = 8
ATTR_ITEM = 9
ATTR_DEPOT_ID = 10
ATTR_EXT_SPAWN_FILE = 11
ATTR_RUNE_CHARGES = 12
ATTR_EXT_HOUSE_FILE = 13
ATTR_HOUSE_DOOR_ID = 14
ATTR_COUNT = 15
ATTR_DURATION = 16
ATTR_DECAYING_STATE = 17
ATTR_WRITTEN_DATE = 18
ATTR_WRITTEN_BY = 19
ATTR_SLEEPER_GUID = 20
ATTR_SLEEP_START = 21
ATTR_CHARGES = 22
ATTR_REGION_ID = 23
ATTR_ATTRIBUTE_MAP = 128


@dataclass(slots=True)
class Node:
    type: int
    payload: bytes
    children: list["Node"] = field(default_factory=list)


@dataclass(slots=True)
class Item:
    server_id: int
    count: int = 1


@dataclass(slots=True)
class Tile:
    x: int
    y: int
    z: int
    flags: int = 0
    house_id: int = 0
    region_id: int = 0
    items: list[Item] = field(default_factory=list)


@dataclass(slots=True)
class Map:
    version: int
    width: int
    height: int
    item_major_version: int
    item_minor_version: int
    description: str = ""
    spawn_file: str = ""
    house_file: str = ""
    tiles: list[Tile] = field(default_factory=list)


class Reader:
    __slots__ = ("data", "offset")

    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.offset

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise ValueError(f"OTBM payload ended at byte {self.offset}, needed {size} more bytes")
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def string(self) -> str:
        return self.take(self.u16()).decode("utf-8", errors="replace")

    def long_string(self) -> str:
        return self.take(self.u32()).decode("utf-8", errors="replace")


def _parse_node(data: bytes, offset: int) -> tuple[Node, int]:
    if offset >= len(data) or data[offset] != NODE_START:
        raise ValueError(f"Expected OTBM node marker at byte {offset}")
    offset += 1
    raw = bytearray()
    children: list[Node] = []

    while offset < len(data):
        marker = data[offset]
        if marker == NODE_END:
            offset += 1
            if not raw:
                raise ValueError("OTBM node has no type")
            return Node(raw[0], bytes(raw[1:]), children), offset
        if marker == NODE_START:
            child, offset = _parse_node(data, offset)
            children.append(child)
            continue
        if marker == NODE_ESCAPE:
            offset += 1
            if offset >= len(data):
                raise ValueError("OTBM escape marker at end of file")
            raw.append(data[offset])
            offset += 1
            continue
        raw.append(marker)
        offset += 1

    raise ValueError("Unterminated OTBM node")


def _strip_magic(data: bytes) -> bytes:
    if data[:4] in (b"OTBM", b"\x00\x00\x00\x00"):
        return data[4:]
    return data


def _skip_attribute_map(reader: Reader) -> None:
    for _ in range(reader.u16()):
        reader.string()
        value_type = reader.u8()
        if value_type == 1:
            reader.long_string()
        elif value_type in (2, 3):
            reader.take(4)
        elif value_type == 4:
            reader.take(1)
        elif value_type == 5:
            reader.take(8)


def _read_item_count(reader: Reader) -> int:
    count = 1
    while reader.remaining:
        attr = reader.u8()
        if attr in (ATTR_COUNT, ATTR_RUNE_CHARGES):
            count = max(1, reader.u8())
        elif attr == ATTR_CHARGES:
            count = max(1, reader.u16())
        elif attr in (ATTR_ACTION_ID, ATTR_UNIQUE_ID, ATTR_DEPOT_ID):
            reader.take(2)
        elif attr in (ATTR_TEXT, ATTR_DESC, ATTR_WRITTEN_BY, ATTR_DESCRIPTION):
            reader.string()
        elif attr == ATTR_TELE_DEST:
            reader.take(5)
        elif attr in (ATTR_HOUSE_DOOR_ID, ATTR_DECAYING_STATE):
            reader.take(1)
        elif attr in (ATTR_DURATION, ATTR_WRITTEN_DATE, ATTR_SLEEPER_GUID, ATTR_SLEEP_START):
            reader.take(4)
        elif attr == ATTR_ATTRIBUTE_MAP:
            _skip_attribute_map(reader)
        else:
            raise ValueError(f"Unsupported OTBM item attribute {attr}")
    return count


def _item_from_node(node: Node, map_version: int, countable_ids: set[int]) -> Item:
    if node.type != NODE_ITEM:
        raise ValueError(f"Expected item node, received node type {node.type}")
    reader = Reader(node.payload)
    server_id = reader.u16()
    count = reader.u8() if map_version == 0 and server_id in countable_ids and reader.remaining else 1
    if reader.remaining:
        count = _read_item_count(reader)
    return Item(server_id, max(1, count))


def _parse_tile(node: Node, base_x: int, base_y: int, base_z: int, map_version: int, countable_ids: set[int]) -> Tile:
    reader = Reader(node.payload)
    x = base_x + reader.u8()
    y = base_y + reader.u8()
    house_id = reader.u32() if node.type == NODE_HOUSE_TILE else 0
    tile = Tile(x=x, y=y, z=base_z, house_id=house_id)

    while reader.remaining:
        attr = reader.u8()
        if attr == ATTR_TILE_FLAGS:
            tile.flags = reader.u32()
        elif attr == ATTR_REGION_ID:
            tile.region_id = reader.u32()
        elif attr == ATTR_ITEM:
            server_id = reader.u16()
            count = reader.u8() if map_version == 0 and server_id in countable_ids and reader.remaining else 1
            tile.items.append(Item(server_id, max(1, count)))
        else:
            raise ValueError(f"Unsupported tile attribute {attr} at {x}:{y}:{base_z}")

    tile.items.extend(_item_from_node(child, map_version, countable_ids) for child in node.children if child.type == NODE_ITEM)
    return tile


def _parse_map_attributes(node: Node, result: Map) -> None:
    reader = Reader(node.payload)
    while reader.remaining:
        attr = reader.u8()
        if attr == ATTR_DESCRIPTION:
            result.description = reader.string()
        elif attr == ATTR_EXT_SPAWN_FILE:
            result.spawn_file = reader.string()
        elif attr == ATTR_EXT_HOUSE_FILE:
            result.house_file = reader.string()
        elif attr == ATTR_EXT_FILE:
            reader.string()
        else:
            raise ValueError(f"Unsupported OTBM map attribute {attr}")


def load(path: str | Path, *, countable_ids: Iterable[int] = ()) -> Map:
    data = _strip_magic(Path(path).read_bytes())
    root, end = _parse_node(data, 0)
    if root.type not in (NODE_ROOT_LEGACY, NODE_ROOT_V1):
        raise ValueError(f"Expected OTBM root node, received node type {root.type}")
    if end != len(data):
        trailing = data[end:]
        if any(trailing):
            raise ValueError(f"OTBM contains {len(trailing)} unexpected trailing bytes")

    header = Reader(root.payload)
    result = Map(
        version=header.u32(),
        width=header.u16(),
        height=header.u16(),
        item_major_version=header.u32(),
        item_minor_version=header.u32(),
    )
    map_data = next((child for child in root.children if child.type == NODE_MAP_DATA), None)
    if map_data is None:
        raise ValueError("OTBM has no map-data node")
    _parse_map_attributes(map_data, result)

    countable = set(countable_ids)
    for area in (child for child in map_data.children if child.type == NODE_TILE_AREA):
        reader = Reader(area.payload)
        base_x, base_y, base_z = reader.u16(), reader.u16(), reader.u8()
        for tile_node in area.children:
            if tile_node.type in (NODE_TILE, NODE_HOUSE_TILE):
                result.tiles.append(_parse_tile(tile_node, base_x, base_y, base_z, result.version, countable))

    return result
