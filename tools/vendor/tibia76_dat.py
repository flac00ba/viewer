from __future__ import annotations

import copy
import io
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union

from PIL import Image

from editor_shared import transactional_write_bytes
from tibia76_formats import DatSpriteIdFormat, U16_MAX, pack_sprite_ids
from tibia76_spr import SPRITE_SIZE, Tibia76Spr

MIN_ITEM_ID = 100
MIN_OUTFIT_ID = 1
MIN_EFFECT_ID = 1
MIN_DISTANCE_ID = 1

# OpenTibia/OTClient default fallback timings used when GameEnhancedAnimations is enabled
# but a legacy DAT record did not carry explicit animator data. Effects in classic OTClient
# use 75 ms per frame; using a generic 250 ms fallback makes magic effects visibly too slow.
DEFAULT_ENHANCED_FRAME_DURATION_MS = 250
DEFAULT_ITEM_ENHANCED_FRAME_DURATION_MS = 500
DEFAULT_CREATURE_ENHANCED_FRAME_DURATION_MS = 250
DEFAULT_EFFECT_ENHANCED_FRAME_DURATION_MS = 75
DEFAULT_DISTANCE_ENHANCED_FRAME_DURATION_MS = 75


def default_enhanced_frame_duration_ms(kind: str) -> int:
    kind = str(kind or '').lower()
    if kind == 'effect':
        return DEFAULT_EFFECT_ENHANCED_FRAME_DURATION_MS
    if kind == 'distance':
        return DEFAULT_DISTANCE_ENHANCED_FRAME_DURATION_MS
    if kind == 'item':
        return DEFAULT_ITEM_ENHANCED_FRAME_DURATION_MS
    if kind == 'outfit':
        return DEFAULT_CREATURE_ENHANCED_FRAME_DURATION_MS
    return DEFAULT_ENHANCED_FRAME_DURATION_MS

# ObjectBuilder 7.55-7.72 model (MetadataFlags3) with one reserved boolean kept as unknown_17
# because real-world 7.6 files sometimes use that slot. Payload sizes match the classic 16-bit format.
DAT_FLAG_INFO: Dict[int, Tuple[str, int]] = {
    0x00: ('ground', 2),
    0x01: ('ground_border', 0),
    0x02: ('on_bottom', 0),
    0x03: ('on_top', 0),
    0x04: ('container', 0),
    0x05: ('stackable', 0),
    0x06: ('multi_use', 0),
    0x07: ('force_use', 0),
    0x08: ('writable', 2),
    0x09: ('writable_once', 2),
    0x0A: ('fluid_container', 0),
    0x0B: ('fluid', 0),
    0x0C: ('unpassable', 0),
    0x0D: ('unmoveable', 0),
    0x0E: ('block_missile', 0),
    0x0F: ('block_pathfind', 0),
    0x10: ('pickupable', 0),
    0x11: ('hangable', 0),
    0x12: ('vertical', 0),
    0x13: ('horizontal', 0),
    0x14: ('rotatable', 0),
    0x15: ('has_light', 4),
    0x16: ('floor_change', 0),
    0x17: ('unknown_17', 0),
    0x18: ('has_offset', 4),
    0x19: ('has_elevation', 2),
    0x1A: ('lying_object', 0),
    0x1B: ('animate_always', 0),
    0x1C: ('mini_map', 2),
    0x1D: ('lens_help', 2),
    0x1E: ('full_ground', 0),
}

FLAG_NAME_TO_CODE = {name: code for code, (name, _size) in DAT_FLAG_INFO.items()}
BOOL_FLAG_ORDER = [name for _code, (name, size) in sorted(DAT_FLAG_INFO.items()) if size == 0]
COMMON_BOOL_FLAGS = list(BOOL_FLAG_ORDER)

DAT_PRESET_VISUAL_FIELDS = (
    'width',
    'height',
    'exact_size',
    'blend_frames',
    'xdiv',
    'ydiv',
    'zdiv',
    'animation_length',
    'sprite_ids',
    'animation_mode',
    'frame_groups',
)
DAT_PRESET_COPY_FIELDS = (
    'ground_speed',
    'max_text_length',
    'light_level',
    'light_color',
    'offset_x',
    'offset_y',
    'elevation',
    'mini_map_color',
    'lens_help_value',
)


class DatAnimationMode:
    LEGACY_SINGLE_GROUP = 'legacy_single_group'
    FRAME_GROUPS = 'frame_groups'


class FrameGroupType(IntEnum):
    # Matches OTClient/OpenTibia frame groups: Idle is the default group, Moving is the walking group.
    IDLE = 0
    MOVING = 1
    DEFAULT = 0

    @classmethod
    def coerce(cls, value: Union['FrameGroupType', int, str, None], *, default: 'FrameGroupType' = None) -> 'FrameGroupType':
        if default is None:
            default = cls.DEFAULT
        if value is None:
            return default
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError:
                return cls.DEFAULT
        text = str(value).strip().lower()
        if text in {'idle', 'standing', 'stand', 'default', '0'}:
            return cls.IDLE
        if text in {'moving', 'walking', 'walk', '1'}:
            return cls.MOVING
        return default

    def label(self) -> str:
        if self == FrameGroupType.MOVING:
            return 'Moving / Walking'
        return 'Idle / Standing'


@dataclass
class DatAnimatorData:
    async_animation: bool = False
    loop_count: int = 0
    start_phase: int = 0
    phase_durations: List[Tuple[int, int]] = field(default_factory=list)

    @classmethod
    def fixed_duration(cls, phase_count: int, duration_ms: int = 250) -> 'DatAnimatorData':
        phase_count = max(1, int(phase_count))
        duration_ms = max(1, int(duration_ms))
        return cls(async_animation=False, loop_count=0, start_phase=0, phase_durations=[(duration_ms, duration_ms) for _ in range(phase_count)])

    @classmethod
    def read(cls, reader: io.BytesIO, phase_count: int) -> 'DatAnimatorData':
        head = reader.read(6)
        if len(head) != 6:
            raise ValueError('Unexpected EOF while reading enhanced animator header')
        # OTClient Animator::unserialize: u8 asyncFlag(0 means async), int32 loopCount, int8 startPhase.
        async_animation = head[0] == 0
        loop_count = struct.unpack('<i', head[1:5])[0]
        start_phase = struct.unpack('<b', head[5:6])[0]
        durations: List[Tuple[int, int]] = []
        for _ in range(max(1, int(phase_count))):
            data = reader.read(8)
            if len(data) != 8:
                raise ValueError('Unexpected EOF while reading enhanced animator phase durations')
            durations.append(struct.unpack('<II', data))
        return cls(async_animation=async_animation, loop_count=loop_count, start_phase=start_phase, phase_durations=durations)

    def normalize(self, phase_count: int, default_duration_ms: int = 250) -> None:
        phase_count = max(1, int(phase_count))
        if len(self.phase_durations) < phase_count:
            self.phase_durations.extend([(default_duration_ms, default_duration_ms)] * (phase_count - len(self.phase_durations)))
        elif len(self.phase_durations) > phase_count:
            self.phase_durations = self.phase_durations[:phase_count]
        fixed: List[Tuple[int, int]] = []
        for mn, mx in self.phase_durations:
            mn = max(0, int(mn))
            mx = max(0, int(mx))
            if mx < mn:
                mx = mn
            fixed.append((mn, mx))
        self.phase_durations = fixed
        if self.start_phase >= phase_count:
            self.start_phase = 0
        if self.start_phase < -1:
            self.start_phase = 0

    def to_bytes(self, phase_count: int, default_duration_ms: int = 250) -> bytes:
        clone = copy.deepcopy(self)
        clone.normalize(phase_count, default_duration_ms=default_duration_ms)
        out = bytearray()
        out.append(0 if clone.async_animation else 1)
        out += struct.pack('<i', int(clone.loop_count))
        out += struct.pack('<b', int(clone.start_phase))
        for minimum, maximum in clone.phase_durations:
            out += struct.pack('<II', int(minimum) & 0xFFFFFFFF, int(maximum) & 0xFFFFFFFF)
        return bytes(out)

    def representative_duration_ms(self, phase_count: int, default_duration_ms: int = 250) -> int:
        clone = copy.deepcopy(self)
        clone.normalize(phase_count, default_duration_ms=default_duration_ms)
        if not clone.phase_durations:
            return int(default_duration_ms)
        first_min, first_max = clone.phase_durations[0]
        if first_min == first_max:
            return int(first_min)
        return int((first_min + first_max) // 2)

    def is_uniform_duration(self, phase_count: int, duration_ms: int) -> bool:
        clone = copy.deepcopy(self)
        clone.normalize(phase_count, default_duration_ms=duration_ms)
        target = int(duration_ms)
        return bool(clone.phase_durations) and all(int(a) == target and int(b) == target for a, b in clone.phase_durations)

    def set_fixed_duration(self, phase_count: int, duration_ms: int) -> None:
        duration_ms = max(1, int(duration_ms))
        self.phase_durations = [(duration_ms, duration_ms) for _ in range(max(1, int(phase_count)))]
        self.normalize(phase_count, default_duration_ms=duration_ms)

    def sliced(self, start: int, count: int, default_duration_ms: int = 250) -> 'DatAnimatorData':
        clone = copy.deepcopy(self)
        clone.normalize(max(1, start + count), default_duration_ms=default_duration_ms)
        start = max(0, int(start))
        count = max(1, int(count))
        out = DatAnimatorData(
            async_animation=clone.async_animation,
            loop_count=clone.loop_count,
            start_phase=0 if clone.start_phase >= 0 else -1,
            phase_durations=list(clone.phase_durations[start:start + count]),
        )
        out.normalize(count, default_duration_ms=default_duration_ms)
        return out

    def to_preset_payload(self) -> Dict[str, object]:
        return {
            'async_animation': bool(self.async_animation),
            'loop_count': int(self.loop_count),
            'start_phase': int(self.start_phase),
            'phase_durations': [[int(a), int(b)] for a, b in self.phase_durations],
        }

    @classmethod
    def from_preset_payload(cls, payload: object) -> Optional['DatAnimatorData']:
        if not isinstance(payload, dict):
            return None
        durations: List[Tuple[int, int]] = []
        raw_durations = payload.get('phase_durations', [])
        if isinstance(raw_durations, list):
            for item in raw_durations:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    durations.append((int(item[0]), int(item[1])))
        return cls(
            async_animation=bool(payload.get('async_animation', False)),
            loop_count=int(payload.get('loop_count', 0)),
            start_phase=int(payload.get('start_phase', 0)),
            phase_durations=durations,
        )


@dataclass
class DatFrameGroup:
    group_type: int = int(FrameGroupType.DEFAULT)
    width: int = 1
    height: int = 1
    exact_size: Optional[int] = None
    blend_frames: int = 1
    xdiv: int = 1
    ydiv: int = 1
    zdiv: int = 1
    animation_length: int = 1
    sprite_ids: List[int] = field(default_factory=list)
    animator: Optional[DatAnimatorData] = None

    @property
    def type_enum(self) -> FrameGroupType:
        return FrameGroupType.coerce(self.group_type)

    def type_label(self) -> str:
        if self.type_enum == FrameGroupType.MOVING:
            return 'Moving'
        if self.type_enum == FrameGroupType.IDLE:
            return 'Idle'
        return f'Group {int(self.group_type)}'

    def dimensions_summary(self) -> str:
        return f'{self.type_label()} {self.width}x{self.height} / layers {self.blend_frames} / patt {self.xdiv}x{self.ydiv}x{self.zdiv} / anim {self.animation_length}'

    def expected_sprite_count(self) -> int:
        return int(self.width) * int(self.height) * int(self.blend_frames) * int(self.xdiv) * int(self.ydiv) * int(self.zdiv) * int(self.animation_length)

    def per_phase_sprite_count(self) -> int:
        return int(self.width) * int(self.height) * int(self.blend_frames) * int(self.xdiv) * int(self.ydiv) * int(self.zdiv)

    def normalize_sprite_ids(self) -> None:
        target = self.expected_sprite_count()
        if len(self.sprite_ids) < target:
            self.sprite_ids.extend([0] * (target - len(self.sprite_ids)))
        elif len(self.sprite_ids) > target:
            self.sprite_ids = self.sprite_ids[:target]

    def copy_dimensions_from(self, other: 'DatFrameGroup', *, copy_animation: bool = False) -> None:
        self.width = max(1, int(other.width))
        self.height = max(1, int(other.height))
        self.exact_size = other.exact_size
        self.blend_frames = max(1, int(other.blend_frames))
        self.xdiv = max(1, int(other.xdiv))
        self.ydiv = max(1, int(other.ydiv))
        self.zdiv = max(1, int(other.zdiv))
        if copy_animation:
            self.animation_length = max(1, int(other.animation_length))
        self.normalize_sprite_ids()

    def get_sprite_index(self, w: int, h: int, layer: int, pattern_x: int, pattern_y: int, pattern_z: int, animation_phase: int) -> int:
        width = max(1, int(self.width))
        height = max(1, int(self.height))
        layers = max(1, int(self.blend_frames))
        xdiv = max(1, int(self.xdiv))
        ydiv = max(1, int(self.ydiv))
        zdiv = max(1, int(self.zdiv))
        anim = max(1, int(self.animation_length))
        return ((((((animation_phase % anim) * zdiv + pattern_z) * ydiv + pattern_y) * xdiv + pattern_x) * layers + layer) * height + h) * width + w

    def phase_slice(self, phase: int) -> List[int]:
        self.normalize_sprite_ids()
        per_phase = self.per_phase_sprite_count()
        phase = max(0, min(max(1, int(self.animation_length)) - 1, int(phase)))
        start = phase * per_phase
        return list(self.sprite_ids[start:start + per_phase])

    def build_preview_image(
        self,
        spr: Optional[Tibia76Spr],
        *,
        layer: int = 0,
        pattern_x: int = 0,
        pattern_y: int = 0,
        pattern_z: int = 0,
        animation_phase: int = 0,
        composite_layers: bool = False,
        rotate_quadrants: int = 0,
    ) -> Optional[Image.Image]:
        if spr is None or not self.sprite_ids:
            return None
        width = max(1, int(self.width))
        height = max(1, int(self.height))
        layers = max(1, int(self.blend_frames))
        xdiv = max(1, int(self.xdiv))
        ydiv = max(1, int(self.ydiv))
        zdiv = max(1, int(self.zdiv))
        anim = max(1, int(self.animation_length))
        self.normalize_sprite_ids()
        layer = max(0, min(layers - 1, int(layer)))
        pattern_x = max(0, min(xdiv - 1, int(pattern_x)))
        pattern_y = max(0, min(ydiv - 1, int(pattern_y)))
        pattern_z = max(0, min(zdiv - 1, int(pattern_z)))
        animation_phase = max(0, min(anim - 1, int(animation_phase)))

        preview = Image.new('RGBA', (width * SPRITE_SIZE, height * SPRITE_SIZE), (0, 0, 0, 0))
        layers_to_draw = range(layers) if composite_layers else (layer,)
        for current_layer in layers_to_draw:
            for h in range(height):
                for w in range(width):
                    sprite_index = self.get_sprite_index(w, h, current_layer, pattern_x, pattern_y, pattern_z, animation_phase)
                    if sprite_index < 0 or sprite_index >= len(self.sprite_ids):
                        continue
                    sprite_id = self.sprite_ids[sprite_index]
                    if sprite_id <= 0:
                        continue
                    try:
                        entry = spr.get(int(sprite_id))
                    except IndexError:
                        continue
                    if entry is None:
                        continue
                    target_x = (width - w - 1) * SPRITE_SIZE
                    target_y = (height - h - 1) * SPRITE_SIZE
                    preview.alpha_composite(entry.to_image(), (target_x, target_y))
        rotate_quadrants = int(rotate_quadrants) % 4
        if rotate_quadrants == 1:
            preview = preview.transpose(Image.Transpose.ROTATE_270)
        elif rotate_quadrants == 2:
            preview = preview.transpose(Image.Transpose.ROTATE_180)
        elif rotate_quadrants == 3:
            preview = preview.transpose(Image.Transpose.ROTATE_90)
        return preview

    def to_preset_payload(self) -> Dict[str, object]:
        return {
            'group_type': int(self.group_type),
            'width': int(self.width),
            'height': int(self.height),
            'exact_size': self.exact_size,
            'blend_frames': int(self.blend_frames),
            'xdiv': int(self.xdiv),
            'ydiv': int(self.ydiv),
            'zdiv': int(self.zdiv),
            'animation_length': int(self.animation_length),
            'sprite_ids': list(self.sprite_ids),
            'animator': self.animator.to_preset_payload() if self.animator is not None else None,
        }

    @classmethod
    def from_preset_payload(cls, payload: object) -> Optional['DatFrameGroup']:
        if not isinstance(payload, dict):
            return None
        group = cls(
            group_type=int(payload.get('group_type', int(FrameGroupType.DEFAULT))),
            width=max(1, int(payload.get('width', 1))),
            height=max(1, int(payload.get('height', 1))),
            exact_size=payload.get('exact_size'),
            blend_frames=max(1, int(payload.get('blend_frames', 1))),
            xdiv=max(1, int(payload.get('xdiv', 1))),
            ydiv=max(1, int(payload.get('ydiv', 1))),
            zdiv=max(1, int(payload.get('zdiv', 1))),
            animation_length=max(1, int(payload.get('animation_length', 1))),
            sprite_ids=[int(x) for x in payload.get('sprite_ids', [])] if isinstance(payload.get('sprite_ids', []), list) else [],
            animator=DatAnimatorData.from_preset_payload(payload.get('animator')),
        )
        group.normalize_sprite_ids()
        return group


@dataclass
class DatObject:
    kind: str
    object_id: int
    flag_records: List[Tuple[int, bytes]] = field(default_factory=list)
    width: int = 1
    height: int = 1
    exact_size: Optional[int] = None
    blend_frames: int = 1
    xdiv: int = 1
    ydiv: int = 1
    zdiv: int = 1
    animation_length: int = 1
    sprite_ids: List[int] = field(default_factory=list)
    animation_mode: str = DatAnimationMode.LEGACY_SINGLE_GROUP
    frame_groups: List[DatFrameGroup] = field(default_factory=list)
    animator: Optional[DatAnimatorData] = None

    ground_speed: Optional[int] = None
    max_text_length: Optional[int] = None
    light_level: Optional[int] = None
    light_color: Optional[int] = None
    offset_x: Optional[int] = None
    offset_y: Optional[int] = None
    elevation: Optional[int] = None
    mini_map_color: Optional[int] = None
    lens_help_value: Optional[int] = None

    bool_flags: Dict[str, bool] = field(default_factory=dict)
    unknown_flags: List[Tuple[int, bytes]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in BOOL_FLAG_ORDER:
            self.bool_flags.setdefault(name, False)

    def has_frame_groups(self) -> bool:
        return self.kind == 'outfit' and self.animation_mode == DatAnimationMode.FRAME_GROUPS and bool(self.frame_groups)

    @property
    def num_sprites(self) -> int:
        if self.has_frame_groups():
            return sum(len(group.sprite_ids) for group in self.frame_groups)
        return len(self.sprite_ids)

    def all_sprite_ids(self) -> Iterator[int]:
        if self.has_frame_groups():
            for group in self.frame_groups:
                yield from group.sprite_ids
        else:
            yield from self.sprite_ids

    def dimensions_summary(self) -> str:
        if self.has_frame_groups():
            parts = [group.dimensions_summary() for group in self.frame_groups[:3]]
            if len(self.frame_groups) > 3:
                parts.append(f'+{len(self.frame_groups) - 3} extra')
            return f'frame groups {len(self.frame_groups)}: ' + ' | '.join(parts)
        return f'{self.width}x{self.height} / layers {self.blend_frames} / patt {self.xdiv}x{self.ydiv}x{self.zdiv} / anim {self.animation_length}'

    def expected_sprite_count(self) -> int:
        return int(self.width) * int(self.height) * int(self.blend_frames) * int(self.xdiv) * int(self.ydiv) * int(self.zdiv) * int(self.animation_length)

    def normalize_sprite_ids(self) -> None:
        if self.has_frame_groups():
            for group in self.frame_groups:
                group.normalize_sprite_ids()
            return
        target = self.expected_sprite_count()
        if len(self.sprite_ids) < target:
            self.sprite_ids.extend([0] * (target - len(self.sprite_ids)))
        elif len(self.sprite_ids) > target:
            self.sprite_ids = self.sprite_ids[:target]

    def legacy_frame_group(self, group_type: int = int(FrameGroupType.DEFAULT)) -> DatFrameGroup:
        group = DatFrameGroup(
            group_type=group_type,
            width=self.width,
            height=self.height,
            exact_size=self.exact_size,
            blend_frames=self.blend_frames,
            xdiv=self.xdiv,
            ydiv=self.ydiv,
            zdiv=self.zdiv,
            animation_length=self.animation_length,
            sprite_ids=list(self.sprite_ids),
            animator=copy.deepcopy(self.animator),
        )
        group.normalize_sprite_ids()
        return group

    def get_frame_group(self, group_type: Union[FrameGroupType, int, str], *, fallback: bool = True) -> Optional[DatFrameGroup]:
        target = FrameGroupType.coerce(group_type)
        for group in self.frame_groups:
            if FrameGroupType.coerce(group.group_type) == target:
                return group
        if fallback and self.frame_groups:
            return self.frame_groups[0]
        return None

    def ensure_frame_group(self, group_type: Union[FrameGroupType, int, str], *, repair_legacy_like_single_group: bool = False) -> Optional[DatFrameGroup]:
        """Return a real, distinct frame group of the requested type, creating it if needed.

        Older intermediate editor builds could leave a creature in FRAME_GROUPS mode with only
        an Idle/default group. The UI then fell back to that group while the combobox said
        "Moving", so edits for Moving actually modified Idle. This helper intentionally
        does not use fallback lookup for the requested type.
        """
        if self.kind != 'outfit':
            return None
        if self.animation_mode != DatAnimationMode.FRAME_GROUPS:
            self.convert_legacy_to_frame_groups()
        if not self.frame_groups:
            self.frame_groups = self.build_idle_moving_groups_from_legacy()

        # Break accidental shared references introduced by older UI fallback code.
        fixed_groups: List[DatFrameGroup] = []
        seen_group_ids = set()
        for group in self.frame_groups:
            if id(group) in seen_group_ids:
                group = copy.deepcopy(group)
            seen_group_ids.add(id(group))
            group.sprite_ids = list(group.sprite_ids)
            if group.animator is not None:
                group.animator = copy.deepcopy(group.animator)
            fixed_groups.append(group)
        self.frame_groups = fixed_groups

        target = FrameGroupType.coerce(group_type)
        existing = self.get_frame_group(target, fallback=False)
        if existing is not None:
            existing.group_type = int(target)
            existing.sprite_ids = list(existing.sprite_ids)
            return existing

        idle = self.get_frame_group(FrameGroupType.IDLE, fallback=False)
        moving = self.get_frame_group(FrameGroupType.MOVING, fallback=False)

        if idle is None:
            base = self.frame_groups[0] if self.frame_groups else self.legacy_frame_group(int(FrameGroupType.IDLE))
            idle = copy.deepcopy(base)
            idle.group_type = int(FrameGroupType.IDLE)
            idle.sprite_ids = list(idle.sprite_ids)
            self.frame_groups.insert(0, idle)

        if target == FrameGroupType.MOVING and moving is None:
            if repair_legacy_like_single_group and max(1, int(idle.animation_length)) > 1:
                full_legacy_like = copy.deepcopy(idle)
                moving = copy.deepcopy(full_legacy_like)
                moving.group_type = int(FrameGroupType.MOVING)

                # Keep Idle as a true standing group while preserving the old full legacy
                # animation sequence as Moving. This is the safest recovery for DATs that
                # were accidentally marked as frame-group DATs before a Moving group existed.
                idle.animation_length = 1
                idle.sprite_ids = full_legacy_like.phase_slice(0)
                idle.animator = None
                idle.normalize_sprite_ids()
            else:
                moving = copy.deepcopy(idle)
                moving.group_type = int(FrameGroupType.MOVING)
            moving.sprite_ids = list(moving.sprite_ids)
            self.frame_groups.append(moving)
            return moving

        if target == FrameGroupType.IDLE:
            return idle

        base = moving or idle or (self.frame_groups[0] if self.frame_groups else self.legacy_frame_group(int(target)))
        group = copy.deepcopy(base)
        group.group_type = int(target)
        group.sprite_ids = list(group.sprite_ids)
        self.frame_groups.append(group)
        return group

    def repair_idle_moving_frame_groups(self, *, repair_legacy_like_single_group: bool = True) -> bool:
        if self.kind != 'outfit':
            return False
        changed = False
        before = [(int(g.group_type), int(g.animation_length), list(g.sprite_ids)) for g in self.frame_groups]
        self.animation_mode = DatAnimationMode.FRAME_GROUPS
        idle = self.ensure_frame_group(FrameGroupType.IDLE, repair_legacy_like_single_group=repair_legacy_like_single_group)
        moving = self.ensure_frame_group(FrameGroupType.MOVING, repair_legacy_like_single_group=repair_legacy_like_single_group)
        if idle is not None:
            idle.group_type = int(FrameGroupType.IDLE)
            idle.normalize_sprite_ids()
        if moving is not None:
            moving.group_type = int(FrameGroupType.MOVING)
            moving.normalize_sprite_ids()
        after = [(int(g.group_type), int(g.animation_length), list(g.sprite_ids)) for g in self.frame_groups]
        if before != after:
            changed = True
        if idle is not None:
            self.set_legacy_from_group(idle)
        return changed

    def set_legacy_from_group(self, group: DatFrameGroup) -> None:
        self.width = group.width
        self.height = group.height
        self.exact_size = group.exact_size
        self.blend_frames = group.blend_frames
        self.xdiv = group.xdiv
        self.ydiv = group.ydiv
        self.zdiv = group.zdiv
        self.animation_length = group.animation_length
        self.sprite_ids = list(group.sprite_ids)
        self.animator = copy.deepcopy(group.animator)
        self.normalize_sprite_ids()

    def build_preset_payload(self) -> Dict[str, object]:
        payload = {
            'kind': self.kind,
            'flags': {name: bool(self.bool_flags.get(name, False)) for name in BOOL_FLAG_ORDER},
            'fields': {field_name: getattr(self, field_name) for field_name in DAT_PRESET_COPY_FIELDS},
        }
        if self.kind == 'outfit' and self.has_frame_groups():
            payload['animation_mode'] = self.animation_mode
            payload['frame_groups'] = [group.to_preset_payload() for group in self.frame_groups]
        elif self.animator is not None:
            payload['animator'] = self.animator.to_preset_payload()
        return payload

    def apply_preset_payload(self, payload: Dict[str, object]) -> None:
        raw_flags = payload.get('flags', {})
        if not isinstance(raw_flags, dict):
            raw_flags = {}
        self.bool_flags = {name: bool(raw_flags.get(name, False)) for name in BOOL_FLAG_ORDER}

        raw_fields = payload.get('fields', {})
        if not isinstance(raw_fields, dict):
            raw_fields = {}
        for field_name in DAT_PRESET_COPY_FIELDS:
            setattr(self, field_name, raw_fields.get(field_name))

        raw_groups = payload.get('frame_groups')
        if self.kind == 'outfit' and payload.get('animation_mode') == DatAnimationMode.FRAME_GROUPS and isinstance(raw_groups, list):
            groups = []
            for raw_group in raw_groups:
                group = DatFrameGroup.from_preset_payload(raw_group)
                if group is not None:
                    groups.append(group)
            if groups:
                self.animation_mode = DatAnimationMode.FRAME_GROUPS
                self.frame_groups = groups
                self.set_legacy_from_group(groups[0])
        elif payload.get('animator') is not None:
            self.animator = DatAnimatorData.from_preset_payload(payload.get('animator'))

    def apply_flag_record(self, code: int, payload: bytes) -> None:
        self.flag_records.append((code, payload))
        if code == 0x00 and len(payload) == 2:
            self.ground_speed = struct.unpack('<H', payload)[0]
        elif code in (0x08, 0x09) and len(payload) == 2:
            self.max_text_length = struct.unpack('<H', payload)[0]
            self.bool_flags['writable' if code == 0x08 else 'writable_once'] = True
        elif code == 0x15 and len(payload) == 4:
            self.light_level, self.light_color = struct.unpack('<HH', payload)
        elif code == 0x18 and len(payload) == 4:
            self.offset_x, self.offset_y = struct.unpack('<HH', payload)
        elif code == 0x19 and len(payload) == 2:
            self.elevation = struct.unpack('<H', payload)[0]
        elif code == 0x1C and len(payload) == 2:
            self.mini_map_color = struct.unpack('<H', payload)[0]
        elif code == 0x1D and len(payload) == 2:
            self.lens_help_value = struct.unpack('<H', payload)[0]
        elif code in DAT_FLAG_INFO and DAT_FLAG_INFO[code][1] == 0:
            self.bool_flags[DAT_FLAG_INFO[code][0]] = True
        else:
            self.unknown_flags.append((code, payload))

    def rebuild_flag_records(self) -> List[Tuple[int, bytes]]:
        records: List[Tuple[int, bytes]] = []

        def add_bool(name: str) -> None:
            if self.bool_flags.get(name, False):
                records.append((FLAG_NAME_TO_CODE[name], b''))

        if self.ground_speed is not None:
            records.append((0x00, struct.pack('<H', int(self.ground_speed) & 0xFFFF)))
        for name in ('ground_border', 'on_bottom', 'on_top', 'container', 'stackable', 'multi_use', 'force_use'):
            add_bool(name)
        if self.bool_flags.get('writable', False):
            records.append((0x08, struct.pack('<H', int(self.max_text_length or 0) & 0xFFFF)))
        if self.bool_flags.get('writable_once', False):
            records.append((0x09, struct.pack('<H', int(self.max_text_length or 0) & 0xFFFF)))
        for name in ('fluid_container', 'fluid', 'unpassable', 'unmoveable', 'block_missile', 'block_pathfind', 'pickupable', 'hangable', 'vertical', 'horizontal', 'rotatable'):
            add_bool(name)
        if self.light_level is not None or self.light_color is not None:
            records.append((0x15, struct.pack('<HH', int(self.light_level or 0) & 0xFFFF, int(self.light_color or 0) & 0xFFFF)))
        for name in ('floor_change', 'unknown_17'):
            add_bool(name)
        if self.offset_x is not None or self.offset_y is not None:
            records.append((0x18, struct.pack('<HH', int(self.offset_x or 0) & 0xFFFF, int(self.offset_y or 0) & 0xFFFF)))
        if self.elevation is not None:
            records.append((0x19, struct.pack('<H', int(self.elevation) & 0xFFFF)))
        for name in ('lying_object', 'animate_always'):
            add_bool(name)
        if self.mini_map_color is not None:
            records.append((0x1C, struct.pack('<H', int(self.mini_map_color) & 0xFFFF)))
        if self.lens_help_value is not None:
            records.append((0x1D, struct.pack('<H', int(self.lens_help_value) & 0xFFFF)))
        add_bool('full_ground')

        handled = set(DAT_FLAG_INFO.keys())
        for code, payload in self.unknown_flags:
            if code not in handled:
                records.append((code, payload))
        self.flag_records = list(records)
        return records

    def is_placeholder(self) -> bool:
        sprite_ids = list(self.all_sprite_ids())
        if not sprite_ids:
            sprite_ids = [0]
        zero_sprites = all(int(s) == 0 for s in sprite_ids)
        legacy_shape = (
            self.width == 1 and self.height == 1 and self.exact_size is None and
            self.blend_frames == 1 and self.xdiv == 1 and self.ydiv == 1 and self.zdiv == 1 and self.animation_length == 1
        )
        frame_shape = True
        if self.has_frame_groups():
            frame_shape = len(self.frame_groups) == 1 and self.frame_groups[0].expected_sprite_count() == 1
        return (
            legacy_shape and frame_shape and zero_sprites and
            self.ground_speed is None and self.max_text_length is None and self.light_level is None and self.light_color is None and
            self.offset_x is None and self.offset_y is None and self.elevation is None and self.mini_map_color is None and self.lens_help_value is None and
            not any(self.bool_flags.values()) and not self.unknown_flags
        )

    @classmethod
    def placeholder(cls, kind: str, object_id: int) -> 'DatObject':
        obj = cls(kind=kind, object_id=object_id)
        obj.sprite_ids = [0]
        return obj

    def get_sprite_index(self, w: int, h: int, layer: int, pattern_x: int, pattern_y: int, pattern_z: int, animation_phase: int) -> int:
        width = max(1, int(self.width))
        height = max(1, int(self.height))
        layers = max(1, int(self.blend_frames))
        xdiv = max(1, int(self.xdiv))
        ydiv = max(1, int(self.ydiv))
        zdiv = max(1, int(self.zdiv))
        anim = max(1, int(self.animation_length))
        return ((((((animation_phase % anim) * zdiv + pattern_z) * ydiv + pattern_y) * xdiv + pattern_x) * layers + layer) * height + h) * width + w

    def build_preview_image(
        self,
        spr: Optional[Tibia76Spr],
        *,
        layer: int = 0,
        pattern_x: int = 0,
        pattern_y: int = 0,
        pattern_z: int = 0,
        animation_phase: int = 0,
        composite_layers: bool = False,
        rotate_quadrants: int = 0,
        frame_group_type: Union[FrameGroupType, int, str, None] = None,
    ) -> Optional[Image.Image]:
        if self.has_frame_groups():
            group = self.get_frame_group(FrameGroupType.IDLE if frame_group_type is None else frame_group_type)
            if group is None:
                return None
            return group.build_preview_image(
                spr,
                layer=layer,
                pattern_x=pattern_x,
                pattern_y=pattern_y,
                pattern_z=pattern_z,
                animation_phase=animation_phase,
                composite_layers=composite_layers,
                rotate_quadrants=rotate_quadrants,
            )
        return self.legacy_frame_group().build_preview_image(
            spr,
            layer=layer,
            pattern_x=pattern_x,
            pattern_y=pattern_y,
            pattern_z=pattern_z,
            animation_phase=animation_phase,
            composite_layers=composite_layers,
            rotate_quadrants=rotate_quadrants,
        )

    def build_idle_moving_groups_from_legacy(self, *, use_all_legacy_phases_as_moving: bool = True) -> List[DatFrameGroup]:
        self.normalize_sprite_ids()
        base = self.legacy_frame_group()
        per_phase = base.per_phase_sprite_count()
        phase0 = base.phase_slice(0)
        idle = DatFrameGroup(
            group_type=int(FrameGroupType.IDLE),
            width=base.width,
            height=base.height,
            exact_size=base.exact_size,
            blend_frames=base.blend_frames,
            xdiv=base.xdiv,
            ydiv=base.ydiv,
            zdiv=base.zdiv,
            animation_length=1,
            sprite_ids=phase0,
        )
        if base.animation_length <= 1:
            moving_sprites = list(phase0)
            moving_anim = 1
            moving_animator = None
        elif use_all_legacy_phases_as_moving:
            # 7.6/custom legacy outfits often use phase 0 both as standing and as the first
            # walking phase. Keeping the full legacy sequence in Moving avoids an empty/gap
            # frame in OTCv8 when GameIdleAnimations is enabled for old looktypes.
            moving_sprites = list(base.sprite_ids)
            moving_anim = base.animation_length
            moving_animator = copy.deepcopy(base.animator)
        else:
            moving_sprites = list(base.sprite_ids[per_phase:])
            moving_anim = max(1, base.animation_length - 1)
            moving_animator = base.animator.sliced(1, moving_anim) if base.animator is not None else None
        moving = DatFrameGroup(
            group_type=int(FrameGroupType.MOVING),
            width=base.width,
            height=base.height,
            exact_size=base.exact_size,
            blend_frames=base.blend_frames,
            xdiv=base.xdiv,
            ydiv=base.ydiv,
            zdiv=base.zdiv,
            animation_length=moving_anim,
            sprite_ids=moving_sprites,
            animator=moving_animator,
        )
        idle.normalize_sprite_ids()
        moving.normalize_sprite_ids()
        return [idle, moving]

    def convert_legacy_to_frame_groups(self, *, use_all_legacy_phases_as_moving: bool = True) -> None:
        self.animation_mode = DatAnimationMode.FRAME_GROUPS
        self.frame_groups = self.build_idle_moving_groups_from_legacy(use_all_legacy_phases_as_moving=use_all_legacy_phases_as_moving)
        self.set_legacy_from_group(self.frame_groups[0])

    def prepend_idle_phase_to_moving(self) -> bool:
        if self.kind != 'outfit':
            return False
        self.repair_idle_moving_frame_groups(repair_legacy_like_single_group=True)
        idle = self.get_frame_group(FrameGroupType.IDLE, fallback=False)
        moving = self.get_frame_group(FrameGroupType.MOVING, fallback=False)
        if idle is None or moving is None:
            return False
        idle_phase0 = idle.phase_slice(0)
        if moving.phase_slice(0) == idle_phase0:
            # Already repaired / already legacy-compatible. Treat as success so the UI
            # does not show a misleading "could not prepend" error.
            return True
        target = copy.deepcopy(moving)
        target.animation_length = max(1, int(moving.animation_length)) + 1
        out = [0] * target.expected_sprite_count()
        self._append_group_phase_into_legacy(out, target, idle, 0, 0)
        for phase in range(max(1, int(moving.animation_length))):
            self._append_group_phase_into_legacy(out, target, moving, phase, phase + 1)
        moving.animation_length = target.animation_length
        moving.sprite_ids = out
        if moving.animator is not None:
            default_ms = moving.animator.representative_duration_ms(max(1, int(moving.animation_length) - 1))
            moving.animator.set_fixed_duration(moving.animation_length, default_ms)
        moving.normalize_sprite_ids()
        self.set_legacy_from_group(idle)
        return True

    def _legacy_target_index(
        self,
        width: int,
        height: int,
        layers: int,
        xdiv: int,
        ydiv: int,
        zdiv: int,
        animation_length: int,
        w: int,
        h: int,
        layer: int,
        pattern_x: int,
        pattern_y: int,
        pattern_z: int,
        animation_phase: int,
    ) -> int:
        return ((((((animation_phase % animation_length) * zdiv + pattern_z) * ydiv + pattern_y) * xdiv + pattern_x) * layers + layer) * height + h) * width + w

    def _append_group_phase_into_legacy(self, out: List[int], target: DatFrameGroup, source: DatFrameGroup, source_phase: int, dest_phase: int) -> None:
        source.normalize_sprite_ids()
        for z in range(min(target.zdiv, source.zdiv)):
            for y in range(min(target.ydiv, source.ydiv)):
                for x in range(min(target.xdiv, source.xdiv)):
                    for layer in range(min(target.blend_frames, source.blend_frames)):
                        for h in range(min(target.height, source.height)):
                            for w in range(min(target.width, source.width)):
                                src_idx = source.get_sprite_index(w, h, layer, x, y, z, source_phase)
                                dst_idx = self._legacy_target_index(target.width, target.height, target.blend_frames, target.xdiv, target.ydiv, target.zdiv, target.animation_length, w, h, layer, x, y, z, dest_phase)
                                if 0 <= src_idx < len(source.sprite_ids) and 0 <= dst_idx < len(out):
                                    out[dst_idx] = source.sprite_ids[src_idx]

    def convert_frame_groups_to_legacy(self, *, append_all_idle_phases: bool = False) -> None:
        if not self.frame_groups:
            self.animation_mode = DatAnimationMode.LEGACY_SINGLE_GROUP
            self.normalize_sprite_ids()
            return
        idle = self.get_frame_group(FrameGroupType.IDLE) or self.frame_groups[0]
        moving = self.get_frame_group(FrameGroupType.MOVING) or idle
        groups_for_size = [idle, moving]
        target = DatFrameGroup(
            group_type=int(FrameGroupType.DEFAULT),
            width=max(1, max(int(g.width) for g in groups_for_size)),
            height=max(1, max(int(g.height) for g in groups_for_size)),
            exact_size=max([int(g.exact_size or max(g.width, g.height)) for g in groups_for_size]),
            blend_frames=max(1, max(int(g.blend_frames) for g in groups_for_size)),
            xdiv=max(1, max(int(g.xdiv) for g in groups_for_size)),
            ydiv=max(1, max(int(g.ydiv) for g in groups_for_size)),
            zdiv=max(1, max(int(g.zdiv) for g in groups_for_size)),
            animation_length=1,
        )
        idle_phase_count = max(1, idle.animation_length if append_all_idle_phases else 1)
        moving_start_phase = 0
        if not append_all_idle_phases and moving is not idle and moving.animation_length > 1:
            # If this was produced from a legacy 7.6 sequence, Moving keeps the old full walk
            # sequence and phase 0 equals Idle phase 0. Do not duplicate it when exporting
            # back to legacy fallback.
            if idle.phase_slice(0) == moving.phase_slice(0):
                moving_start_phase = 1
        moving_phase_count = max(1, max(1, int(moving.animation_length)) - moving_start_phase)
        target.animation_length = idle_phase_count + moving_phase_count
        out = [0] * target.expected_sprite_count()
        dest = 0
        for phase in range(idle_phase_count):
            self._append_group_phase_into_legacy(out, target, idle, phase, dest)
            dest += 1
        for phase in range(moving_start_phase, max(1, int(moving.animation_length))):
            self._append_group_phase_into_legacy(out, target, moving, phase, dest)
            dest += 1
        self.animation_mode = DatAnimationMode.LEGACY_SINGLE_GROUP
        self.frame_groups = []
        self.width = target.width
        self.height = target.height
        self.exact_size = target.exact_size
        self.blend_frames = target.blend_frames
        self.xdiv = target.xdiv
        self.ydiv = target.ydiv
        self.zdiv = target.zdiv
        self.animation_length = target.animation_length
        self.sprite_ids = out
        self.normalize_sprite_ids()


class Tibia76Dat:
    def __init__(self) -> None:
        self.signature: int = 0
        self.item_max_id: int = MIN_ITEM_ID - 1
        self.outfit_count: int = 0
        self.effect_count: int = 0
        self.distance_count: int = 0
        self.sprite_id_format: DatSpriteIdFormat = DatSpriteIdFormat.U16
        self.game_idle_animations: bool = False
        self.game_enhanced_animations: bool = False
        self.client_version: int = 760
        self.items: List[DatObject] = []
        self.outfits: List[DatObject] = []
        self.effects: List[DatObject] = []
        self.distances: List[DatObject] = []

    @staticmethod
    def _read_flag_payload(reader: io.BytesIO, code: int) -> bytes:
        if code not in DAT_FLAG_INFO:
            raise ValueError(f'Unsupported DAT flag 0x{code:02X}')
        size = DAT_FLAG_INFO[code][1]
        payload = reader.read(size)
        if len(payload) != size:
            raise ValueError(f'Unexpected EOF while reading payload for DAT flag 0x{code:02X}')
        return payload

    @staticmethod
    def _parse_legacy_shape_and_sprites(reader: io.BytesIO, thing: DatObject, kind: str, object_id: int, sprite_id_format: DatSpriteIdFormat, *, game_enhanced_animations: bool = False, client_version: int = 760) -> None:
        head = reader.read(2)
        if len(head) != 2:
            raise ValueError(f'Unexpected EOF while reading dimensions for {kind} #{object_id}')
        thing.width, thing.height = head[0], head[1]
        if thing.width < 1 or thing.height < 1:
            raise ValueError(f'Invalid object dimensions for {kind} #{object_id}: {thing.width}x{thing.height}')
        if thing.width > 1 or thing.height > 1:
            exact = reader.read(1)
            if len(exact) != 1:
                raise ValueError(f'Unexpected EOF while reading exact-size byte for {kind} #{object_id}')
            thing.exact_size = exact[0]
        tail_size = 5 if int(client_version) >= 755 else 4
        tail = reader.read(tail_size)
        if len(tail) != tail_size:
            raise ValueError(f'Unexpected EOF while reading animation/pattern info for {kind} #{object_id}')
        thing.blend_frames = tail[0]
        thing.xdiv = tail[1]
        thing.ydiv = tail[2]
        if int(client_version) >= 755:
            thing.zdiv = tail[3]
            thing.animation_length = tail[4]
        else:
            thing.zdiv = 1
            thing.animation_length = tail[3]
        if thing.blend_frames < 1 or thing.xdiv < 1 or thing.ydiv < 1 or thing.zdiv < 1 or thing.animation_length < 1:
            raise ValueError(
                f'Invalid object pattern/animation dimensions for {kind} #{object_id}: '
                f'layers={thing.blend_frames}, pattern={thing.xdiv}x{thing.ydiv}x{thing.zdiv}, animation={thing.animation_length}'
            )
        if thing.animation_length > 1 and game_enhanced_animations:
            thing.animator = DatAnimatorData.read(reader, thing.animation_length)
        else:
            thing.animator = None
        sprite_count = thing.expected_sprite_count()
        byte_size = sprite_id_format.byte_size
        sprite_blob = reader.read(sprite_count * byte_size)
        if len(sprite_blob) != sprite_count * byte_size:
            raise ValueError(f'Unexpected EOF while reading {sprite_id_format.label()} sprite ids for {kind} #{object_id}')
        if sprite_count:
            unpack_fmt = 'I' if sprite_id_format == DatSpriteIdFormat.U32 else 'H'
            thing.sprite_ids = list(struct.unpack(f'<{sprite_count}{unpack_fmt}', sprite_blob))
        else:
            thing.sprite_ids = []
        thing.animation_mode = DatAnimationMode.LEGACY_SINGLE_GROUP
        thing.frame_groups = []

    @staticmethod
    def _parse_frame_group(reader: io.BytesIO, kind: str, object_id: int, group_index: int, sprite_id_format: DatSpriteIdFormat, *, game_enhanced_animations: bool, client_version: int) -> DatFrameGroup:
        group_type_raw = reader.read(1)
        if len(group_type_raw) != 1:
            raise ValueError(f'Unexpected EOF while reading frame group type for {kind} #{object_id} group {group_index}')
        dims = reader.read(2)
        if len(dims) != 2:
            raise ValueError(f'Unexpected EOF while reading dimensions for {kind} #{object_id} group {group_index}')
        group = DatFrameGroup(group_type=group_type_raw[0], width=dims[0], height=dims[1])
        if group.width < 1 or group.height < 1:
            raise ValueError(f'Invalid frame group dimensions for {kind} #{object_id} group {group_index}: {group.width}x{group.height}')
        if group.width > 1 or group.height > 1:
            exact = reader.read(1)
            if len(exact) != 1:
                raise ValueError(f'Unexpected EOF while reading exact-size byte for {kind} #{object_id} group {group_index}')
            group.exact_size = exact[0]
        tail_size = 5 if int(client_version) >= 755 else 4
        tail = reader.read(tail_size)
        if len(tail) != tail_size:
            raise ValueError(f'Unexpected EOF while reading pattern/animation info for {kind} #{object_id} group {group_index}')
        group.blend_frames = tail[0]
        group.xdiv = tail[1]
        group.ydiv = tail[2]
        if int(client_version) >= 755:
            group.zdiv = tail[3]
            group.animation_length = tail[4]
        else:
            group.zdiv = 1
            group.animation_length = tail[3]
        if group.blend_frames < 1 or group.xdiv < 1 or group.ydiv < 1 or group.zdiv < 1 or group.animation_length < 1:
            raise ValueError(
                f'Invalid frame group pattern/animation dimensions for {kind} #{object_id} group {group_index}: '
                f'layers={group.blend_frames}, pattern={group.xdiv}x{group.ydiv}x{group.zdiv}, animation={group.animation_length}'
            )
        if group.animation_length > 1 and game_enhanced_animations:
            group.animator = DatAnimatorData.read(reader, group.animation_length)
        sprite_count = group.expected_sprite_count()
        byte_size = sprite_id_format.byte_size
        sprite_blob = reader.read(sprite_count * byte_size)
        if len(sprite_blob) != sprite_count * byte_size:
            raise ValueError(f'Unexpected EOF while reading {sprite_id_format.label()} sprite ids for {kind} #{object_id} group {group_index}')
        if sprite_count:
            unpack_fmt = 'I' if sprite_id_format == DatSpriteIdFormat.U32 else 'H'
            group.sprite_ids = list(struct.unpack(f'<{sprite_count}{unpack_fmt}', sprite_blob))
        else:
            group.sprite_ids = []
        return group

    @staticmethod
    def _parse_object(
        reader: io.BytesIO,
        kind: str,
        object_id: int,
        sprite_id_format: Union[DatSpriteIdFormat, int, str] = DatSpriteIdFormat.U16,
        *,
        game_idle_animations: bool = False,
        game_enhanced_animations: Union[bool, str] = False,
        client_version: int = 760,
    ) -> DatObject:
        thing = DatObject(kind=kind, object_id=object_id)
        while True:
            flag_byte = reader.read(1)
            if len(flag_byte) != 1:
                raise ValueError(f'Unexpected EOF while reading flags for {kind} #{object_id}')
            code = flag_byte[0]
            if code == 0xFF:
                break
            payload = Tibia76Dat._read_flag_payload(reader, code)
            thing.apply_flag_record(code, payload)

        fmt = DatSpriteIdFormat.coerce(sprite_id_format)
        # Recovery profile for DAT files produced by the earlier broken enhanced-animation patch:
        # creature frame groups had animator blocks, but animated items/effects/missiles did not.
        # Correct OpenTibiaBR/OTCv8 DATs use one global GameEnhancedAnimations rule, but this
        # per-category mode lets the editor reopen those mixed files and save them back correctly.
        enhanced_mode = str(game_enhanced_animations).strip().lower() if isinstance(game_enhanced_animations, str) else ''
        enhanced_for_object = bool(game_enhanced_animations)
        if enhanced_mode in {'creatures_only', 'outfits_only', 'creature_only', 'outfit_only'}:
            enhanced_for_object = kind == 'outfit'
        if kind == 'outfit' and game_idle_animations:
            group_count_raw = reader.read(1)
            if len(group_count_raw) != 1:
                raise ValueError(f'Unexpected EOF while reading creature frame group count for {kind} #{object_id}')
            group_count = group_count_raw[0]
            if group_count < 1:
                raise ValueError(f'Invalid creature frame group count for {kind} #{object_id}: {group_count}')
            groups = [
                Tibia76Dat._parse_frame_group(
                    reader,
                    kind,
                    object_id,
                    group_index,
                    fmt,
                    game_enhanced_animations=enhanced_for_object,
                    client_version=client_version,
                )
                for group_index in range(group_count)
            ]
            thing.animation_mode = DatAnimationMode.FRAME_GROUPS
            thing.frame_groups = groups
            thing.set_legacy_from_group(groups[0])
        else:
            Tibia76Dat._parse_legacy_shape_and_sprites(
                reader,
                thing,
                kind,
                object_id,
                fmt,
                game_enhanced_animations=enhanced_for_object,
                client_version=client_version,
            )
        return thing

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        sprite_id_format: Union[DatSpriteIdFormat, int, str] = 'auto',
        game_idle_animations: Union[bool, str] = 'auto',
        game_enhanced_animations: Union[bool, str] = 'auto',
        client_version: int = 760,
    ) -> 'Tibia76Dat':
        return cls.from_bytes(
            Path(path).read_bytes(),
            sprite_id_format=sprite_id_format,
            game_idle_animations=game_idle_animations,
            game_enhanced_animations=game_enhanced_animations,
            client_version=client_version,
        )

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        sprite_id_format: Union[DatSpriteIdFormat, int, str] = 'auto',
        game_idle_animations: Union[bool, str] = 'auto',
        game_enhanced_animations: Union[bool, str] = 'auto',
        client_version: int = 760,
    ) -> 'Tibia76Dat':
        fmt_is_auto = isinstance(sprite_id_format, str) and sprite_id_format.strip().lower() == 'auto'
        idle_is_auto = isinstance(game_idle_animations, str) and game_idle_animations.strip().lower() == 'auto'
        enhanced_is_auto = isinstance(game_enhanced_animations, str) and game_enhanced_animations.strip().lower() == 'auto'
        if fmt_is_auto or idle_is_auto or enhanced_is_auto:
            fmt_candidates = (DatSpriteIdFormat.U16, DatSpriteIdFormat.U32) if fmt_is_auto else (DatSpriteIdFormat.coerce(sprite_id_format),)
            idle_candidates = (False, True) if idle_is_auto else (bool(game_idle_animations),)
            errors: List[str] = []
            for fmt_candidate in fmt_candidates:
                for idle_candidate in idle_candidates:
                    if enhanced_is_auto:
                        # Try enhanced profiles before the plain legacy profile. A DAT with animator bytes can
                        # sometimes be mis-read as plain sprite IDs without throwing immediately, so returning
                        # the first non-enhanced parse is unsafe.
                        if idle_candidate:
                            # Repair mode for files saved by the previous partial enhanced-animation implementation.
                            # It reads animator blocks only on creature/outfit frame groups, then marks the loaded
                            # DAT as enhanced so the next save writes proper animator blocks for every animated thing.
                            enhanced_candidates: Tuple[Union[bool, str], ...] = (True, 'creatures_only', False)
                        else:
                            enhanced_candidates = (True, False)
                    else:
                        enhanced_candidates = (game_enhanced_animations if isinstance(game_enhanced_animations, str) else bool(game_enhanced_animations),)
                    for enhanced_candidate in enhanced_candidates:
                        try:
                            parsed = cls.from_bytes(
                                data,
                                sprite_id_format=fmt_candidate,
                                game_idle_animations=idle_candidate,
                                game_enhanced_animations=enhanced_candidate,
                                client_version=client_version,
                            )
                            if isinstance(enhanced_candidate, str) and enhanced_candidate.strip().lower() in {'creatures_only', 'outfits_only', 'creature_only', 'outfit_only'}:
                                parsed.game_enhanced_animations = True
                                parsed.recovered_mixed_enhanced_animations = True
                            return parsed
                        except Exception as exc:
                            errors.append(f'{fmt_candidate.label()} idle={idle_candidate} enhanced={enhanced_candidate}: {exc}')
            raise ValueError('Could not parse DAT with supported sprite/frame-group profiles. ' + ' | '.join(errors))

        fmt = DatSpriteIdFormat.coerce(sprite_id_format)
        reader = io.BytesIO(data)
        if len(data) < 12:
            raise ValueError('File too small to be a valid Tibia.dat')
        obj = cls()
        obj.sprite_id_format = fmt
        obj.game_idle_animations = bool(game_idle_animations)
        enhanced_mode = str(game_enhanced_animations).strip().lower() if isinstance(game_enhanced_animations, str) else ''
        obj.game_enhanced_animations = bool(game_enhanced_animations) or enhanced_mode in {'creatures_only', 'outfits_only', 'creature_only', 'outfit_only'}
        obj.recovered_mixed_enhanced_animations = enhanced_mode in {'creatures_only', 'outfits_only', 'creature_only', 'outfit_only'}
        obj.client_version = int(client_version)
        obj.signature = struct.unpack('<I', reader.read(4))[0]
        obj.item_max_id, obj.outfit_count, obj.effect_count, obj.distance_count = struct.unpack('<HHHH', reader.read(8))

        parse_kwargs = dict(
            sprite_id_format=fmt,
            game_idle_animations=obj.game_idle_animations,
            game_enhanced_animations=game_enhanced_animations,
            client_version=obj.client_version,
        )
        if obj.item_max_id >= MIN_ITEM_ID:
            obj.items = [cls._parse_object(reader, 'item', object_id, **parse_kwargs) for object_id in range(MIN_ITEM_ID, obj.item_max_id + 1)]
        else:
            obj.items = []
        obj.outfits = [cls._parse_object(reader, 'outfit', object_id, **parse_kwargs) for object_id in range(MIN_OUTFIT_ID, obj.outfit_count + 1)]
        obj.effects = [cls._parse_object(reader, 'effect', object_id, **parse_kwargs) for object_id in range(MIN_EFFECT_ID, obj.effect_count + 1)]
        obj.distances = [cls._parse_object(reader, 'distance', object_id, **parse_kwargs) for object_id in range(MIN_DISTANCE_ID, obj.distance_count + 1)]

        if reader.tell() != len(data):
            raise ValueError(f'Unparsed trailing data in DAT: {len(data) - reader.tell()} bytes')
        return obj

    def _iter_category_records(self, kind: str) -> Iterator[DatObject]:
        if kind == 'item':
            yield from self.items
        elif kind == 'outfit':
            yield from self.outfits
        elif kind == 'effect':
            yield from self.effects
        elif kind == 'distance':
            yield from self.distances
        else:
            raise KeyError(kind)

    def iter_all(self) -> Iterable[DatObject]:
        yield from self.items
        yield from self.outfits
        yield from self.effects
        yield from self.distances

    def objects_for_kind(self, kind: str) -> List[DatObject]:
        if kind == 'item':
            return self.items
        if kind == 'outfit':
            return self.outfits
        if kind == 'effect':
            return self.effects
        if kind == 'distance':
            return self.distances
        raise KeyError(kind)

    def get_object(self, kind: str, object_id: int) -> Optional[DatObject]:
        objects = self.objects_for_kind(kind)
        minimum = MIN_ITEM_ID if kind == 'item' else 1
        index = int(object_id) - minimum
        if 0 <= index < len(objects):
            return objects[index]
        return None

    def _renumber_category(self, kind: str) -> None:
        objects = self.objects_for_kind(kind)
        start = MIN_ITEM_ID if kind == 'item' else 1
        for idx, obj in enumerate(objects, start=start):
            obj.kind = kind
            obj.object_id = idx

    def renumber_object_ids(self) -> None:
        self._renumber_category('item')
        self._renumber_category('outfit')
        self._renumber_category('effect')
        self._renumber_category('distance')
        self.item_max_id = MIN_ITEM_ID - 1 + len(self.items)
        self.outfit_count = len(self.outfits)
        self.effect_count = len(self.effects)
        self.distance_count = len(self.distances)

    def _trim_trailing_placeholders(self, kind: str) -> None:
        objects = self.objects_for_kind(kind)
        while objects and objects[-1].is_placeholder():
            objects.pop()
        self.renumber_object_ids()

    def append_new(self, kind: str) -> DatObject:
        target = self.objects_for_kind(kind)
        obj = DatObject(kind=kind, object_id=0)
        obj.normalize_sprite_ids()
        target.append(obj)
        self.renumber_object_ids()
        return target[-1]

    def duplicate_object(self, kind: str, object_id: int) -> DatObject:
        source = self.get_object(kind, object_id)
        if source is None:
            raise KeyError((kind, object_id))
        clone = copy.deepcopy(source)
        target = self.objects_for_kind(kind)
        target.append(clone)
        self.renumber_object_ids()
        return target[-1]

    def delete_object(self, kind: str, object_id: int) -> Tuple[DatObject, bool]:
        """ObjectBuilder-style delete.

        Returns (removed_or_replaced_object, compacted_tail).
        If deleting from the middle, the slot becomes an empty placeholder and IDs do not shift.
        If deleting the last object, the category tail shrinks; consecutive trailing placeholders are also trimmed.
        """
        target = self.objects_for_kind(kind)
        minimum = MIN_ITEM_ID if kind == 'item' else 1
        index = int(object_id) - minimum
        if not (0 <= index < len(target)):
            raise KeyError((kind, object_id))
        removed = target[index]
        compacted_tail = False
        if index == len(target) - 1:
            target.pop()
            compacted_tail = True
            while target and target[-1].is_placeholder():
                target.pop()
        else:
            target[index] = DatObject.placeholder(kind, object_id)
        self.renumber_object_ids()
        return removed, compacted_tail

    def repair_generated_effect_animator_durations(self, *, old_duration_ms: int = 250, new_duration_ms: int = DEFAULT_EFFECT_ENHANCED_FRAME_DURATION_MS) -> int:
        repaired = 0
        for obj in list(self.effects) + list(self.distances):
            if obj.animation_length <= 1 or obj.animator is None:
                continue
            if obj.animator.is_uniform_duration(obj.animation_length, int(old_duration_ms)):
                obj.animator.set_fixed_duration(obj.animation_length, int(new_duration_ms))
                repaired += 1
        return repaired

    def prepend_idle_phase_to_all_moving_groups(self) -> int:
        repaired = 0
        for obj in self.outfits:
            if obj.has_frame_groups() and obj.prepend_idle_phase_to_moving():
                repaired += 1
        return repaired

    def repair_all_idle_moving_frame_groups(self) -> int:
        repaired = 0
        for obj in self.outfits:
            if obj.has_frame_groups() and obj.repair_idle_moving_frame_groups(repair_legacy_like_single_group=True):
                repaired += 1
        return repaired

    def max_sprite_id(self) -> int:
        value = 0
        for obj in self.iter_all():
            for sprite_id in obj.all_sprite_ids():
                if int(sprite_id) > value:
                    value = int(sprite_id)
        return value

    def sprite_reference_count(self) -> int:
        return sum(obj.num_sprites for obj in self.iter_all())

    def sprite_id_format_label(self) -> str:
        return self.sprite_id_format.label()

    def animation_profile_label(self) -> str:
        parts = ['GameIdleAnimations ON' if self.game_idle_animations else 'Legacy single-group DAT']
        if self.game_enhanced_animations:
            parts.append('GameEnhancedAnimations ON')
        return ' + '.join(parts)

    def to_bytes(
        self,
        *,
        sprite_id_format: Union[DatSpriteIdFormat, int, str, None] = None,
        game_idle_animations: Optional[bool] = None,
        game_enhanced_animations: Optional[bool] = None,
        client_version: Optional[int] = None,
    ) -> bytes:
        fmt = self.sprite_id_format if sprite_id_format is None else DatSpriteIdFormat.coerce(sprite_id_format)
        idle_enabled = self.game_idle_animations if game_idle_animations is None else bool(game_idle_animations)
        enhanced_enabled = self.game_enhanced_animations if game_enhanced_animations is None else bool(game_enhanced_animations)
        version = self.client_version if client_version is None else int(client_version)
        if fmt == DatSpriteIdFormat.U16 and self.max_sprite_id() > U16_MAX:
            raise ValueError(f'DAT contains sprite id {self.max_sprite_id()} > {U16_MAX}; save as U32 sprite references.')
        self.renumber_object_ids()
        for obj in self.iter_all():
            obj.normalize_sprite_ids()

        out = bytearray()
        out += struct.pack('<I', self.signature)
        out += struct.pack('<HHHH', self.item_max_id, self.outfit_count, self.effect_count, self.distance_count)

        for obj in self.items:
            out += self._serialize_object(obj, fmt, game_idle_animations=idle_enabled, game_enhanced_animations=enhanced_enabled, client_version=version)
        for obj in self.outfits:
            out += self._serialize_object(obj, fmt, game_idle_animations=idle_enabled, game_enhanced_animations=enhanced_enabled, client_version=version)
        for obj in self.effects:
            out += self._serialize_object(obj, fmt, game_idle_animations=idle_enabled, game_enhanced_animations=enhanced_enabled, client_version=version)
        for obj in self.distances:
            out += self._serialize_object(obj, fmt, game_idle_animations=idle_enabled, game_enhanced_animations=enhanced_enabled, client_version=version)

        self.sprite_id_format = fmt
        self.game_idle_animations = idle_enabled
        self.game_enhanced_animations = enhanced_enabled
        self.client_version = version
        return bytes(out)

    def save(
        self,
        path: str | Path,
        *,
        do_backup: bool = False,
        sprite_id_format: Union[DatSpriteIdFormat, int, str, None] = None,
        game_idle_animations: Optional[bool] = None,
        game_enhanced_animations: Optional[bool] = None,
        client_version: Optional[int] = None,
    ) -> None:
        data = self.to_bytes(
            sprite_id_format=sprite_id_format,
            game_idle_animations=game_idle_animations,
            game_enhanced_animations=game_enhanced_animations,
            client_version=client_version,
        )
        transactional_write_bytes(path, data, do_backup=do_backup)

        check = self.from_bytes(
            data,
            sprite_id_format=self.sprite_id_format,
            game_idle_animations=self.game_idle_animations,
            game_enhanced_animations=self.game_enhanced_animations,
            client_version=self.client_version,
        )
        if check.item_max_id != self.item_max_id or check.outfit_count != self.outfit_count or check.effect_count != self.effect_count or check.distance_count != self.distance_count:
            raise ValueError('DAT self-check failed after save: category counts changed after reopen.')

    @staticmethod
    def _serialize_legacy_group_tail(group: DatFrameGroup, sprite_id_format: Union[DatSpriteIdFormat, int, str], *, game_enhanced_animations: bool, client_version: int, default_duration_ms: int = DEFAULT_ENHANCED_FRAME_DURATION_MS) -> bytes:
        group.normalize_sprite_ids()
        out = bytearray()
        out += bytes((group.width & 0xFF, group.height & 0xFF))
        if group.width > 1 or group.height > 1:
            out.append(int(group.exact_size if group.exact_size is not None else max(group.width, group.height)) & 0xFF)
        out += bytes((group.blend_frames & 0xFF, group.xdiv & 0xFF, group.ydiv & 0xFF))
        if int(client_version) >= 755:
            out.append(group.zdiv & 0xFF)
        out.append(group.animation_length & 0xFF)
        if group.animation_length > 1 and game_enhanced_animations:
            animator = group.animator or DatAnimatorData.fixed_duration(group.animation_length, default_duration_ms)
            out += animator.to_bytes(group.animation_length, default_duration_ms=default_duration_ms)
        out += pack_sprite_ids(group.sprite_ids, sprite_id_format)
        return bytes(out)

    @staticmethod
    def _serialize_object(
        obj: DatObject,
        sprite_id_format: Union[DatSpriteIdFormat, int, str] = DatSpriteIdFormat.U16,
        *,
        game_idle_animations: bool = False,
        game_enhanced_animations: bool = False,
        client_version: int = 760,
    ) -> bytes:
        out = bytearray()
        for code, payload in obj.rebuild_flag_records():
            out.append(code & 0xFF)
            out += payload
        out.append(0xFF)

        default_duration_ms = default_enhanced_frame_duration_ms(obj.kind)

        if obj.kind == 'outfit' and game_idle_animations:
            if obj.has_frame_groups():
                repaired = copy.deepcopy(obj)
                repaired.repair_idle_moving_frame_groups(repair_legacy_like_single_group=True)
                groups = repaired.frame_groups
            else:
                # Do not write a single idle/default group for legacy 7.6 looktypes when
                # GameIdleAnimations is enabled. OTCv8 expects a walking-capable group, and
                # using the full legacy sequence as Moving preserves old walking without gaps.
                groups = obj.build_idle_moving_groups_from_legacy(use_all_legacy_phases_as_moving=True)
            if not groups:
                groups = [DatFrameGroup(group_type=int(FrameGroupType.DEFAULT), sprite_ids=[0])]
            if len(groups) > 255:
                raise ValueError(f'Creature #{obj.object_id} has too many frame groups: {len(groups)}')
            out.append(len(groups) & 0xFF)
            for group in groups:
                out.append(int(group.group_type) & 0xFF)
                out += Tibia76Dat._serialize_legacy_group_tail(
                    group,
                    sprite_id_format,
                    game_enhanced_animations=game_enhanced_animations,
                    client_version=client_version,
                    default_duration_ms=default_duration_ms,
                )
            return bytes(out)

        if obj.has_frame_groups():
            fallback = copy.deepcopy(obj)
            fallback.convert_frame_groups_to_legacy()
            legacy_group = fallback.legacy_frame_group()
        else:
            legacy_group = obj.legacy_frame_group()
        out += Tibia76Dat._serialize_legacy_group_tail(
            legacy_group,
            sprite_id_format,
            game_enhanced_animations=game_enhanced_animations,
            client_version=client_version,
            default_duration_ms=default_duration_ms,
        )
        return bytes(out)
