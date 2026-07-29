from __future__ import annotations

import gzip
import json
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT / "tools"
DOCS = PROJECT / "docs"
ASSETS = DOCS / "assets"
sys.path.insert(0, str(TOOLS))

from chunk_format import ChunkEntry, ChunkTile, encode, encode_gzip  # noqa: E402


class ChunkFormatTests(unittest.TestCase):
    def test_binary_layout_matches_browser_reader(self) -> None:
        tile = ChunkTile(
            local_x=7,
            local_y=9,
            flags=0x10203040,
            house_id=81,
            entries=[ChunkEntry(0, 2160, 100), ChunkEntry(1, 3, 2)],
        )
        payload = encode(64, [tile])

        self.assertEqual(payload[:4], b"YMC1")
        version, chunk_size, tile_count = struct.unpack_from("<BBH", payload, 4)
        self.assertEqual((version, chunk_size, tile_count), (1, 64, 1))
        self.assertEqual(struct.unpack_from("<BBIIH", payload, 8), (7, 9, 0x10203040, 81, 2))
        self.assertEqual(struct.unpack_from("<BHH", payload, 20), (0, 2160, 100))
        self.assertEqual(struct.unpack_from("<BHH", payload, 25), (1, 3, 2))

    def test_gzip_is_reproducible(self) -> None:
        tiles = [ChunkTile(1, 2, entries=[ChunkEntry(0, 100, 1)])]
        self.assertEqual(encode_gzip(64, tiles), encode_gzip(64, tiles))
        self.assertEqual(gzip.decompress(encode_gzip(64, tiles)), encode(64, tiles))

    def test_out_of_chunk_tile_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode(64, [ChunkTile(64, 0)])


class GeneratedViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
        with gzip.open(ASSETS / "things.json.gz", "rt", encoding="utf-8") as stream:
            cls.things = json.load(stream)

    def test_core_files_exist(self) -> None:
        for relative in (
            "index.html",
            "app.js",
            "data.js",
            "renderer.js",
            "styles.css",
            "og-card.jpg",
            ".nojekyll",
        ):
            self.assertTrue((DOCS / relative).is_file(), relative)

    def test_manifest_is_consistent(self) -> None:
        stats = self.manifest["stats"]
        self.assertGreater(stats["tiles"], 0)
        self.assertEqual(stats["usedItemTypes"], len(self.things["items"]))
        self.assertEqual(stats["usedCreatureTypes"], len(self.things["creatures"]))
        self.assertEqual(stats["atlasPages"], len(self.things["atlas"]["pages"]))

        chunk_count = 0
        for floor, descriptor in self.manifest["map"]["floors"].items():
            for chunk_x, chunk_y in descriptor["chunks"]:
                chunk = ASSETS / "chunks" / floor / f"{chunk_x}_{chunk_y}.bin.gz"
                overview = ASSETS / "overview" / floor / f"{chunk_x}_{chunk_y}.webp"
                self.assertTrue(chunk.is_file(), chunk)
                self.assertTrue(overview.is_file(), overview)
                self.assertEqual(gzip.decompress(chunk.read_bytes())[:4], b"YMC1")
                chunk_count += 1
        self.assertEqual(stats["chunks"], chunk_count)

    def test_initial_position_points_to_an_existing_chunk(self) -> None:
        initial = self.manifest["map"]["initial"]
        chunk_size = self.manifest["map"]["chunkSize"]
        descriptor = self.manifest["map"]["floors"][str(initial["z"])]
        expected = [initial["x"] // chunk_size, initial["y"] // chunk_size]
        self.assertIn(expected, descriptor["chunks"])

    def test_atlas_pages_are_valid_lossless_webp(self) -> None:
        atlas = self.things["atlas"]
        for relative in atlas["pages"]:
            path = ASSETS / relative
            with Image.open(path) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.size, (atlas["size"], atlas["size"]))

    def test_overviews_contain_real_transparent_sprite_renders(self) -> None:
        chunk_size = self.manifest["map"]["chunkSize"]
        pixels_per_tile = self.manifest["map"]["overviewPixelsPerTile"]
        expected_size = (chunk_size * pixels_per_tile, chunk_size * pixels_per_tile)
        saw_transparency = False
        saw_rendered_pixels = False
        for path in (ASSETS / "overview").rglob("*.webp"):
            with Image.open(path) as image:
                self.assertIn(image.mode, ("RGB", "RGBA"))
                self.assertEqual(image.size, expected_size)
                if image.mode == "RGBA":
                    alpha = image.getchannel("A")
                    minimum, maximum = alpha.getextrema()
                    alpha.close()
                else:
                    minimum = maximum = 255
                saw_transparency |= minimum == 0
                saw_rendered_pixels |= maximum > 0
        self.assertTrue(saw_transparency)
        self.assertTrue(saw_rendered_pixels)

    def test_github_file_limit_has_large_safety_margin(self) -> None:
        largest = max(path.stat().st_size for path in DOCS.rglob("*") if path.is_file())
        self.assertLess(largest, 25 * 1024 * 1024)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_visible_overviews_are_not_evicted(self) -> None:
        result = subprocess.run(
            ["node", str(PROJECT / "tests" / "overview_cache_test.mjs")],
            cwd=PROJECT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
