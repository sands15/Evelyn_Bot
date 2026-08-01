from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable
import uuid
import re

from PIL import Image

from .paths import get_runtime_root
from .vision_ocr_tiles import build_screen_ocr_tiles, merge_screen_ocr_texts


WINDOWS_OCR_RESULT_SCHEMA = "windows_ocr.result.v1"
WINDOWS_OCR_MAX_TEXT_CHARS = 6000
WINDOWS_OCR_OBSERVATION_SCHEMA = "windows_ocr.observation.v1"


def normalize_windows_ocr_text(text: str, *, language: str) -> str:
    """Discard script-mismatched and fragment-only lines before they become evidence."""
    normalized_lines: list[str] = []
    language = str(language or "").lower()
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or "\ufffd" in line:
            continue
        han_count = sum(
            1
            for character in line
            if "\u3400" <= character <= "\u9fff"
        )
        if language.startswith("ko") and han_count:
            continue
        tokens = re.findall(r"[A-Za-z0-9가-힣]+", line)
        if not tokens or not any(len(token) >= 2 for token in tokens):
            continue
        if (
            len(tokens) >= 10
            and sum(1 for token in tokens if len(token) == 1) / len(tokens)
            >= 0.65
        ):
            continue
        normalized_lines.append(line)
    return "\n".join(normalized_lines)


class WindowsNativeOcr:
    """Run the fixed Windows OCR script only against bridge-owned screenshot files."""

    def __init__(
        self,
        *,
        screenshot_root: Path,
        script_path: Path | None = None,
        powershell_path: Path | None = None,
        timeout_sec: float = 15.0,
        run_process: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.screenshot_root = Path(screenshot_root)
        self.script_path = Path(
            script_path
            or get_runtime_root() / "launchers" / "invoke_windows_ocr.ps1"
        )
        self.powershell_path = Path(
            powershell_path
            or Path(
                os.environ.get("SystemRoot", r"C:\Windows"),
                "System32",
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe",
            )
        )
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.run_process = run_process

    def _confined_image(self, image_path: Path) -> Path:
        resolved_root = self.screenshot_root.resolve()
        resolved_image = type(self.screenshot_root)(image_path).resolve()
        if (
            resolved_root not in (resolved_image, *resolved_image.parents)
            or not resolved_image.is_file()
        ):
            raise RuntimeError("windows_ocr_image_outside_bridge_root")
        return resolved_image

    def _recognize_tile(self, tile_path: Path) -> str:
        if os.name != "nt":
            raise RuntimeError("windows_ocr_requires_windows")
        if not self.script_path.is_file() or not self.powershell_path.is_file():
            raise RuntimeError("windows_ocr_runtime_unavailable")
        completed = self.run_process(
            [
                str(self.powershell_path),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script_path),
                "-ImagePath",
                str(tile_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_sec,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if int(completed.returncode or 0) != 0:
            raise RuntimeError("windows_ocr_process_failed")
        lines = [
            line.strip()
            for line in str(completed.stdout or "").splitlines()
            if line.strip()
        ]
        if not lines:
            raise RuntimeError("windows_ocr_empty_response")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("windows_ocr_invalid_response") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != WINDOWS_OCR_RESULT_SCHEMA
            or payload.get("ok") is not True
        ):
            raise RuntimeError("windows_ocr_invalid_response")
        return normalize_windows_ocr_text(
            str(payload.get("text") or ""),
            language=str(payload.get("language") or ""),
        )[:WINDOWS_OCR_MAX_TEXT_CHARS]

    def recognize_sync(self, image_path: Path) -> dict[str, Any]:
        image_path = self._confined_image(image_path)
        self.screenshot_root.mkdir(parents=True, exist_ok=True)
        texts: list[str] = []
        failures = 0
        with Image.open(image_path) as image:
            tiles = build_screen_ocr_tiles(image.convert("RGB"))
            for tile in tiles:
                tile_path = self.screenshot_root / (
                    f".windows_ocr_{uuid.uuid4().hex}.png"
                )
                try:
                    tile.save(tile_path, format="PNG")
                    texts.append(self._recognize_tile(tile_path))
                except Exception:
                    failures += 1
                finally:
                    try:
                        tile_path.unlink()
                    except (FileNotFoundError, PermissionError, OSError):
                        pass
        merged = merge_screen_ocr_texts(texts)
        if not merged and failures == len(tiles):
            raise RuntimeError("windows_ocr_failed")
        return {
            "schema": WINDOWS_OCR_OBSERVATION_SCHEMA,
            "attempted": True,
            "text": merged[:WINDOWS_OCR_MAX_TEXT_CHARS],
        }

    async def recognize(self, image_path: Path) -> dict[str, Any]:
        return await asyncio.to_thread(self.recognize_sync, image_path)


__all__ = [
    "WINDOWS_OCR_MAX_TEXT_CHARS",
    "WINDOWS_OCR_OBSERVATION_SCHEMA",
    "WINDOWS_OCR_RESULT_SCHEMA",
    "WindowsNativeOcr",
    "normalize_windows_ocr_text",
]
