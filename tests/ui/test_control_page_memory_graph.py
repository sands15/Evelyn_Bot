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

    def test_graph_labels_are_below_nodes_without_heavy_stroke(self) -> None:
        self.assertIn("fill: var(--soft);", self.html)
        self.assertIn("font-weight: 600;", self.html)
        self.assertIn("dominant-baseline: hanging;", self.html)
        self.assertIn('" y="\' + (point.y + radius + 7).toFixed(1) + \'" text-anchor="middle"', self.html)
        self.assertIn('text.setAttribute("x", node.x.toFixed(1));', self.html)
        self.assertIn('text.setAttribute("y", (node.y + node.radius + 7).toFixed(1));', self.html)
        self.assertNotIn("stroke-width: 4px;", self.html)
        self.assertNotIn("node.x + node.radius + 5", self.html)

    def test_graph_layout_updates_when_memory_window_moves(self) -> None:
        self.assertIn("function applyWindowBox(box)", self.html)
        self.assertIn("applyMemoryGraphLayout(memoryGraphMotionState);", self.html)


if __name__ == "__main__":
    unittest.main()
