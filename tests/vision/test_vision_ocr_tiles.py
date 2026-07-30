from __future__ import annotations

import unittest

from evelyn_core.vision_ocr_tiles import (
    build_screen_ocr_tiles,
    merge_screen_ocr_texts,
)


class _FakeImage:
    def __init__(self, width: int, height: int) -> None:
        self.size = (width, height)
        self.crops: list[tuple[int, int, int, int]] = []

    def crop(self, bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        self.crops.append(bounds)
        return bounds


class VisionOcrTileTests(unittest.TestCase):
    def test_4k_screen_is_split_into_six_bounded_overlapping_regions(self) -> None:
        image = _FakeImage(3840, 2160)

        tiles = build_screen_ocr_tiles(image)

        self.assertEqual(len(tiles), 6)
        self.assertEqual(tiles, image.crops)
        self.assertEqual(tiles[0], (0, 0, 1328, 1128))
        self.assertEqual(tiles[-1], (2512, 1032, 3840, 2160))
        self.assertTrue(
            all(
                0 <= left < right <= 3840 and 0 <= top < bottom <= 2160
                for left, top, right, bottom in tiles
            )
        )

    def test_small_image_is_used_without_copying_or_cropping(self) -> None:
        image = _FakeImage(1280, 720)

        self.assertEqual(build_screen_ocr_tiles(image), [image])
        self.assertEqual(image.crops, [])

    def test_merge_drops_empty_and_exact_overlap_duplicates(self) -> None:
        self.assertEqual(
            merge_screen_ocr_texts(
                [" E.V.E.L.Y.N   전송 ", "", "e.v.e.l.y.n 전송", "상태 버튼"]
            ),
            "E.V.E.L.Y.N 전송\n상태 버튼",
        )


if __name__ == "__main__":
    unittest.main()
