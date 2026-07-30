from __future__ import annotations

import math
import re
from typing import Any, Iterable


def build_screen_ocr_tiles(
    image: Any,
    *,
    target_tile_width: int = 1400,
    target_tile_height: int = 1200,
    max_columns: int = 3,
    max_rows: int = 2,
    overlap_px: int = 48,
) -> list[Any]:
    """Split a high-resolution screen in memory so small UI text survives OCR resize."""
    width, height = (int(value) for value in image.size)
    if width <= target_tile_width and height <= target_tile_height:
        return [image]

    columns = min(
        max(1, max_columns),
        max(1, math.ceil(width / max(1, target_tile_width))),
    )
    rows = min(
        max(1, max_rows),
        max(1, math.ceil(height / max(1, target_tile_height))),
    )
    tile_width = math.ceil(width / columns)
    tile_height = math.ceil(height / rows)
    tiles: list[Any] = []
    for row in range(rows):
        for column in range(columns):
            left = max(0, (column * tile_width) - (overlap_px if column else 0))
            top = max(0, (row * tile_height) - (overlap_px if row else 0))
            right = min(
                width,
                ((column + 1) * tile_width)
                + (overlap_px if column + 1 < columns else 0),
            )
            bottom = min(
                height,
                ((row + 1) * tile_height)
                + (overlap_px if row + 1 < rows else 0),
            )
            tiles.append(image.crop((left, top, right, bottom)))
    return tiles


def merge_screen_ocr_texts(texts: Iterable[Any]) -> str:
    """Preserve reading order while dropping exact overlap duplicates and empty tiles."""
    merged: list[str] = []
    seen: set[str] = set()
    for value in texts:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return "\n".join(merged)


__all__ = ["build_screen_ocr_tiles", "merge_screen_ocr_texts"]
