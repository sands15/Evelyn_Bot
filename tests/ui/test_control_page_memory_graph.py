from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
CONTROL_PAGE = REPO_ROOT / "docs" / "index.html"


class ControlPageMemoryGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = CONTROL_PAGE.read_text(encoding="utf-8")

    def test_graph_has_separate_node_size_control(self) -> None:
        self.assertIn('id="memoryNodeGrowButton"', self.html)
        self.assertIn("function growMemoryGraphNodes()", self.html)
        self.assertIn("evelynMemoryGraphNodeScale", self.html)

    def test_graph_uses_container_dimensions_for_viewbox(self) -> None:
        self.assertIn("function graphDimensions()", self.html)
        self.assertIn("const dimensions = graphDimensions();", self.html)
        self.assertIn("startMemoryGraphMotion(nodes, visibleEdges, positions, dimensions)", self.html)
        self.assertNotIn("const width = 980;", self.html)
        self.assertNotIn("const height = 560;", self.html)

    def test_memory_window_can_resize_to_page_top(self) -> None:
        self.assertIn("const topMargin = 0;", self.html)
        self.assertIn("window.innerHeight - topMargin - margin", self.html)
        self.assertIn("Math.max(topMargin, top)", self.html)


if __name__ == "__main__":
    unittest.main()
