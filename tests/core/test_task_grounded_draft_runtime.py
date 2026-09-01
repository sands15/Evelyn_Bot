from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.task_grounded_draft_runtime import (  # noqa: E402
    GROUNDED_DRAFT_SCHEMA,
    GROUNDED_DRAFT_TTS_TEXT,
    GroundedDraftError,
    explicit_link_requested,
    grounded_draft_from_task_payload,
    grounded_draft_kind,
    grounded_evidence_fragments,
    grounded_evidence_manifest,
    render_grounded_draft,
    safe_verified_https_url,
    validate_grounded_draft,
)


TASK_ID = "task-grounded-test"


def _observation(step: int, tool: str, code: str, evidence: dict, **changes):
    value = {
        "step": step,
        "tool": tool,
        "verified": True,
        "outcome": "success",
        "code": code,
        "summary": "verified",
        "evidence": json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
    }
    value.update(changes)
    return value


def _read_observation(step: int = 1, content: str = "alpha evidence"):
    encoded = content.encode("utf-8")
    return _observation(
        step,
        "workspace_read",
        "workspace_read_completed",
        {
            "path": "docs/sample.md",
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
            "offset": 0,
            "length": len(encoded),
            "nextOffset": len(encoded),
            "eof": True,
            "content": content,
            "truncated": False,
        },
    )


def _web_observation(step: int = 2, url: str = "https://example.com/source"):
    return _observation(
        step,
        "web_search",
        "web_search_completed",
        {
            "query": "public test query",
            "results": [
                {
                    "title": "Public source title",
                    "snippet": "Public source body sentinel.",
                    "url": url,
                }
            ],
        },
    )


def _draft(fragment, *, kind: str = "summarize"):
    return {
        "schema": GROUNDED_DRAFT_SCHEMA,
        "taskId": TASK_ID,
        "kind": kind,
        "sections": [
            {
                "title": "핵심",
                "claims": [
                    {
                        "text": "근거에 연결된 검토 대상 주장이다.",
                        "stepId": fragment.step_id,
                        "evidenceRef": fragment.evidence_ref,
                    }
                ],
            }
        ],
        "semanticVerified": False,
        "humanReviewRequired": True,
    }


class TaskGroundedDraftRuntimeTests(unittest.TestCase):
    def test_kind_and_link_intent_are_exact_and_negation_safe(self) -> None:
        cases = {
            "문서를 검토해줘": "review",
            "문서를 요약해줘": "summarize",
            "이 동작을 설명해줘": "explain",
            "두 방식을 비교해줘": "compare",
            "please review this": "review",
        }
        for goal, expected in cases.items():
            with self.subTest(goal=goal):
                self.assertEqual(grounded_draft_kind(goal), expected)
        self.assertIsNone(grounded_draft_kind("요약하고 비교해줘"))
        self.assertIsNone(grounded_draft_kind("설명하지 마"))
        self.assertTrue(explicit_link_requested("출처 링크도 포함해서 요약해줘"))
        self.assertFalse(explicit_link_requested("링크는 빼고 요약해줘"))
        self.assertFalse(explicit_link_requested("링크 없이 요약해줘"))
        self.assertFalse(explicit_link_requested("URL은 넣지 말고 요약해줘"))
        self.assertFalse(explicit_link_requested("그냥 요약해줘"))

    def test_only_verified_current_read_diff_and_search_receipts_make_fragments(self) -> None:
        read = _read_observation()
        diff = _observation(
            2,
            "workspace_diff",
            "workspace_diff_completed",
            {
                "diff": "+safe change",
                "stderr": "",
                "exitCode": 0,
                "truncated": False,
                "paths": ["sample.py"],
            },
        )
        search = _observation(
            3,
            "workspace_search",
            "workspace_search_completed",
            {
                "path": "src",
                "query": "needle",
                "matches": [{"path": "src/a.py", "line": 3, "text": "needle = 1"}],
                "truncated": False,
            },
        )
        web = _web_observation(4)
        ignored = _observation(
            5,
            "runtime_status",
            "runtime_status_collected",
            {"schema": "runtime_health.public.v1", "ok": True},
        )
        unverified = _read_observation(6)
        unverified["verified"] = False

        fragments = grounded_evidence_fragments(
            TASK_ID,
            [read, diff, search, web, ignored, unverified],
        )

        self.assertEqual(
            {fragment.tool for fragment in fragments},
            {"workspace_read", "workspace_diff", "workspace_search", "web_search"},
        )
        self.assertTrue(all(fragment.task_id == TASK_ID for fragment in fragments))
        self.assertTrue(
            all(fragment.evidence_ref.startswith("evref-") for fragment in fragments)
        )
        self.assertEqual(
            fragments,
            grounded_evidence_fragments(TASK_ID, [read, diff, search, web, ignored, unverified]),
        )
        self.assertNotEqual(
            fragments[0].evidence_ref,
            grounded_evidence_fragments("task-other", [read])[0].evidence_ref,
        )

    def test_manifest_is_bounded_and_contains_no_url(self) -> None:
        fragments = grounded_evidence_fragments(TASK_ID, [_web_observation()])
        manifest = grounded_evidence_manifest(
            task_id=TASK_ID,
            kind="summarize",
            fragments=fragments,
        )
        encoded = json.dumps(manifest, ensure_ascii=False)
        self.assertEqual(manifest["semanticVerified"], False)
        self.assertIn("Public source body sentinel.", encoded)
        self.assertNotIn("https://example.com/source", encoded)
        self.assertNotIn("verified_url", encoded)

    def test_exact_draft_binds_every_claim_to_current_step_and_reference(self) -> None:
        fragments = grounded_evidence_fragments(TASK_ID, [_read_observation()])
        draft = validate_grounded_draft(
            _draft(fragments[0]),
            task_id=TASK_ID,
            expected_kind="summarize",
            fragments=fragments,
        )
        self.assertEqual(draft.to_dict(), _draft(fragments[0]))
        self.assertFalse(draft.to_dict()["semanticVerified"])
        self.assertTrue(draft.to_dict()["humanReviewRequired"])

        invalid_values = []
        fabricated = _draft(fragments[0])
        fabricated["sections"][0]["claims"][0]["evidenceRef"] = "evref-" + "0" * 64
        invalid_values.append(fabricated)
        wrong_step = _draft(fragments[0])
        wrong_step["sections"][0]["claims"][0]["stepId"] = 99
        invalid_values.append(wrong_step)
        cross_run = _draft(fragments[0])
        cross_run["taskId"] = "task-other"
        invalid_values.append(cross_run)
        semantic_false_green = _draft(fragments[0])
        semantic_false_green["semanticVerified"] = True
        invalid_values.append(semantic_false_green)
        url_smuggling = _draft(fragments[0])
        url_smuggling["sections"][0]["claims"][0]["text"] = "See https://private.invalid"
        invalid_values.append(url_smuggling)
        markdown_url_smuggling = _draft(fragments[0])
        markdown_url_smuggling["sections"][0]["claims"][0]["text"] = (
            "[unverified source](//private.invalid)"
        )
        invalid_values.append(markdown_url_smuggling)
        title_url_smuggling = _draft(fragments[0])
        title_url_smuggling["sections"][0]["title"] = "https://private.invalid"
        invalid_values.append(title_url_smuggling)
        extra = _draft(fragments[0])
        extra["rawSource"] = "must fail"
        invalid_values.append(extra)
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(GroundedDraftError):
                validate_grounded_draft(
                    value,
                    task_id=TASK_ID,
                    expected_kind="summarize",
                    fragments=fragments,
                )

    def test_https_policy_removes_credentials_local_private_and_redirect_urls(self) -> None:
        self.assertEqual(
            safe_verified_https_url("https://example.com/source#fragment"),
            "https://example.com/source",
        )
        rejected = (
            "http://example.com/source",
            "https://user:password@example.com/source",
            "https://localhost/source",
            "https://127.0.0.1/source",
            "https://10.0.0.1/source",
            "https://service.internal/source",
            "https://example.com/out?redirect=https://other.example",
            "https://example.com/out?value=https://other.example",
            "https://example.com/source?token=secret",
            "https://example.com/source%0Ainjected",
            "https://example.com/source\x00tail",
            "https://bit.ly/example",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertEqual(safe_verified_https_url(url), "")
        self.assertEqual(
            safe_verified_https_url("https://example.com/source", redirected=True),
            "",
        )

    def test_render_shows_only_claim_and_label_until_link_is_explicit(self) -> None:
        fragments = grounded_evidence_fragments(TASK_ID, [_web_observation()])
        draft = validate_grounded_draft(
            _draft(fragments[0]),
            task_id=TASK_ID,
            expected_kind="summarize",
            fragments=fragments,
        )
        without_link = render_grounded_draft(draft, fragments, include_links=False)
        with_link = render_grounded_draft(draft, fragments, include_links=True)
        self.assertNotIn("https://example.com/source", without_link)
        self.assertNotIn("example.com", without_link)
        self.assertIn("web:example", without_link)
        self.assertIn("https://example.com/source", with_link)
        self.assertNotIn("Public source body sentinel.", without_link)
        self.assertNotIn("Public source body sentinel.", with_link)
        self.assertNotIn("https://", GROUNDED_DRAFT_TTS_TEXT)
        self.assertNotIn("Public source body sentinel.", GROUNDED_DRAFT_TTS_TEXT)

    def test_terminal_payload_rejects_cross_run_and_stale_references(self) -> None:
        observations = [_read_observation()]
        fragments = grounded_evidence_fragments(TASK_ID, observations)
        payload = {
            "schema": "evelyn.task-loop.v1",
            "taskId": TASK_ID,
            "status": "grounded_draft_ready",
            "code": "grounded_draft_ready",
            "summary": "reviewable draft",
            "stepCount": 1,
            "modelCallCount": 2,
            "approvalTool": "",
            "observations": observations,
            "groundedDraft": _draft(fragments[0]),
        }

        draft, rebuilt = grounded_draft_from_task_payload(
            payload,
            goal="자료를 요약해줘",
        )

        self.assertEqual(draft.to_dict(), payload["groundedDraft"])
        self.assertEqual(rebuilt, fragments)
        stale = copy.deepcopy(payload)
        stale["observations"] = [_web_observation()]
        with self.assertRaises(GroundedDraftError):
            grounded_draft_from_task_payload(stale, goal="자료를 요약해줘")
        cross_run = copy.deepcopy(payload)
        cross_run["taskId"] = "task-other"
        with self.assertRaises(GroundedDraftError):
            grounded_draft_from_task_payload(cross_run, goal="자료를 요약해줘")


if __name__ == "__main__":
    unittest.main()
