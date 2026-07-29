#!/usr/bin/env python3
"""Convert YurOTS client/map files into compact, streamable web assets."""

from __future__ import annotations

import argparse
import colorsys
import gzip
import hashlib
import json
import math
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, features

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TOOLS_DIR.parent
VENDOR_DIR = TOOLS_DIR / "vendor"
sys.path.insert(0, str(VENDOR_DIR))
sys.path.insert(0, str(TOOLS_DIR))

from chunk_format import ENTRY_CREATURE, ENTRY_ITEM, ChunkEntry, ChunkTile, encode_gzip
import otbm
from tibia76_dat import DatFrameGroup, DatObject, Tibia76Dat
from tibia76_otb import Tibia76Otb
from tibia76_spr import Tibia76Spr

SPRITE_SIZE = 32
DEFAULT_ITEM_FRAME_MS = 500
DEFAULT_CREATURE_FRAME_MS = 250


@dataclass(slots=True)
class CreatureLook:
    name: str
    look_type: int
    look_item: int
    look_mount: int
    look_addon: int
    head: int
    body: int
    legs: int
    feet: int


@dataclass(slots=True)
class SpawnCreature:
    name: str
    x: int
    y: int
    z: int
    direction: int


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("viewer.config.json must contain a JSON object")
    return value


def _resolve(config_path: Path, value: str) -> Path:
    return (config_path.parent / value).resolve()


def _validate_generated_path(path: Path, final_path: Path) -> Path:
    resolved = path.resolve()
    final = final_path.resolve()
    project = PROJECT_DIR.resolve()
    allowed = {
        final,
        final.with_name(f".{final.name}.building"),
        final.with_name(f".{final.name}.previous"),
    }
    if project not in final.parents or final == project or final.name != "assets" or resolved not in allowed:
        raise ValueError(f"Refusing unsafe generated-data path: {resolved}")
    return resolved


def _safe_reset_working_output(path: Path, final_path: Path) -> None:
    resolved = _validate_generated_path(path, final_path)
    expected = final_path.resolve().with_name(f".{final_path.name}.building")
    if resolved != expected:
        raise ValueError(f"Refusing to build outside the staging directory: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def _publish_working_output(working_path: Path, final_path: Path) -> None:
    working = _validate_generated_path(working_path, final_path)
    final = _validate_generated_path(final_path, final_path)
    previous = _validate_generated_path(final.with_name(f".{final.name}.previous"), final_path)
    if not (working / "manifest.json").is_file():
        raise ValueError("Refusing to publish generated data without manifest.json")

    if previous.exists():
        shutil.rmtree(previous)
    if final.exists():
        final.rename(previous)
    try:
        working.rename(final)
    except Exception:
        if previous.exists() and not final.exists():
            previous.rename(final)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def _xml_int(element: ET.Element, name: str, default: int = 0) -> int:
    try:
        return int(element.attrib.get(name, default))
    except (TypeError, ValueError):
        return default


def load_creature_looks(path: Path) -> dict[str, CreatureLook]:
    root = ET.parse(path).getroot()
    result: dict[str, CreatureLook] = {}
    for node in root:
        if node.tag.lower() != "creature":
            continue
        name = node.attrib.get("name", "").strip()
        if not name:
            continue
        key = name.casefold()
        result.setdefault(
            key,
            CreatureLook(
                name=name,
                look_type=_xml_int(node, "looktype"),
                look_item=_xml_int(node, "lookitem"),
                look_mount=_xml_int(node, "lookmount"),
                look_addon=_xml_int(node, "lookaddon"),
                head=_xml_int(node, "lookhead"),
                body=_xml_int(node, "lookbody"),
                legs=_xml_int(node, "looklegs"),
                feet=_xml_int(node, "lookfeet"),
            ),
        )
    return result


def load_spawns(path: Path) -> list[SpawnCreature]:
    if not path.exists():
        return []
    root = ET.parse(path).getroot()
    result: list[SpawnCreature] = []
    for spawn in root:
        if spawn.tag.lower() != "spawn":
            continue
        center_x = _xml_int(spawn, "centerx")
        center_y = _xml_int(spawn, "centery")
        center_z = _xml_int(spawn, "centerz")
        for creature in spawn:
            if creature.tag.lower() not in ("monster", "npc"):
                continue
            name = creature.attrib.get("name", "").strip()
            if not name:
                continue
            result.append(
                SpawnCreature(
                    name=name,
                    x=center_x + _xml_int(creature, "x"),
                    y=center_y + _xml_int(creature, "y"),
                    z=center_z,
                    direction=_xml_int(creature, "direction", 2) & 0xFF,
                )
            )
    return result


def _dat_item(dat: Tibia76Dat, client_id: int) -> DatObject | None:
    index = int(client_id) - 100
    return dat.items[index] if 0 <= index < len(dat.items) else None


def _dat_outfit(dat: Tibia76Dat, look_type: int) -> DatObject | None:
    index = int(look_type) - 1
    return dat.outfits[index] if 0 <= index < len(dat.outfits) else None


def _is_countable(otb_item: Any, dat: Tibia76Dat) -> bool:
    thing = _dat_item(dat, otb_item.client_id)
    flags = thing.bool_flags if thing is not None else {}
    return (
        otb_item.flag_enabled("stackable")
        or otb_item.group in (11, 12)
        or bool(flags.get("stackable"))
        or bool(flags.get("fluid"))
        or bool(flags.get("fluid_container"))
    )


def _all_sprite_ids(thing: DatObject) -> Iterable[int]:
    return (int(sprite_id) for sprite_id in thing.all_sprite_ids() if int(sprite_id) > 0)


def _duration_list(group: DatFrameGroup, fallback_ms: int) -> list[int]:
    count = max(1, int(group.animation_length))
    animator = group.animator
    if animator is None or not animator.phase_durations:
        return [fallback_ms] * count
    durations: list[int] = []
    for index in range(count):
        low, high = animator.phase_durations[min(index, len(animator.phase_durations) - 1)]
        value = (int(low) + int(high)) // 2 if low and high else int(low or high or fallback_ms)
        durations.append(max(1, value))
    return durations


def _frame_group_payload(group: DatFrameGroup, sprite_refs: dict[int, int], fallback_ms: int) -> dict[str, Any]:
    animator = group.animator
    return {
        "type": int(group.group_type),
        "width": max(1, int(group.width)),
        "height": max(1, int(group.height)),
        "layers": max(1, int(group.blend_frames)),
        "patternX": max(1, int(group.xdiv)),
        "patternY": max(1, int(group.ydiv)),
        "patternZ": max(1, int(group.zdiv)),
        "frames": max(1, int(group.animation_length)),
        "durations": _duration_list(group, fallback_ms),
        "async": bool(animator.async_animation) if animator else False,
        "loop": int(animator.loop_count) if animator else 0,
        "sprites": [sprite_refs.get(int(sprite_id), -1) for sprite_id in group.sprite_ids],
    }


def _thing_payload(
    thing: DatObject,
    sprite_refs: dict[int, int],
    *,
    fallback_ms: int,
    category: str,
) -> dict[str, Any]:
    groups = thing.frame_groups if thing.has_frame_groups() else [thing.legacy_frame_group()]
    flags = thing.bool_flags
    return {
        "category": category,
        "groups": [_frame_group_payload(group, sprite_refs, fallback_ms) for group in groups],
        "offsetX": int(thing.offset_x or 0),
        "offsetY": int(thing.offset_y or 0),
        "elevation": int(thing.elevation or 0),
        "minimap": int(thing.mini_map_color) if thing.mini_map_color is not None else -1,
        "flags": {
            "ground": bool(flags.get("ground") or flags.get("full_ground")),
            "border": bool(flags.get("ground_border")),
            "bottom": bool(flags.get("on_bottom")),
            "top": bool(flags.get("on_top")),
            "stackable": bool(flags.get("stackable")),
            "fluid": bool(flags.get("fluid") or flags.get("fluid_container")),
            "splash": bool(flags.get("fluid")),
            "hangable": bool(flags.get("hangable")),
            "hookSouth": bool(flags.get("vertical")),
            "hookEast": bool(flags.get("horizontal")),
            "hasElevation": bool(flags.get("has_elevation")),
            "animateAlways": bool(flags.get("animate_always")),
        },
    }


def _rgba_average(rgba: bytes) -> tuple[int, int, int] | None:
    red = green = blue = count = 0
    for index in range(0, len(rgba), 4):
        if rgba[index + 3] == 0:
            continue
        red += rgba[index]
        green += rgba[index + 1]
        blue += rgba[index + 2]
        count += 1
    if not count:
        return None
    return red // count, green // count, blue // count


def build_atlases(
    spr_path: Path,
    used_sprite_ids: set[int],
    output: Path,
    atlas_size: int,
) -> tuple[dict[int, int], list[str], dict[int, tuple[int, int, int]]]:
    if atlas_size % SPRITE_SIZE:
        raise ValueError("atlasSize must be divisible by 32")
    spr_blob = spr_path.read_bytes()
    count_format = Tibia76Spr.detect_count_format(spr_blob)
    layout = Tibia76Spr.analyze_layout(spr_blob, count_format)
    cells_per_row = atlas_size // SPRITE_SIZE
    page_capacity = cells_per_row * cells_per_row
    output.mkdir(parents=True, exist_ok=True)

    sprite_refs: dict[int, int] = {}
    sprite_averages: dict[int, tuple[int, int, int]] = {}
    digest_to_ref: dict[bytes, int] = {}
    pages: list[str] = []
    page: Image.Image | None = None
    unique_count = 0

    def save_page(image: Image.Image, page_index: int) -> None:
        name = f"atlas-{page_index:03d}.webp" if features.check("webp") else f"atlas-{page_index:03d}.png"
        destination = output / name
        if destination.suffix == ".webp":
            image.save(destination, "WEBP", lossless=True, quality=100, method=6, exact=True)
        else:
            image.save(destination, "PNG", optimize=True)
        pages.append(f"atlases/{name}")

    for sprite_id in sorted(used_sprite_ids):
        if sprite_id < 1 or sprite_id > layout.count:
            sprite_refs[sprite_id] = -1
            continue
        offset = layout.offsets[sprite_id - 1]
        if offset == 0:
            sprite_refs[sprite_id] = -1
            continue
        entry = Tibia76Spr._decode_sprite_block(spr_blob, offset)
        average = _rgba_average(entry.rgba)
        if average is None:
            sprite_refs[sprite_id] = -1
            continue
        sprite_averages[sprite_id] = average
        digest = hashlib.blake2b(entry.rgba, digest_size=16).digest()
        existing = digest_to_ref.get(digest)
        if existing is not None:
            sprite_refs[sprite_id] = existing
            continue

        page_index = unique_count // page_capacity
        cell_index = unique_count % page_capacity
        if cell_index == 0:
            if page is not None:
                save_page(page, page_index - 1)
                page.close()
            page = Image.new("RGBA", (atlas_size, atlas_size), (0, 0, 0, 0))
        assert page is not None
        cell_x = (cell_index % cells_per_row) * SPRITE_SIZE
        cell_y = (cell_index // cells_per_row) * SPRITE_SIZE
        page.alpha_composite(entry.to_image(), (cell_x, cell_y))
        sprite_refs[sprite_id] = unique_count
        digest_to_ref[digest] = unique_count
        unique_count += 1

    if page is not None:
        save_page(page, (unique_count - 1) // page_capacity)
        page.close()

    return sprite_refs, pages, sprite_averages


class AtlasSpriteReader:
    def __init__(self, root: Path, pages: list[str], atlas_size: int):
        self.root = root
        self.pages = pages
        self.atlas_size = atlas_size
        self.cells_per_row = atlas_size // SPRITE_SIZE
        self.capacity = self.cells_per_row * self.cells_per_row
        self._page_images: dict[int, Image.Image] = {}
        self._scaled_sprites: dict[tuple[int, int], Image.Image] = {}

    def sprite(self, reference: int, size: int) -> Image.Image | None:
        if reference < 0:
            return None
        key = (reference, size)
        cached = self._scaled_sprites.get(key)
        if cached is not None:
            return cached
        page_index = reference // self.capacity
        if not 0 <= page_index < len(self.pages):
            return None
        page = self._page_images.get(page_index)
        if page is None:
            with Image.open(self.root / self.pages[page_index]) as source:
                page = source.convert("RGBA")
            self._page_images[page_index] = page
        cell = reference % self.capacity
        left = (cell % self.cells_per_row) * SPRITE_SIZE
        top = (cell // self.cells_per_row) * SPRITE_SIZE
        image = page.crop((left, top, left + SPRITE_SIZE, top + SPRITE_SIZE))
        if size != SPRITE_SIZE:
            image = image.resize((size, size), Image.Resampling.NEAREST)
        self._scaled_sprites[key] = image
        return image

    def close(self) -> None:
        for image in self._scaled_sprites.values():
            image.close()
        for page in self._page_images.values():
            page.close()
        self._scaled_sprites.clear()
        self._page_images.clear()


def _overview_item_parts(
    definition: dict[str, Any],
    tile_x: int,
    tile_y: int,
    entry: ChunkEntry,
    hook_side: str,
) -> list[tuple[int, int, int]]:
    groups = definition.get("groups") or []
    if not groups:
        return []
    group = groups[0]
    width = max(1, int(group.get("width", 1)))
    height = max(1, int(group.get("height", 1)))
    layers = max(1, int(group.get("layers", 1)))
    pattern_count_x = max(1, int(group.get("patternX", 1)))
    pattern_count_y = max(1, int(group.get("patternY", 1)))
    sprites = group.get("sprites") or []
    if not sprites:
        return []

    flags = definition.get("flags") or {}
    pattern_x = tile_x % pattern_count_x
    pattern_y = tile_y % pattern_count_y
    subtype = -1
    if flags.get("fluid"):
        subtype = max(0, int(entry.value))
    elif flags.get("hangable"):
        if hook_side == "south" and pattern_count_x > 1:
            pattern_x = 1
        elif hook_side == "east" and pattern_count_x > 2:
            pattern_x = 2
        else:
            pattern_x = 0
        pattern_y = 0
    elif flags.get("stackable") and pattern_count_x == 4 and pattern_count_y == 2:
        count = max(0, int(entry.value))
        pattern_x = count - 1 if 0 < count < 5 else 0 if count < 10 else 1 if count < 25 else 2 if count < 50 else 3
        pattern_y = 0 if count < 5 else 1

    result: list[tuple[int, int, int]] = []
    sprite_count = len(sprites)
    for sprite_x in range(width):
        for sprite_y in range(height):
            for layer in range(layers):
                if subtype >= 0 and width <= 1 and height <= 1:
                    index = subtype
                else:
                    index = (
                        ((((pattern_y * pattern_count_x + pattern_x) * layers + layer) * height + sprite_y) * width)
                        + sprite_x
                    )
                reference = int(sprites[index % sprite_count])
                if reference >= 0:
                    result.append((reference, -sprite_x, -sprite_y))
    return result


def _draw_overview_item(
    canvas: Image.Image,
    sprite_reader: AtlasSpriteReader,
    definition: dict[str, Any],
    tile_x: int,
    tile_y: int,
    entry: ChunkEntry,
    hook_side: str,
    cursor: list[int],
    pixels_per_tile: int,
) -> None:
    scale = pixels_per_tile / SPRITE_SIZE
    base_x = cursor[0] - round(int(definition.get("offsetX", 0)) * scale)
    base_y = cursor[1] - round(int(definition.get("offsetY", 0)) * scale)
    for reference, sprite_x, sprite_y in _overview_item_parts(definition, tile_x, tile_y, entry, hook_side):
        sprite = sprite_reader.sprite(reference, pixels_per_tile)
        if sprite is not None:
            canvas.alpha_composite(
                sprite,
                (
                    base_x + sprite_x * pixels_per_tile,
                    base_y + sprite_y * pixels_per_tile,
                ),
            )


def build_sprite_overviews(
    output_path: Path,
    chunks: dict[tuple[int, int, int], list[ChunkTile]],
    items_payload: dict[str, Any],
    atlas_pages: list[str],
    atlas_size: int,
    chunk_size: int,
    pixels_per_tile: int,
) -> None:
    if not 1 <= pixels_per_tile <= SPRITE_SIZE:
        raise ValueError("overviewPixelsPerTile must be between 1 and 32")
    margin_tiles = 8
    margin_pixels = margin_tiles * pixels_per_tile
    output_pixels = chunk_size * pixels_per_tile
    canvas_pixels = output_pixels + margin_pixels * 2
    sprite_reader = AtlasSpriteReader(output_path, atlas_pages, atlas_size)
    try:
        chunk_keys = sorted(chunks)
        for overview_index, (floor, chunk_x, chunk_y) in enumerate(chunk_keys, start=1):
            if overview_index == 1 or overview_index % 50 == 0 or overview_index == len(chunk_keys):
                print(f"      overview {overview_index:,}/{len(chunk_keys):,}")
            world_left = chunk_x * chunk_size - margin_tiles
            world_top = chunk_y * chunk_size - margin_tiles
            world_right = (chunk_x + 1) * chunk_size + margin_tiles - 1
            world_bottom = (chunk_y + 1) * chunk_size + margin_tiles - 1
            nearby: list[tuple[int, int, ChunkTile]] = []
            for neighbour_y in range(chunk_y - 1, chunk_y + 2):
                for neighbour_x in range(chunk_x - 1, chunk_x + 2):
                    for tile in chunks.get((floor, neighbour_x, neighbour_y), ()):
                        absolute_x = neighbour_x * chunk_size + tile.local_x
                        absolute_y = neighbour_y * chunk_size + tile.local_y
                        if world_left <= absolute_x <= world_right and world_top <= absolute_y <= world_bottom:
                            nearby.append((absolute_x, absolute_y, tile))

            canvas = Image.new("RGBA", (canvas_pixels, canvas_pixels), (0, 0, 0, 0))
            for tile_x, tile_y, tile in sorted(nearby, key=lambda value: (value[1], value[0])):
                cursor = [
                    (tile_x - world_left) * pixels_per_tile,
                    (tile_y - world_top) * pixels_per_tile,
                ]
                item_entries = [entry for entry in tile.entries if entry.kind == ENTRY_ITEM]
                resolved = [
                    (entry, items_payload.get(str(entry.identifier)))
                    for entry in item_entries
                    if items_payload.get(str(entry.identifier)) is not None
                ]
                ground = [(entry, definition) for entry, definition in resolved if definition["flags"].get("ground")]
                border = [
                    (entry, definition)
                    for entry, definition in resolved
                    if not definition["flags"].get("ground") and definition["flags"].get("border")
                ]
                common = [
                    (entry, definition)
                    for entry, definition in resolved
                    if not definition["flags"].get("ground") and not definition["flags"].get("border")
                ]
                hook_definition = next(
                    (
                        definition
                        for _, definition in resolved
                        if definition["flags"].get("hookSouth") or definition["flags"].get("hookEast")
                    ),
                    None,
                )
                hook_side = (
                    "south"
                    if hook_definition and hook_definition["flags"].get("hookSouth")
                    else "east"
                    if hook_definition and hook_definition["flags"].get("hookEast")
                    else "none"
                )
                for entry, definition in ground:
                    _draw_overview_item(
                        canvas, sprite_reader, definition, tile_x, tile_y, entry, hook_side, cursor, pixels_per_tile
                    )
                for entry, definition in (*border, *common):
                    _draw_overview_item(
                        canvas, sprite_reader, definition, tile_x, tile_y, entry, hook_side, cursor, pixels_per_tile
                    )
                    if definition["flags"].get("hasElevation") and int(definition.get("elevation", 0)) > 0:
                        elevation = round(int(definition["elevation"]) * pixels_per_tile / SPRITE_SIZE)
                        cursor[0] -= elevation
                        cursor[1] -= elevation

            cropped = canvas.crop(
                (
                    margin_pixels,
                    margin_pixels,
                    margin_pixels + output_pixels,
                    margin_pixels + output_pixels,
                )
            )
            destination = output_path / "overview" / str(floor) / f"{chunk_x}_{chunk_y}.webp"
            destination.parent.mkdir(parents=True, exist_ok=True)
            cropped.save(destination, "WEBP", lossless=True, quality=100, method=4, exact=True)
            cropped.close()
            canvas.close()
    finally:
        sprite_reader.close()


def _minimap_color(value: int) -> tuple[int, int, int] | None:
    if value < 0:
        return None
    index = value & 0xFF
    hue = ((index * 11) % 360) / 360.0
    saturation = (120 + index % 80) / 255.0
    brightness = (140 + index % 90) / 255.0
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, brightness)
    return int(red * 255), int(green * 255), int(blue * 255)


def _representative_color(
    thing: DatObject | None,
    sprite_averages: dict[int, tuple[int, int, int]],
) -> tuple[int, int, int]:
    if thing is None:
        return 72, 79, 87
    color = _minimap_color(int(thing.mini_map_color) if thing.mini_map_color is not None else -1)
    if color is not None:
        return color
    for sprite_id in _all_sprite_ids(thing):
        average = sprite_averages.get(sprite_id)
        if average is not None:
            return average
    return 72, 79, 87


def _gzip_json(path: Path, value: Any) -> None:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


def _bounds_for_tiles(tiles: Iterable[otbm.Tile]) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for tile in tiles:
        bounds = result.setdefault(tile.z, [tile.x, tile.y, tile.x, tile.y])
        bounds[0] = min(bounds[0], tile.x)
        bounds[1] = min(bounds[1], tile.y)
        bounds[2] = max(bounds[2], tile.x)
        bounds[3] = max(bounds[3], tile.y)
    return result


def _initial_position(
    config: dict[str, Any],
    floor_counts: Counter[int],
    chunks: dict[tuple[int, int, int], list[ChunkTile]],
    chunk_size: int,
) -> dict[str, int]:
    configured = config.get("initialPosition")
    if isinstance(configured, dict) and all(key in configured for key in ("x", "y", "z")):
        return {key: int(configured[key]) for key in ("x", "y", "z")}
    floor = floor_counts.most_common(1)[0][0]
    (floor, chunk_x, chunk_y), tiles = max(
        ((key, tiles) for key, tiles in chunks.items() if key[0] == floor),
        key=lambda entry: len(entry[1]),
    )
    average_x = sum(tile.local_x for tile in tiles) // max(1, len(tiles))
    average_y = sum(tile.local_y for tile in tiles) // max(1, len(tiles))
    return {
        "x": chunk_x * chunk_size + average_x,
        "y": chunk_y * chunk_size + average_y,
        "z": floor,
    }


def build(config_path: Path) -> None:
    config = _read_json(config_path)
    paths = config.get("paths", {})
    required = ("map", "otb", "dat", "spr", "creatures")
    missing = [name for name in required if not isinstance(paths.get(name), str)]
    if missing:
        raise ValueError(f"Missing paths in config: {', '.join(missing)}")

    map_path = _resolve(config_path, paths["map"])
    otb_path = _resolve(config_path, paths["otb"])
    dat_path = _resolve(config_path, paths["dat"])
    spr_path = _resolve(config_path, paths["spr"])
    creatures_path = _resolve(config_path, paths["creatures"])
    final_output_path = _resolve(config_path, config.get("output", "docs/assets"))
    output_path = final_output_path.with_name(f".{final_output_path.name}.building")
    _safe_reset_working_output(output_path, final_output_path)

    for path in (map_path, otb_path, dat_path, spr_path, creatures_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    print("[1/7] Loading OTB and DAT definitions...")
    otb_file = Tibia76Otb.load(otb_path)
    dat_file = Tibia76Dat.load(
        dat_path,
        sprite_id_format=config.get("dat", {}).get("spriteIdFormat", "auto"),
        game_idle_animations=config.get("dat", {}).get("idleAnimations", "auto"),
        game_enhanced_animations=config.get("dat", {}).get("enhancedAnimations", "auto"),
        client_version=int(config.get("dat", {}).get("clientVersion", 760)),
    )
    otb_by_server = {int(item.server_id): item for item in otb_file.items}
    countable_ids = {item.server_id for item in otb_file.items if _is_countable(item, dat_file)}

    print("[2/7] Parsing OTBM map and spawn XML...")
    world = otbm.load(map_path, countable_ids=countable_ids)
    spawn_path_value = paths.get("spawns")
    spawn_path = _resolve(config_path, spawn_path_value) if isinstance(spawn_path_value, str) else map_path.with_name(world.spawn_file or f"{map_path.stem}-spawn.xml")
    spawns = load_spawns(spawn_path)
    creature_looks = load_creature_looks(creatures_path)

    used_server_ids = {item.server_id for tile in world.tiles for item in tile.items}
    used_looks: dict[str, CreatureLook] = {}
    missing_creatures: set[str] = set()
    for spawn in spawns:
        look = creature_looks.get(spawn.name.casefold())
        if look is None:
            missing_creatures.add(spawn.name)
        else:
            used_looks.setdefault(spawn.name.casefold(), look)

    item_things: dict[int, DatObject] = {}
    missing_items: list[int] = []
    for server_id in sorted(used_server_ids):
        otb_item = otb_by_server.get(server_id)
        thing = _dat_item(dat_file, otb_item.client_id) if otb_item is not None else None
        if thing is None:
            missing_items.append(server_id)
            continue
        item_things[server_id] = thing

    creature_things: dict[str, DatObject] = {}
    for key, look in used_looks.items():
        thing = _dat_outfit(dat_file, look.look_type) if look.look_type else None
        if thing is not None:
            creature_things[key] = thing

    used_sprite_ids: set[int] = set()
    for thing in item_things.values():
        used_sprite_ids.update(_all_sprite_ids(thing))
    for thing in creature_things.values():
        used_sprite_ids.update(_all_sprite_ids(thing))

    atlas_size = int(config.get("atlasSize", 1024))
    print(f"[3/7] Decoding {len(used_sprite_ids):,} used sprites and building atlases...")
    sprite_refs, atlas_pages, sprite_averages = build_atlases(
        spr_path,
        used_sprite_ids,
        output_path / "atlases",
        atlas_size,
    )

    print("[4/7] Building compact item and creature definitions...")
    items_payload: dict[str, Any] = {}
    item_colors: dict[int, tuple[int, int, int]] = {}
    for server_id, thing in item_things.items():
        otb_item = otb_by_server[server_id]
        payload = _thing_payload(thing, sprite_refs, fallback_ms=DEFAULT_ITEM_FRAME_MS, category="item")
        flags = payload["flags"]
        flags["ground"] = bool(flags["ground"] or otb_item.group == 1)
        flags["bottom"] = bool(flags["bottom"] or otb_item.group == 1)
        flags["top"] = bool(flags["top"] or otb_item.top_order or otb_item.group in (11, 12))
        flags["stackable"] = bool(flags["stackable"] or otb_item.flag_enabled("stackable"))
        flags["fluid"] = bool(flags["fluid"] or otb_item.group in (11, 12))
        flags["splash"] = bool(flags["splash"] or otb_item.group == 11)
        payload.update(
            {
                "serverId": server_id,
                "clientId": int(otb_item.client_id),
                "name": otb_item.name or f"Item #{server_id}",
                "group": int(otb_item.group),
                "topOrder": int(otb_item.top_order or 0),
            }
        )
        items_payload[str(server_id)] = payload
        item_colors[server_id] = _representative_color(thing, sprite_averages)

    creature_keys = sorted(used_looks)
    creature_indices = {key: index for index, key in enumerate(creature_keys)}
    creatures_payload: list[dict[str, Any]] = []
    for key in creature_keys:
        look = used_looks[key]
        thing = creature_things.get(key)
        creatures_payload.append(
            {
                "name": look.name,
                "lookType": look.look_type,
                "lookItem": look.look_item,
                "lookMount": look.look_mount,
                "lookAddon": look.look_addon,
                "colors": [look.head, look.body, look.legs, look.feet],
                "thing": _thing_payload(thing, sprite_refs, fallback_ms=DEFAULT_CREATURE_FRAME_MS, category="creature") if thing else None,
            }
        )

    cells_per_row = atlas_size // SPRITE_SIZE
    _gzip_json(
        output_path / "things.json.gz",
        {
            "atlas": {
                "size": atlas_size,
                "cellSize": SPRITE_SIZE,
                "cellsPerRow": cells_per_row,
                "capacity": cells_per_row * cells_per_row,
                "pages": atlas_pages,
            },
            "items": items_payload,
            "creatures": creatures_payload,
        },
    )

    print("[5/7] Splitting the map into compressed spatial chunks...")
    chunk_size = int(config.get("chunkSize", 64))
    chunks: dict[tuple[int, int, int], list[ChunkTile]] = defaultdict(list)
    tiles_by_position: dict[tuple[int, int, int], ChunkTile] = {}
    floor_counts: Counter[int] = Counter()
    for tile in world.tiles:
        chunk_x = math.floor(tile.x / chunk_size)
        chunk_y = math.floor(tile.y / chunk_size)
        converted = ChunkTile(
            local_x=tile.x - chunk_x * chunk_size,
            local_y=tile.y - chunk_y * chunk_size,
            flags=tile.flags,
            house_id=tile.house_id,
            entries=[ChunkEntry(ENTRY_ITEM, item.server_id, min(item.count, 0xFFFF)) for item in tile.items],
        )
        chunks[(tile.z, chunk_x, chunk_y)].append(converted)
        tiles_by_position[(tile.x, tile.y, tile.z)] = converted
        floor_counts[tile.z] += 1

    for spawn in spawns:
        key = spawn.name.casefold()
        creature_index = creature_indices.get(key)
        if creature_index is None:
            continue
        tile = tiles_by_position.get((spawn.x, spawn.y, spawn.z))
        if tile is None:
            chunk_x = math.floor(spawn.x / chunk_size)
            chunk_y = math.floor(spawn.y / chunk_size)
            tile = ChunkTile(spawn.x - chunk_x * chunk_size, spawn.y - chunk_y * chunk_size)
            chunks[(spawn.z, chunk_x, chunk_y)].append(tile)
            tiles_by_position[(spawn.x, spawn.y, spawn.z)] = tile
        tile.entries.append(ChunkEntry(ENTRY_CREATURE, creature_index, spawn.direction))

    chunk_index: dict[int, list[list[int]]] = defaultdict(list)
    for (floor, chunk_x, chunk_y), tiles in sorted(chunks.items()):
        destination = output_path / "chunks" / str(floor) / f"{chunk_x}_{chunk_y}.bin.gz"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(encode_gzip(chunk_size, tiles))
        chunk_index[floor].append([chunk_x, chunk_y])

    print("[6/7] Creating real-sprite low-zoom overview tiles...")
    overview_pixels = int(config.get("overviewPixelsPerTile", 8))
    build_sprite_overviews(
        output_path,
        chunks,
        items_payload,
        atlas_pages,
        atlas_size,
        chunk_size,
        overview_pixels,
    )

    bounds = _bounds_for_tiles(world.tiles)
    initial = _initial_position(config, floor_counts, chunks, chunk_size)
    floors_payload = {
        str(floor): {
            "bounds": bounds.get(floor, [0, 0, 0, 0]),
            "chunks": chunk_index.get(floor, []),
            "tileCount": floor_counts.get(floor, 0),
        }
        for floor in sorted(chunk_index)
    }
    manifest = {
        "version": 1,
        "title": str(config.get("title", "YurOTS Map")),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "map": {
            "name": map_path.name,
            "width": world.width,
            "height": world.height,
            "description": world.description,
            "chunkSize": chunk_size,
            "overviewPixelsPerTile": overview_pixels,
            "floors": floors_payload,
            "initial": initial,
        },
        "assets": {"things": "things.json.gz"},
        "stats": {
            "tiles": len(world.tiles),
            "items": sum(len(tile.items) for tile in world.tiles),
            "spawns": len(spawns),
            "usedItemTypes": len(item_things),
            "usedCreatureTypes": len(creature_things),
            "usedSprites": len(used_sprite_ids),
            "atlasPages": len(atlas_pages),
            "chunks": len(chunks),
            "missingItems": len(missing_items),
            "missingCreatures": len(missing_creatures),
        },
        "warnings": {
            "missingItemIds": missing_items[:100],
            "missingCreatureNames": sorted(missing_creatures)[:100],
        },
    }
    with (output_path / "manifest.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    _publish_working_output(output_path, final_output_path)
    print("[7/7] Done.")
    print(
        f"Tiles: {len(world.tiles):,}; items: {manifest['stats']['items']:,}; "
        f"chunks: {len(chunks):,}; atlas pages: {len(atlas_pages)}"
    )
    if missing_items:
        print(f"Warning: {len(missing_items)} server item ids have no usable OTB/DAT mapping")
    if missing_creatures:
        print(f"Warning: {len(missing_creatures)} spawn creature names are absent from creatures.xml")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_DIR / "viewer.config.json",
        help="Path to viewer.config.json",
    )
    arguments = parser.parse_args()
    build(arguments.config.resolve())


if __name__ == "__main__":
    main()
