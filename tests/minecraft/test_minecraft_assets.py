import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.minecraft_assets import (  # noqa: E402
    MinecraftItemIconLoader,
    collect_item_definition_model_refs,
    find_latest_minecraft_version_jar,
    minecraft_texture_ref_to_asset_path,
    normalize_model_reference,
    pick_model_texture_ref,
    read_minecraft_asset_bytes,
    read_minecraft_asset_json,
    resolve_minecraft_item_texture_path,
    resolve_model_texture_path,
    resolve_texture_alias,
)


def _open_archive(files: dict[str, bytes | str]) -> zipfile.ZipFile:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for path, content in files.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            archive.writestr(path, content)
    payload.seek(0)
    return zipfile.ZipFile(payload)


class MinecraftAssetsTests(unittest.TestCase):
    def test_asset_readers_and_texture_paths_are_tolerant(self) -> None:
        with _open_archive(
            {
                "assets/minecraft/models/item/stick.json": json.dumps({"textures": {"layer0": "item/stick"}}),
                "assets/minecraft/textures/item/stick.png": b"png",
                "assets/minecraft/models/item/bad.json": "{bad",
            }
        ) as archive:
            self.assertEqual(read_minecraft_asset_bytes(archive, "assets/minecraft/textures/item/stick.png"), b"png")
            self.assertIsNone(read_minecraft_asset_bytes(archive, "missing"))
            self.assertEqual(
                read_minecraft_asset_json(archive, "assets/minecraft/models/item/stick.json"),
                {"textures": {"layer0": "item/stick"}},
            )
            self.assertIsNone(read_minecraft_asset_json(archive, "assets/minecraft/models/item/bad.json"))

        self.assertEqual(
            minecraft_texture_ref_to_asset_path("minecraft:textures/item/stick"),
            "assets/minecraft/textures/item/stick.png",
        )
        self.assertEqual(
            minecraft_texture_ref_to_asset_path("/block/stone.png"),
            "assets/minecraft/textures/block/stone.png",
        )
        self.assertIsNone(minecraft_texture_ref_to_asset_path(""))

    def test_aliases_models_and_item_definitions_resolve_textures(self) -> None:
        self.assertEqual(resolve_texture_alias("#layer0", {"layer0": "item/stick"}), "item/stick")
        self.assertIsNone(resolve_texture_alias("#a", {"a": "#b", "b": "#a"}))
        self.assertEqual(pick_model_texture_ref({"particle": "item/fallback"}), "item/fallback")
        self.assertEqual(normalize_model_reference("minecraft:models/block/oak_log.json"), "block/oak_log")
        self.assertEqual(
            collect_item_definition_model_refs(
                {
                    "model": {"type": "minecraft:model", "model": "minecraft:item/stick"},
                    "fallback": [{"type": "minecraft:model", "model": "minecraft:item/stick"}],
                }
            ),
            ["minecraft:item/stick"],
        )

        with _open_archive(
            {
                "assets/minecraft/items/custom_pickaxe.json": json.dumps(
                    {"model": {"type": "minecraft:model", "model": "minecraft:item/generated_pickaxe"}}
                ),
                "assets/minecraft/models/item/generated_pickaxe.json": json.dumps(
                    {
                        "parent": "minecraft:item/generated",
                        "textures": {"layer0": "minecraft:item/custom_pickaxe"},
                    }
                ),
                "assets/minecraft/models/item/generated.json": json.dumps({"textures": {"particle": "#layer0"}}),
                "assets/minecraft/textures/item/custom_pickaxe.png": b"png",
            }
        ) as archive:
            self.assertEqual(
                resolve_model_texture_path(archive, "minecraft:item/generated_pickaxe"),
                "assets/minecraft/textures/item/custom_pickaxe.png",
            )
            self.assertEqual(
                resolve_minecraft_item_texture_path(archive, "minecraft:Custom Pickaxe!"),
                "assets/minecraft/textures/item/custom_pickaxe.png",
            )

    def test_item_texture_resolution_falls_back_to_direct_and_legacy_models(self) -> None:
        with _open_archive(
            {
                "assets/minecraft/textures/block/stone.png": b"png",
                "assets/minecraft/models/item/legacy_item.json": json.dumps({"textures": {"layer0": "item/legacy_item"}}),
                "assets/minecraft/textures/item/legacy_item.png": b"png",
            }
        ) as archive:
            self.assertEqual(
                resolve_minecraft_item_texture_path(archive, "minecraft:stone"),
                "assets/minecraft/textures/block/stone.png",
            )
            self.assertEqual(
                resolve_minecraft_item_texture_path(archive, "legacy_item"),
                "assets/minecraft/textures/item/legacy_item.png",
            )
            self.assertIsNone(resolve_minecraft_item_texture_path(archive, "missing_item"))

    def test_icon_loader_finds_latest_version_jar_and_caches_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            minecraft_root = Path(temp_dir) / ".minecraft"
            old_version = minecraft_root / "versions" / "1.0"
            new_version = minecraft_root / "versions" / "1.1"
            old_version.mkdir(parents=True)
            new_version.mkdir(parents=True)
            old_jar = old_version / "1.0.jar"
            new_jar = new_version / "1.1.jar"
            old_jar.write_bytes(b"not a zip")
            with zipfile.ZipFile(new_jar, "w") as archive:
                archive.writestr("assets/minecraft/textures/item/stick.png", b"stick-png")
            old_time = new_jar.stat().st_mtime - 10
            os.utime(old_jar, (old_time, old_time))

            self.assertEqual(find_latest_minecraft_version_jar([minecraft_root]), new_jar)

            cache: dict[str, bytes | None] = {}
            loader = MinecraftItemIconLoader(Path(temp_dir), minecraft_roots=[minecraft_root], cache=cache)
            self.assertEqual(loader.load_icon("minecraft:stick"), b"stick-png")
            self.assertEqual(cache["stick"], b"stick-png")
            new_jar.unlink()
            self.assertEqual(loader.load_icon("stick"), b"stick-png")


if __name__ == "__main__":
    unittest.main()
