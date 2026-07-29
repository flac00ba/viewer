from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

from PIL import Image

from editor_shared import transactional_write_bytes
from tibia76_formats import SprCountFormat, U16_MAX, write_sprite_count

SPRITE_SIZE = 32
PIXEL_COUNT = SPRITE_SIZE * SPRITE_SIZE
DEFAULT_KEY_COLOR = (248, 248, 240)


@dataclass
class SpriteEntry:
    key_color: Tuple[int, int, int]
    rgba: bytes  # 32 * 32 * 4 bytes

    def to_image(self) -> Image.Image:
        return Image.frombytes('RGBA', (SPRITE_SIZE, SPRITE_SIZE), self.rgba)

    @classmethod
    def from_image(
        cls,
        image: Image.Image,
        *,
        key_color: Tuple[int, int, int] = DEFAULT_KEY_COLOR,
        fit_mode: str = 'strict',
    ) -> 'SpriteEntry':
        image = image.convert('RGBA')
        if fit_mode == 'strict':
            if image.size != (SPRITE_SIZE, SPRITE_SIZE):
                raise ValueError('Image must be exactly 32x32 in strict mode.')
            canvas = image
        elif fit_mode == 'fit':
            if image.width > SPRITE_SIZE or image.height > SPRITE_SIZE:
                raise ValueError('Image is larger than 32x32; crop or scale it first.')
            canvas = Image.new('RGBA', (SPRITE_SIZE, SPRITE_SIZE), (0, 0, 0, 0))
            x = (SPRITE_SIZE - image.width) // 2
            y = (SPRITE_SIZE - image.height) // 2
            canvas.alpha_composite(image, (x, y))
        else:
            raise ValueError("fit_mode must be 'strict' or 'fit'")
        return cls(key_color=key_color, rgba=canvas.tobytes())

    @classmethod
    def transparent(cls, *, key_color: Tuple[int, int, int] = DEFAULT_KEY_COLOR) -> 'SpriteEntry':
        return cls(key_color=key_color, rgba=bytes(PIXEL_COUNT * 4))

    def is_fully_transparent(self) -> bool:
        data = self.rgba
        return all(data[i] == 0 for i in range(3, len(data), 4))


@dataclass
class SprLayoutInfo:
    signature: int
    count: int
    count_format: SprCountFormat
    offset_table_start: int
    offset_table_end: int
    offsets: List[int]
    warnings: List[str]


class Tibia76Spr:
    """
    Reader/writer for Tibia 7.6-style .spr files.

    U16 layout:
      [4] uint32 LE signature
      [2] uint16 LE sprite_count
      [sprite_count * 4] uint32 LE offsets

    U32/GameSpritesU32 layout:
      [4] uint32 LE signature
      [4] uint32 LE sprite_count
      [sprite_count * 4] uint32 LE offsets

    Sprite blocks are unchanged in both formats.
    """

    def __init__(self) -> None:
        self.signature: int = 0
        self.entries: List[Optional[SpriteEntry]] = []
        self.count_format: SprCountFormat = SprCountFormat.U16
        self.format_warnings: List[str] = []

    @staticmethod
    def _decode_sprite_block(blob: bytes, offset: int) -> SpriteEntry:
        if offset + 5 > len(blob):
            raise ValueError('Sprite block header out of range')

        key = (blob[offset], blob[offset + 1], blob[offset + 2])
        payload_size = struct.unpack_from('<H', blob, offset + 3)[0]
        p = offset + 5
        end = p + payload_size
        if end > len(blob):
            raise ValueError('Sprite payload out of range')

        rgba = bytearray(b'\x00' * (PIXEL_COUNT * 4))
        pixel_pos = 0

        while p < end:
            if p + 4 > end:
                raise ValueError('Corrupted sprite payload header')

            transparent_run = struct.unpack_from('<H', blob, p)[0]
            colored_run = struct.unpack_from('<H', blob, p + 2)[0]
            p += 4
            pixel_pos += transparent_run
            if pixel_pos > PIXEL_COUNT:
                raise ValueError('Sprite decode transparent-run overflow')

            for _ in range(colored_run):
                if p + 3 > end:
                    raise ValueError('Corrupted sprite pixel data')
                if pixel_pos >= PIXEL_COUNT:
                    raise ValueError('Sprite decode overflow')
                base = pixel_pos * 4
                rgba[base + 0] = blob[p + 0]
                rgba[base + 1] = blob[p + 1]
                rgba[base + 2] = blob[p + 2]
                rgba[base + 3] = 255
                p += 3
                pixel_pos += 1

        if p != end:
            raise ValueError('Sprite payload size mismatch')

        return SpriteEntry(key_color=key, rgba=bytes(rgba))

    @staticmethod
    def _sprite_block_end(blob: bytes, offset: int) -> int:
        if offset == 0:
            return 0
        if offset + 5 > len(blob):
            raise ValueError('Sprite block header out of range')
        payload_size = struct.unpack_from('<H', blob, offset + 3)[0]
        end = offset + 5 + payload_size
        if end > len(blob):
            raise ValueError('Sprite payload out of range')
        return end

    @staticmethod
    def _encode_sprite_payload(rgba: bytes) -> bytes:
        if len(rgba) != PIXEL_COUNT * 4:
            raise ValueError('Expected 32x32 RGBA data')

        payload = bytearray()
        pos = 0
        while pos < PIXEL_COUNT:
            transparent_run = 0
            while pos < PIXEL_COUNT and rgba[pos * 4 + 3] == 0:
                transparent_run += 1
                pos += 1

            if pos >= PIXEL_COUNT:
                break

            colored_start = pos
            colored_run = 0
            while pos < PIXEL_COUNT and rgba[pos * 4 + 3] != 0:
                colored_run += 1
                pos += 1

            payload += struct.pack('<HH', transparent_run, colored_run)
            for i in range(colored_start, colored_start + colored_run):
                base = i * 4
                payload += rgba[base:base + 3]

        if len(payload) > U16_MAX:
            raise ValueError('Compressed sprite payload exceeds uint16 size')
        return bytes(payload)

    @classmethod
    def analyze_layout(cls, blob: bytes, count_format: Union[SprCountFormat, int, str]) -> SprLayoutInfo:
        fmt = SprCountFormat.coerce(count_format)
        min_size = 8 if fmt == SprCountFormat.U32 else 6
        if len(blob) < min_size:
            raise ValueError('File too small to be a valid .spr')
        signature = struct.unpack_from('<I', blob, 0)[0]
        if fmt == SprCountFormat.U32:
            count = struct.unpack_from('<I', blob, 4)[0]
            table_start = 8
        else:
            count = struct.unpack_from('<H', blob, 4)[0]
            table_start = 6
        table_end = table_start + count * 4
        if table_end > len(blob):
            raise ValueError('Offset table exceeds file size')

        offsets = [struct.unpack_from('<I', blob, table_start + i * 4)[0] for i in range(count)]
        warnings: List[str] = []
        nonzero_offsets = [offset for offset in offsets if offset != 0]
        if any(offset < table_end or offset >= len(blob) for offset in nonzero_offsets):
            raise ValueError('Offset table contains offsets outside sprite data range')
        if nonzero_offsets != sorted(nonzero_offsets):
            warnings.append('non-zero offsets are not sorted')
        if len(set(nonzero_offsets)) != len(nonzero_offsets):
            warnings.append('duplicate non-zero offsets detected')
        for offset in nonzero_offsets:
            cls._sprite_block_end(blob, offset)
        return SprLayoutInfo(
            signature=signature,
            count=count,
            count_format=fmt,
            offset_table_start=table_start,
            offset_table_end=table_end,
            offsets=offsets,
            warnings=warnings,
        )

    @classmethod
    def detect_count_format(cls, blob: bytes) -> SprCountFormat:
        candidates: List[SprLayoutInfo] = []
        for fmt in (SprCountFormat.U16, SprCountFormat.U32):
            try:
                candidates.append(cls.analyze_layout(blob, fmt))
            except Exception:
                pass
        if not candidates:
            raise ValueError('Could not detect SPR format as U16 or U32')
        if len(candidates) == 1:
            return candidates[0].count_format
        # Classic converter/editor default: ambiguous small files stay legacy U16.
        for candidate in candidates:
            if candidate.count_format == SprCountFormat.U16:
                return candidate.count_format
        return candidates[0].count_format

    @classmethod
    def from_bytes(cls, blob: bytes, *, count_format: Union[SprCountFormat, int, str] = 'auto') -> 'Tibia76Spr':
        if str(count_format).strip().lower() == 'auto':
            fmt = cls.detect_count_format(blob)
        else:
            fmt = SprCountFormat.coerce(count_format)
        layout = cls.analyze_layout(blob, fmt)
        obj = cls()
        obj.signature = layout.signature
        obj.count_format = layout.count_format
        obj.format_warnings = list(layout.warnings)
        obj.entries = [None] * layout.count
        for i, offset in enumerate(layout.offsets):
            if offset == 0:
                continue
            obj.entries[i] = cls._decode_sprite_block(blob, offset)
        return obj

    @classmethod
    def load(cls, path: str | Path, *, count_format: Union[SprCountFormat, int, str] = 'auto') -> 'Tibia76Spr':
        return cls.from_bytes(Path(path).read_bytes(), count_format=count_format)

    def to_bytes(self, *, count_format: Union[SprCountFormat, int, str, None] = None) -> bytes:
        fmt = self.count_format if count_format is None else SprCountFormat.coerce(count_format)
        if len(self.entries) > U16_MAX:
            fmt = SprCountFormat.U32

        out = bytearray()
        out += struct.pack('<I', self.signature)
        write_sprite_count(out, len(self.entries), fmt)
        offset_table_start = len(out)
        out += b'\x00' * (len(self.entries) * 4)

        offsets: List[int] = []
        for entry in self.entries:
            if entry is None:
                offsets.append(0)
                continue
            payload = self._encode_sprite_payload(entry.rgba)
            offsets.append(len(out))
            out += bytes(entry.key_color)
            out += struct.pack('<H', len(payload))
            out += payload

        for i, offset in enumerate(offsets):
            struct.pack_into('<I', out, offset_table_start + i * 4, offset)

        self.count_format = fmt
        return bytes(out)

    def save(self, path: str | Path, *, do_backup: bool = False, count_format: Union[SprCountFormat, int, str, None] = None) -> None:
        transactional_write_bytes(path, self.to_bytes(count_format=count_format), do_backup=do_backup)

    def sprite_count(self) -> int:
        return len(self.entries)

    def count_format_label(self) -> str:
        return self.count_format.label()

    def empty_count(self) -> int:
        return sum(1 for entry in self.entries if entry is None)

    def default_key_color(self) -> Tuple[int, int, int]:
        for entry in self.entries:
            if entry is not None:
                return entry.key_color
        return DEFAULT_KEY_COLOR

    def get(self, sprite_id: int) -> Optional[SpriteEntry]:
        if sprite_id < 1 or sprite_id > len(self.entries):
            raise IndexError('sprite_id out of range')
        return self.entries[sprite_id - 1]

    def set(self, sprite_id: int, entry: Optional[SpriteEntry]) -> None:
        if sprite_id < 1 or sprite_id > len(self.entries):
            raise IndexError('sprite_id out of range')
        self.entries[sprite_id - 1] = entry

    def ensure_entry(self, sprite_id: int) -> SpriteEntry:
        entry = self.get(sprite_id)
        if entry is None:
            entry = SpriteEntry.transparent(key_color=self.default_key_color())
            self.set(sprite_id, entry)
        return entry

    def append(self, entry: Optional[SpriteEntry]) -> int:
        self.entries.append(entry)
        if len(self.entries) > U16_MAX:
            self.count_format = SprCountFormat.U32
        return len(self.entries)

    def find_first_empty_slot(self) -> Optional[int]:
        for index, entry in enumerate(self.entries, start=1):
            if entry is None:
                return index
        return None

    def append_into_first_free_slot(self, entry: Optional[SpriteEntry]) -> int:
        slot = self.find_first_empty_slot()
        if slot is not None:
            self.set(slot, entry)
            return slot
        return self.append(entry)

    def extract_png(self, sprite_id: int, out_path: str | Path) -> None:
        entry = self.get(sprite_id)
        if entry is None:
            raise ValueError(f'Sprite {sprite_id} is empty')
        entry.to_image().save(out_path)

    def replace_from_image(
        self,
        sprite_id: int,
        image: Image.Image,
        *,
        key_color: Optional[Tuple[int, int, int]] = None,
        fit_mode: str = 'strict',
    ) -> None:
        current = self.get(sprite_id)
        if key_color is None:
            key_color = current.key_color if current is not None else self.default_key_color()
        self.set(sprite_id, SpriteEntry.from_image(image, key_color=key_color, fit_mode=fit_mode))

    def append_from_image(
        self,
        image: Image.Image,
        *,
        key_color: Optional[Tuple[int, int, int]] = None,
        fit_mode: str = 'strict',
    ) -> int:
        if key_color is None:
            key_color = self.default_key_color()
        return self.append(SpriteEntry.from_image(image, key_color=key_color, fit_mode=fit_mode))
