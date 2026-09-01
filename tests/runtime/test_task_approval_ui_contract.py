from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
INDEX = REPO_ROOT / "docs" / "index.html"
SCRIPT = REPO_ROOT / "docs" / "assets" / "evelyn-task-approval.js"
STYLE = REPO_ROOT / "docs" / "assets" / "evelyn-task-approval.css"


class TaskApprovalUiContractTests(unittest.TestCase):
    def test_panel_consumes_the_existing_state_poll_projection(self) -> None:
        index = INDEX.read_text(encoding="utf-8")
        self.assertIn('id="taskApprovalMount"', index)
        self.assertIn('id="taskRecordMount"', index)
        self.assertIn('new CustomEvent("evelyn:task-approval-state"', index)
        self.assertIn('new CustomEvent("evelyn:task-record-state"', index)
        self.assertIn("state.actions.approval", index)
        self.assertIn("state.actions.tasks", index)
        self.assertIn("evelyn-task-approval.js", index)
        self.assertIn("evelyn-task-approval.css", index)

    def test_confirm_token_stays_in_closure_and_is_never_dom_or_storage_state(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('let confirmToken = "";', source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("dataset.confirmToken", source)
        self.assertNotIn("data-confirm-token", source)
        self.assertNotIn("window.confirmToken", source)

    def test_approval_mutations_use_a_dedicated_single_attempt_csrf_client(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("window.fetchApi", source)
        self.assertIn("const APPROVAL_PATHS = new Set", source)
        self.assertIn('"X-Evelyn-CSRF-Token": approvalCsrfToken', source)
        self.assertIn("This mutation request is deliberately sent once", source)
        self.assertIn(
            'if (response.status === 403) approvalCsrfToken = "";',
            source,
        )
        self.assertNotIn("response.status === 403 &&", source)
        api_source = source[
            source.index("async function api(") :
            source.index("function clearApprovalSecret(")
        ]
        self.assertEqual(api_source.count("await fetch("), 1)

    def test_javascript_parses(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        result = subprocess.run(
            [node, "--check", str(SCRIPT)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_full_untruncated_diff_and_dirty_base_need_separate_checks(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('value.diffTruncated !== false', source)
        self.assertIn('"pre", "task-approval-diff", preview.fullDiff', source)
        self.assertIn("diff.tabIndex = 0", source)
        self.assertIn("dirtyBaseAcknowledgementRequired", source)
        self.assertIn('data-task-approval-dirty-check', source)
        self.assertIn('data-task-approval-exact-check', source)
        self.assertIn("후보 검토용 관측이며 동작 해결의 증명이 아닙니다", source)
        self.assertIn('dirtyBaseAcknowledged: dirtyAcknowledged', source)
        self.assertIn('"1회 토큰 만료"', source)
        self.assertIn('"추적 여부"', source)
        self.assertIn("자동 재시도", source)
        self.assertIn('next.state === "awaiting_approval"', source)

    def test_workspace_test_has_no_review_or_approval_path(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        blocked = source.index('publicApproval.tool === "workspace_test"')
        review = source.index('dataset.taskApprovalReview = "1"')
        self.assertLess(blocked, review)
        blocked_section = source[blocked:review]
        self.assertIn("sandbox", blocked_section)
        self.assertNotIn("requestPreview", blocked_section)

    def test_diff_card_has_bounded_scrollable_layout(self) -> None:
        style = STYLE.read_text(encoding="utf-8")
        self.assertIn(".task-approval-diff", style)
        self.assertIn("max-height: 360px", style)
        self.assertIn("overflow: auto", style)
        self.assertEqual(style.count("unicode-bidi: plaintext"), 2)

    def test_recent_task_records_are_exact_bounded_and_content_free(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        record_section = source[
            source.index("function validTaskRecord(") :
            source.index("function setState(")
        ]
        self.assertIn('value.schema !== "evelyn.task-public-record.v1"', record_section)
        self.assertIn('value.processLocal !== true || value.durable !== false', record_section)
        self.assertIn(
            '(guidanceVersion === "base") !== (guidanceDigest === BASE_GUIDANCE_DIGEST)',
            record_section,
        )
        self.assertIn(".slice(-4)", record_section)
        self.assertIn("hasExactKeys(value, TASK_RECORD_FIELDS)", record_section)
        for private_field in (
            "summary",
            "evidence",
            "principal",
            "modulePath",
            "finalReply",
            "userText",
        ):
            self.assertNotIn(private_field, record_section)


if __name__ == "__main__":
    unittest.main()
