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
        self.assertIn('id="memoryNodeShrinkButton"', self.html)
        self.assertIn('id="memoryNodeScaleValue"', self.html)
        self.assertIn('id="memoryNodeGrowButton"', self.html)
        self.assertIn('<button class="node-scale-value" id="memoryNodeScaleValue" type="button"', self.html)
        self.assertIn("⦿ 1.00x", self.html)
        self.assertIn("function setMemoryGraphNodeScale(nextScale)", self.html)
        self.assertIn("memoryNodeShrinkButton.addEventListener", self.html)
        self.assertIn("memoryNodeScaleValue.addEventListener", self.html)
        self.assertIn("memoryNodeGrowButton.addEventListener", self.html)
        self.assertIn("evelynMemoryGraphNodeScale", self.html)

    def test_node_size_control_can_reset_and_shrink_below_one(self) -> None:
        self.assertIn("Math.max(0.25, Math.min(2, memoryGraphNodeScale));", self.html)
        self.assertIn("memoryNodeShrinkButton.disabled = memoryGraphNodeScale <= 0.25;", self.html)
        self.assertIn("setMemoryGraphNodeScale(1);", self.html)
        self.assertIn("memoryGraphNodeScale - 0.25", self.html)
        self.assertIn("memoryGraphNodeScale + 0.25", self.html)

    def test_node_size_control_keeps_existing_graph_positions(self) -> None:
        self.assertIn("function refreshMemoryGraphNodeScale()", self.html)
        self.assertIn("node.radius = graphNodeRadius(node.importance);", self.html)
        self.assertIn('circle.setAttribute("r", node.radius.toFixed(1));', self.html)
        self.assertIn("refreshMemoryGraphNodeScale();", self.html)

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

    def test_graph_node_labels_use_requested_type_names(self) -> None:
        self.assertIn("node.created_at", self.html)
        self.assertIn('if (type === "legacy") return "";', self.html)
        self.assertIn('if (type === "daily") return formatGraphDate(date, "daily") || "daily";', self.html)
        self.assertIn('if (type === "core") return "core";', self.html)
        self.assertIn('if (type === "project") return "Project";', self.html)
        self.assertIn('if (format === "daily") return `${date.year.slice(-2)}.${date.day}`;', self.html)
        self.assertNotIn("return acronymLabel(node.title || node.id);", self.html)

    def test_legacy_memory_cards_are_locked_in_public_ui(self) -> None:
        self.assertIn("selectedMemoryCard.locked || selectedMemoryCard.canEdit === false || selectedMemoryCard.contentHidden", self.html)
        self.assertIn("Archived memory is locked, so its contents cannot be viewed or edited here.", self.html)
        self.assertIn("locked ? (selectedMemoryCard.preview || \"Archived memory is locked.\")", self.html)
        self.assertIn("locked ? \"Archived\"", self.html)
        self.assertIn('const relPath = locked ? "Archived"', self.html)
        self.assertIn("const nodeLocked = Boolean(node.locked || node.canEdit === false || node.contentHidden);", self.html)
        self.assertIn('const displayTitle = locked ? "Archived memory" : title;', self.html)
        self.assertIn("memoryEditTitle.value = hasSelection ? (locked ? \"Archived memory\" : (selectedMemoryCard.title || \"\")) : \"\";", self.html)

    def test_graph_layout_updates_when_memory_window_resizes(self) -> None:
        self.assertIn("function applyWindowBox(box, { resize = true } = {})", self.html)
        self.assertIn("applyMemoryGraphLayout(memoryGraphMotionState);", self.html)

    def test_memory_window_position_drag_does_not_resize_graph(self) -> None:
        self.assertIn("function applyWindowBox(box, { resize = true } = {})", self.html)
        self.assertIn("if (resize) {", self.html)
        self.assertIn("applyWindowBox(next, { resize: false });", self.html)

    def test_graph_nodes_do_not_highlight_on_hover(self) -> None:
        self.assertNotIn(".memory-graph .node:hover circle", self.html)
        self.assertNotIn("stroke: var(--green);", self.html)
        self.assertNotIn("stroke-width: 3;", self.html)
        self.assertIn("stroke: rgba(88, 96, 108, 0.52);", self.html)

    def test_management_graph_requires_explicit_toggle(self) -> None:
        self.assertIn('id="memoryManagementGraphButton"', self.html)
        self.assertIn("let memoryGraphIncludeInternal = false;", self.html)
        self.assertIn('memoryGraphIncludeInternal ? "&include_internal=true" : ""', self.html)
        self.assertIn("updateMemoryManagementGraphButton();", self.html)

    def test_memory_editor_shows_provenance_and_permanently_deletes(self) -> None:
        self.assertIn('id="memoryProvenance"', self.html)
        self.assertIn("function renderMemoryProvenance(card)", self.html)
        self.assertIn("provenance.sourceRefs", self.html)
        self.assertIn("provenance.derivedFrom", self.html)
        self.assertIn("provenance.evidenceHashes", self.html)
        self.assertIn(
            '"/delete/preview"',
            self.html,
        )
        self.assertIn(
            '"/delete/apply"',
            self.html,
        )
        self.assertIn("confirmToken: preview.confirmToken", self.html)
        self.assertIn(
            'memoryDeleteButton.addEventListener("click", deleteSelectedMemory)',
            self.html,
        )
        self.assertNotIn('postMemoryAction("hide")', self.html)


if __name__ == "__main__":
    unittest.main()
