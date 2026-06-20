from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Any

from .minecraft_runtime_snapshot import normalize_minecraft_item_name
from .text import clean_text


def read_minecraft_asset_bytes(archive: zipfile.ZipFile, asset_path: str) -> bytes | None:
    try:
        return archive.read(asset_path)
    except KeyError:
        return None


def read_minecraft_asset_json(archive: zipfile.ZipFile, asset_path: str) -> dict[str, Any] | list[Any] | None:
    payload = read_minecraft_asset_bytes(archive, asset_path)
    if payload is None:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def minecraft_texture_ref_to_asset_path(texture_ref: str) -> str | None:
    normalized = clean_text(str(texture_ref or "")).strip()
    if not normalized:
        return None
    normalized = normalized.replace("minecraft:", "")
    if normalized.startswith("textures/"):
        normalized = normalized[len("textures/"):]
    normalized = normalized.lstrip("/")
    if not normalized:
        return None
    if not normalized.endswith(".png"):
        normalized = normalized + ".png"
    return f"assets/minecraft/textures/{normalized}"


def resolve_texture_alias(texture_ref: str, textures: dict[str, Any]) -> str | None:
    current = clean_text(str(texture_ref or "")).strip()
    seen: set[str] = set()
    while current.startswith("#"):
        key = current[1:]
        if key in seen:
            return None
        seen.add(key)
        next_value = textures.get(key)
        if not isinstance(next_value, str):
            return None
        current = next_value
    return current or None


def pick_model_texture_ref(textures: dict[str, Any]) -> str | None:
    for key in ("layer0", "all", "side", "top", "front", "end", "particle", "north"):
        value = textures.get(key)
        if isinstance(value, str) and value:
            return resolve_texture_alias(value, textures)
    return None


def normalize_model_reference(model_ref: str, *, default_kind: str = "item") -> str:
    normalized = clean_text(str(model_ref or "")).strip().replace("minecraft:", "").lstrip("/")
    if normalized.startswith("models/"):
        normalized = normalized[len("models/"):]
    if normalized.endswith(".json"):
        normalized = normalized[:-5]
    if "/" not in normalized:
        normalized = f"{default_kind}/{normalized}"
    return normalized


def resolve_model_texture_path(
    archive: zipfile.ZipFile,
    model_ref: str,
    *,
    inherited_textures: dict[str, Any] | None = None,
    seen_models: set[str] | None = None,
    default_kind: str = "item",
) -> str | None:
    normalized_ref = normalize_model_reference(model_ref, default_kind=default_kind)
    if not normalized_ref:
        return None
    seen = seen_models or set()
    if normalized_ref in seen:
        return None
    seen.add(normalized_ref)
    model_path = f"assets/minecraft/models/{normalized_ref}.json"
    model_json = read_minecraft_asset_json(archive, model_path)
    fallback_name = normalized_ref.split("/", 1)[1] if "/" in normalized_ref else normalized_ref
    if not isinstance(model_json, dict):
        for direct_ref in (f"item/{fallback_name}", f"block/{fallback_name}"):
            direct_path = minecraft_texture_ref_to_asset_path(direct_ref)
            if direct_path and read_minecraft_asset_bytes(archive, direct_path) is not None:
                return direct_path
        return None
    textures = dict(inherited_textures or {})
    own_textures = model_json.get("textures") if isinstance(model_json.get("textures"), dict) else {}
    for key, value in own_textures.items():
        if isinstance(value, str):
            textures[key] = value
    texture_ref = pick_model_texture_ref(textures)
    if texture_ref:
        asset_path = minecraft_texture_ref_to_asset_path(texture_ref)
        if asset_path and read_minecraft_asset_bytes(archive, asset_path) is not None:
            return asset_path
    parent_ref = model_json.get("parent")
    if isinstance(parent_ref, str) and parent_ref:
        parent_path = resolve_model_texture_path(
            archive,
            parent_ref,
            inherited_textures=textures,
            seen_models=seen,
        )
        if parent_path:
            return parent_path
    for direct_ref in (f"item/{fallback_name}", f"block/{fallback_name}"):
        direct_path = minecraft_texture_ref_to_asset_path(direct_ref)
        if direct_path and read_minecraft_asset_bytes(archive, direct_path) is not None:
            return direct_path
    return None


def collect_item_definition_model_refs(node: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(node, dict):
        node_type = clean_text(str(node.get("type") or "")).strip()
        model_value = node.get("model")
        if node_type == "minecraft:model" and isinstance(model_value, str) and model_value:
            refs.append(model_value)
        for value in node.values():
            refs.extend(collect_item_definition_model_refs(value))
    elif isinstance(node, list):
        for value in node:
            refs.extend(collect_item_definition_model_refs(value))
    deduped: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        normalized = clean_text(ref)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def resolve_minecraft_item_texture_path(archive: zipfile.ZipFile, item_name: str) -> str | None:
    normalized_name = normalize_minecraft_item_name(item_name)
    if not normalized_name:
        return None
    for direct_ref in (f"item/{normalized_name}", f"block/{normalized_name}"):
        direct_path = minecraft_texture_ref_to_asset_path(direct_ref)
        if direct_path and read_minecraft_asset_bytes(archive, direct_path) is not None:
            return direct_path
    item_definition = read_minecraft_asset_json(archive, f"assets/minecraft/items/{normalized_name}.json")
    for model_ref in collect_item_definition_model_refs(item_definition):
        resolved = resolve_model_texture_path(archive, model_ref)
        if resolved:
            return resolved
    legacy_item_model = resolve_model_texture_path(archive, f"item/{normalized_name}")
    if legacy_item_model:
        return legacy_item_model
    legacy_block_model = resolve_model_texture_path(archive, f"block/{normalized_name}", default_kind="block")
    if legacy_block_model:
        return legacy_block_model
    return None


def find_latest_minecraft_version_jar(minecraft_roots: list[Path]) -> Path | None:
    version_jars: list[Path] = []
    seen_roots: set[str] = set()
    for root in minecraft_roots:
        root = root.expanduser()
        key = str(root).lower()
        if key in seen_roots:
            continue
        seen_roots.add(key)
        versions_dir = root / "versions"
        if not versions_dir.exists():
            continue
        for entry in versions_dir.iterdir():
            if not entry.is_dir():
                continue
            jar_path = entry / f"{entry.name}.jar"
            if jar_path.is_file():
                version_jars.append(jar_path)
    if not version_jars:
        return None
    return max(version_jars, key=lambda path: path.stat().st_mtime)


class MinecraftItemIconLoader:
    def __init__(
        self,
        project_root: Path,
        *,
        minecraft_roots: list[Path] | None = None,
        cache: dict[str, bytes | None] | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.minecraft_roots = minecraft_roots
        self.cache = cache if cache is not None else {}

    def candidate_roots(self) -> list[Path]:
        if self.minecraft_roots is not None:
            return list(self.minecraft_roots)
        candidates: list[Path] = []
        appdata = os.getenv("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / ".minecraft")
        candidates.append(Path.home() / ".minecraft")
        candidates.append(self.project_root / ".minecraft")
        return candidates

    def discover_version_jar(self) -> Path | None:
        return find_latest_minecraft_version_jar(self.candidate_roots())

    def load_icon(self, item_name: str) -> bytes | None:
        normalized_name = normalize_minecraft_item_name(item_name)
        if not normalized_name:
            return None
        if normalized_name in self.cache:
            return self.cache[normalized_name]
        jar_path = self.discover_version_jar()
        if jar_path is None:
            self.cache[normalized_name] = None
            return None
        icon_bytes: bytes | None = None
        try:
            with zipfile.ZipFile(jar_path) as archive:
                texture_path = resolve_minecraft_item_texture_path(archive, normalized_name)
                if texture_path:
                    icon_bytes = read_minecraft_asset_bytes(archive, texture_path)
        except (OSError, zipfile.BadZipFile):
            icon_bytes = None
        self.cache[normalized_name] = icon_bytes
        return icon_bytes
