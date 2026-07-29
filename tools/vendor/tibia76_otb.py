from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

NODE_START = 0xFE
NODE_END = 0xFF
ESCAPE = 0xFD
FLUID_SUBTYPE_SERVER_ID_BASE = 20000

# Some OTBs start with OTBI, some older 7.x files begin with four zero bytes before the root node.
OTBI_IDENTIFIER = b'OTBI'

ITEM_GROUP_NAMES = {
    0: 'none',
    1: 'ground',
    2: 'container',
    3: 'weapon',
    4: 'ammunition',
    5: 'armor',
    6: 'charges',
    7: 'teleport',
    8: 'magicfield',
    9: 'writeable',
    10: 'key',
    11: 'splash',
    12: 'fluid',
    13: 'door',
    14: 'deprecated',
}
GROUP_NAME_TO_CODE = {v: k for k, v in ITEM_GROUP_NAMES.items()}

ITEM_ATTR_NAMES = {
    0x10: 'server_id',
    0x11: 'client_id',
    0x12: 'name',
    0x13: 'description',
    0x14: 'speed',
    0x15: 'slot',
    0x16: 'max_items',
    0x17: 'weight',
    0x18: 'weapon',
    0x19: 'amu',
    0x1A: 'armor',
    0x1B: 'magic_level',
    0x1C: 'magic_field_type',
    0x1D: 'writeable',
    0x1E: 'rotate_to',
    0x1F: 'decay',
    0x20: 'sprite_hash',
    0x21: 'minimap_color',
    0x22: 'attr_07',
    0x23: 'attr_08',
    0x24: 'light',
    0x25: 'decay2',
    0x26: 'weapon2',
    0x27: 'amu2',
    0x28: 'armor2',
    0x29: 'writeable2',
    0x2A: 'light2',
    0x2B: 'top_order',
    0x2C: 'writeable3',
}
ATTR_NAME_TO_CODE = {v: k for k, v in ITEM_ATTR_NAMES.items()}

ITEM_FLAG_BITS = {
    'block_solid': 1,
    'block_projectile': 2,
    'block_pathfind': 4,
    'has_height': 8,
    'useable': 16,
    'pickupable': 32,
    'moveable': 64,
    'stackable': 128,
    'floor_change_down': 256,
    'floor_change_north': 512,
    'floor_change_east': 1024,
    'floor_change_south': 2048,
    'floor_change_west': 4096,
    'always_on_top': 8192,
    'readable': 16384,
    'rotable': 32768,
    'hangable': 65536,
    'vertical': 131072,
    'horizontal': 262144,
    'cannot_decay': 524288,
    'allow_dist_read': 1048576,
    'unused': 2097152,
    'client_charges': 4194304,
    'look_through': 8388608,
}

OTB_PRESET_COPY_FIELDS = (
    'group',
    'speed',
    'minimap_color',
    'light_level',
    'light_color',
    'top_order',
)


@dataclass
class OTBNode:
    node_type: int
    props: bytes = b''
    children: List['OTBNode'] = field(default_factory=list)


@dataclass
class OTBAttribute:
    code: int
    data: bytes


@dataclass
class OTBItem:
    group: int
    raw_flags: int
    attributes: List[OTBAttribute] = field(default_factory=list)
    server_id: int = 0
    client_id: int = 0
    name: str = ''
    speed: Optional[int] = None
    minimap_color: Optional[int] = None
    light_level: Optional[int] = None
    light_color: Optional[int] = None
    top_order: Optional[int] = None
    unknown_attributes: List[OTBAttribute] = field(default_factory=list)

    def flag_enabled(self, name: str) -> bool:
        bit = ITEM_FLAG_BITS[name]
        return (self.raw_flags & bit) != 0

    def set_flag(self, name: str, enabled: bool) -> None:
        bit = ITEM_FLAG_BITS[name]
        if enabled:
            self.raw_flags |= bit
        else:
            self.raw_flags &= ~bit

    def group_name(self) -> str:
        return ITEM_GROUP_NAMES.get(self.group, f'group_{self.group}')

    def build_preset_payload(self) -> Dict[str, object]:
        return {
            'group': self.group_name(),
            'fields': {field_name: getattr(self, field_name) for field_name in OTB_PRESET_COPY_FIELDS if field_name != 'group'},
            'flags': {name: self.flag_enabled(name) for name in ITEM_FLAG_BITS},
        }

    def apply_preset_payload(self, payload: Dict[str, object]) -> None:
        group_name = str(payload.get('group', self.group_name()) or 'none')
        self.group = GROUP_NAME_TO_CODE.get(group_name, self.group)

        raw_fields = payload.get('fields', {})
        if not isinstance(raw_fields, dict):
            raw_fields = {}
        for field_name in OTB_PRESET_COPY_FIELDS:
            if field_name == 'group':
                continue
            setattr(self, field_name, raw_fields.get(field_name))

        raw_flags = payload.get('flags', {})
        if not isinstance(raw_flags, dict):
            raw_flags = {}
        for name in ITEM_FLAG_BITS:
            self.set_flag(name, bool(raw_flags.get(name, False)))

    @classmethod
    def from_node(cls, node: OTBNode) -> 'OTBItem':
        if len(node.props) < 4:
            raise ValueError('OTB item node props too short for flags')
        raw_flags = struct.unpack_from('<I', node.props, 0)[0]
        item = cls(group=node.node_type, raw_flags=raw_flags)
        pos = 4
        while pos < len(node.props):
            if pos + 3 > len(node.props):
                raise ValueError('OTB attribute header truncated')
            code = node.props[pos]
            length = struct.unpack_from('<H', node.props, pos + 1)[0]
            pos += 3
            end = pos + length
            if end > len(node.props):
                raise ValueError('OTB attribute data truncated')
            data = node.props[pos:end]
            pos = end
            attr = OTBAttribute(code=code, data=data)
            item.attributes.append(attr)

            if code == 0x10 and len(data) == 2:
                item.server_id = struct.unpack('<H', data)[0]
            elif code == 0x11 and len(data) == 2:
                item.client_id = struct.unpack('<H', data)[0]
            elif code == 0x12:
                item.name = data.decode('latin-1', errors='ignore')
            elif code == 0x14 and len(data) == 2:
                item.speed = struct.unpack('<H', data)[0]
            elif code == 0x21 and len(data) == 2:
                item.minimap_color = struct.unpack('<H', data)[0]
            elif code in (0x24, 0x2A) and len(data) == 4:
                item.light_level, item.light_color = struct.unpack('<HH', data)
            elif code == 0x2B and len(data) == 1:
                item.top_order = data[0]
            else:
                item.unknown_attributes.append(attr)
        return item

    def rebuild_attributes(self) -> List[OTBAttribute]:
        desired: Dict[int, Optional[bytes]] = {
            0x10: struct.pack('<H', int(self.server_id) & 0xFFFF),
            0x11: struct.pack('<H', int(self.client_id) & 0xFFFF),
            0x12: self.name.encode('latin-1', errors='replace') if self.name else None,
            0x14: struct.pack('<H', int(self.speed) & 0xFFFF) if self.speed is not None else None,
            0x21: struct.pack('<H', int(self.minimap_color) & 0xFFFF) if self.minimap_color is not None else None,
            0x2B: bytes((int(self.top_order) & 0xFF,)) if self.top_order is not None else None,
        }
        if self.light_level is not None or self.light_color is not None:
            light_payload = struct.pack('<HH', int(self.light_level or 0) & 0xFFFF, int(self.light_color or 0) & 0xFFFF)
            # Keep whichever light attribute the original file used.
            existing_codes = {attr.code for attr in self.attributes}
            if 0x24 in existing_codes and 0x2A not in existing_codes:
                desired[0x24] = light_payload
                desired[0x2A] = None
            else:
                desired[0x2A] = light_payload
                desired[0x24] = None
        else:
            desired[0x24] = None
            desired[0x2A] = None

        out: List[OTBAttribute] = []
        seen = set()
        for attr in self.attributes:
            if attr.code in desired:
                payload = desired[attr.code]
                seen.add(attr.code)
                if payload is not None:
                    out.append(OTBAttribute(attr.code, payload))
            else:
                out.append(attr)

        for code in (0x10, 0x11, 0x12, 0x14, 0x21, 0x24, 0x2A, 0x2B):
            if code not in seen:
                payload = desired.get(code)
                if payload is not None:
                    out.append(OTBAttribute(code, payload))

        self.attributes = list(out)
        self.unknown_attributes = [attr for attr in out if attr.code not in {0x10, 0x11, 0x12, 0x14, 0x21, 0x24, 0x2A, 0x2B}]
        return out

    def to_node(self) -> OTBNode:
        props = bytearray(struct.pack('<I', self.raw_flags))
        for attr in self.rebuild_attributes():
            props.append(attr.code & 0xFF)
            props += struct.pack('<H', len(attr.data))
            props += attr.data
        return OTBNode(node_type=self.group, props=bytes(props), children=[])


class Tibia76Otb:
    def __init__(self) -> None:
        self.file_prefix: bytes = b''
        self.root_type: int = 0
        self.root_flags: int = 0
        self.major_version: int = 0xFFFFFFFF
        self.minor_version: int = 0
        self.build_version: int = 0
        self.csd_version: bytes = b'\x00' * 128
        self.root_unknown_attributes: List[OTBAttribute] = []
        self.items: List[OTBItem] = []
        self.suffix: bytes = b''
        self._items_by_server_id: Dict[int, OTBItem] = {}
        self._items_by_client_id: Dict[int, OTBItem] = {}

    @staticmethod
    def _escape_bytes(data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            if b in (NODE_START, NODE_END, ESCAPE):
                out.append(ESCAPE)
            out.append(b)
        return bytes(out)

    @classmethod
    def _parse_node(cls, data: bytes, pos: int) -> Tuple[OTBNode, int]:
        if pos >= len(data) or data[pos] != NODE_START:
            raise ValueError('Expected OTB node start marker')
        pos += 1
        if pos >= len(data):
            raise ValueError('Unexpected EOF after OTB node start')
        if data[pos] == ESCAPE:
            pos += 1
            if pos >= len(data):
                raise ValueError('Unexpected EOF after OTB escape')
        node_type = data[pos]
        pos += 1
        props = bytearray()
        children: List[OTBNode] = []
        while pos < len(data):
            b = data[pos]
            if b == NODE_END:
                pos += 1
                return OTBNode(node_type=node_type, props=bytes(props), children=children), pos
            if b == NODE_START:
                child, pos = cls._parse_node(data, pos)
                children.append(child)
                continue
            if b == ESCAPE:
                pos += 1
                if pos >= len(data):
                    raise ValueError('Unexpected EOF after OTB escape in props')
                props.append(data[pos])
                pos += 1
                continue
            props.append(b)
            pos += 1
        raise ValueError('OTB node not terminated')

    @classmethod
    def _encode_node(cls, node: OTBNode) -> bytes:
        out = bytearray([NODE_START])
        out += cls._escape_bytes(bytes((node.node_type & 0xFF,)))
        out += cls._escape_bytes(node.props)
        for child in node.children:
            out += cls._encode_node(child)
        out.append(NODE_END)
        return bytes(out)
        
    def is_fluid_subtype_entry(self, item: OTBItem) -> bool:
        return ( item.server_id > FLUID_SUBTYPE_SERVER_ID_BASE and item.client_id == 0 and item.group == GROUP_NAME_TO_CODE['none'])

    @classmethod
    def load(cls, path: str | Path) -> 'Tibia76Otb':
        blob = Path(path).read_bytes()
        first_node = blob.find(bytes((NODE_START,)))
        if first_node == -1:
            raise ValueError('Could not find OTB root node')

        obj = cls()
        obj.file_prefix = blob[:first_node]
        root, pos = cls._parse_node(blob, first_node)
        obj.suffix = blob[pos:]
        obj.root_type = root.node_type
        if len(root.props) >= 4:
            obj.root_flags = struct.unpack_from('<I', root.props, 0)[0]
        p = 4
        while p < len(root.props):
            if p + 3 > len(root.props):
                raise ValueError('Root OTB attribute header truncated')
            code = root.props[p]
            length = struct.unpack_from('<H', root.props, p + 1)[0]
            p += 3
            end = p + length
            if end > len(root.props):
                raise ValueError('Root OTB attribute data truncated')
            data = root.props[p:end]
            p = end
            attr = OTBAttribute(code=code, data=data)
            if code == 0x01 and len(data) == 140:
                obj.major_version, obj.minor_version, obj.build_version = struct.unpack_from('<III', data, 0)
                obj.csd_version = data[12:140]
            else:
                obj.root_unknown_attributes.append(attr)

        obj.items = [OTBItem.from_node(child) for child in root.children]
        obj.rebuild_indices()
        return obj

    def _root_props(self) -> bytes:
        props = bytearray(struct.pack('<I', self.root_flags))
        version_data = struct.pack(
            '<III',
            int(self.major_version) & 0xFFFFFFFF,
            int(self.minor_version) & 0xFFFFFFFF,
            int(self.build_version) & 0xFFFFFFFF,
        )
        csd = (self.csd_version or b'')[:128].ljust(128, b'\x00')
        props.append(0x01)
        props += struct.pack('<H', 140)
        props += version_data + csd
        for attr in self.root_unknown_attributes:
            if attr.code == 0x01:
                continue
            props.append(attr.code & 0xFF)
            props += struct.pack('<H', len(attr.data))
            props += attr.data
        return bytes(props)

    def save(self, path: str | Path) -> None:
        root = OTBNode(node_type=self.root_type, props=self._root_props(), children=[item.to_node() for item in self.items])
        blob = self.file_prefix + self._encode_node(root) + self.suffix
        Path(path).write_bytes(blob)

    def rebuild_indices(self) -> None:
        self._items_by_server_id = {}
        self._items_by_client_id = {}
        for item in self.items:
            self._items_by_server_id[int(item.server_id)] = item
            self._items_by_client_id[int(item.client_id)] = item

    def sort_items(self) -> None:
        self.items.sort(key=lambda item: (item.server_id, item.client_id, item.group, item.name.lower()))
        self.rebuild_indices()

    def next_server_id(self) -> int:
        real_item_ids = [
            item.server_id
            for item in self.items
            if not self.is_fluid_subtype_entry(item)
        ]
        return max(real_item_ids, default=99) + 1

    def next_client_id(self) -> int:
        real_client_ids = [int(item.client_id) for item in self.items if int(item.client_id) >= 100]
        return max(real_client_ids, default=99) + 1

    def valid_item_client_id_set(self, dat_item_max_id: int) -> set[int]:
        return set(range(100, max(99, int(dat_item_max_id)) + 1))

    def clear_invalid_client_ids(self, dat_item_max_id: int) -> List[OTBItem]:
        """Clear OTB items whose client_id points outside the DAT item range."""
        invalid: List[OTBItem] = []
        max_id = int(dat_item_max_id)
        for item in self.items:
            cid = int(item.client_id)
            if cid >= 100 and cid > max_id:
                invalid.append(item)
                item.client_id = 0
        if invalid:
            self.rebuild_indices()
        return invalid

    def find_missing_client_ids(self, dat_item_max_id: int) -> List[int]:
        existing = {int(item.client_id) for item in self.items if int(item.client_id) >= 100}
        return [cid for cid in range(100, int(dat_item_max_id) + 1) if cid not in existing]

    def sync_with_dat_item_range(
        self,
        dat_item_max_id: int,
        *,
        deleted_client_ids: Optional[Iterable[int]] = None,
        clear_deleted: bool = True,
        clear_out_of_range: bool = True,
    ) -> Tuple[List[OTBItem], List[OTBItem]]:
        """Best-effort sync against the current DAT item range.

        Returns (broken_from_compact_delete, cleared_out_of_range).
        """
        broken: List[OTBItem] = []
        if deleted_client_ids:
            broken = self.remap_client_ids_after_dat_delete(list(deleted_client_ids), clear_deleted=clear_deleted)
        cleared: List[OTBItem] = []
        if clear_out_of_range:
            cleared = self.clear_invalid_client_ids(int(dat_item_max_id))
        self.sort_items()
        return broken, cleared



    def remap_client_ids_after_dat_delete(self, deleted_client_ids: List[int], *, clear_deleted: bool = True) -> List[OTBItem]:
        """
        Shift OTB client_id values after compacting Tibia.dat by deleting one or more
        item IDs from the middle.

        Example:
            deleted_client_ids=[5105, 5106]
            5107 -> 5105
            5108 -> 5106

        Items pointing exactly at removed DAT ids are returned. When clear_deleted=True
        they are additionally cleared to client_id=0 so they do not point at a wrong item.
        """
        import bisect

        deleted = sorted({int(x) for x in deleted_client_ids if int(x) >= 100})
        if not deleted:
            return []

        deleted_set = set(deleted)
        broken: List[OTBItem] = []

        for item in self.items:
            cid = int(item.client_id)
            if cid < 100:
                continue
            if cid in deleted_set:
                broken.append(item)
                if clear_deleted:
                    item.client_id = 0
                continue
            shift = bisect.bisect_left(deleted, cid)
            if shift > 0:
                item.client_id = cid - shift

        self.rebuild_indices()
        return broken

    def append_placeholder_item(self, client_id: int, *, server_id: Optional[int] = None, name: str = '') -> OTBItem:
        if server_id is None:
            server_id = self.next_server_id()
        item = OTBItem(group=GROUP_NAME_TO_CODE['none'], raw_flags=0, server_id=int(server_id), client_id=int(client_id), name=name)
        self.items.append(item)
        self.sort_items()
        return item

    def ensure_client_ids_exist(self, client_ids: Iterable[int], *, name_prefix: str = 'auto_item') -> List[OTBItem]:
        existing = {int(item.client_id) for item in self.items}
        created: List[OTBItem] = []
        for client_id in sorted({int(x) for x in client_ids if int(x) >= 100}):
            if client_id in existing:
                continue
            created.append(self.append_placeholder_item(client_id, name=f'{name_prefix}_{client_id}'))
            existing.add(client_id)
        return created

    def find_by_server_id(self, server_id: int) -> Optional[OTBItem]:
        return self._items_by_server_id.get(int(server_id))

    def find_by_client_id(self, client_id: int) -> Optional[OTBItem]:
        return self._items_by_client_id.get(int(client_id))
