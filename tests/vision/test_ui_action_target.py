from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.ui_action_target import (  # noqa: E402
    UI_ACTION_DISCOVERY_MAX_TARGETS,
    UI_ACTION_EVENT_SCHEMA,
    UI_ACTION_STATUS_SCHEMA,
    UI_ACTION_TOKEN_RECORD_RETENTION_SEC,
    UiActionTargetManager,
)


ELEMENT_ID = "a" * 20
WINDOW_TITLE = "Evelyn Settings"
WINDOW_CLASS = "Chrome_WidgetWin_1"


def observation(
    *,
    now: float,
    title: str = WINDOW_TITLE,
    class_name: str = WINDOW_CLASS,
    element_id: str = ELEMENT_ID,
    name: str = "저장",
    enabled: bool = True,
    include_target: bool = True,
) -> dict:
    elements = []
    if include_target:
        elements.append(
            {
                "elementId": element_id,
                "name": name,
                "automationId": "save-button",
                "controlType": "Button",
                "isEnabled": enabled,
                "bounds": {
                    "x": 10.0,
                    "y": 20.0,
                    "width": 80.0,
                    "height": 30.0,
                },
            }
        )
    return {
        "schema": "windows_accessibility.observation.v1",
        "attempted": True,
        "available": True,
        "capturedAt": now,
        "windowTitle": title,
        "windowClass": class_name,
        "truncated": False,
        "elements": elements,
        "text": "private UI text that must not be journaled",
    }


class UiActionTargetManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = 1000.0
        self.manager = UiActionTargetManager(
            status_path=self.root / "status.json",
            events_dir=self.root / "events",
            now=lambda: self.clock,
            process_nonce="process-a",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def preview(self, **kwargs):
        return self.manager.preview(
            observation=kwargs.pop(
                "observation",
                observation(now=self.clock),
            ),
            element_id=kwargs.pop("element_id", ELEMENT_ID),
            action=kwargs.pop("action", "invoke"),
            postcondition=kwargs.pop("postcondition", "target_absent"),
            **kwargs,
        )

    def begin(self, preview: dict, *, current: dict | None = None) -> dict:
        return self.manager.begin_apply(
            confirm_token=preview["confirmToken"],
            observation=current or observation(now=self.clock),
        )

    def execute_result(self, begin: dict, *, ok: bool = True) -> dict:
        execution = begin["execution"]
        return {
            "schema": "windows_ui_action.result.v1",
            "ok": ok,
            "errorCode": "" if ok else "ui_action_execution_failed",
            "completedAt": self.clock,
            "executed": ok,
            **execution,
        }

    def test_preview_binds_visible_button_and_is_content_free_on_disk(self) -> None:
        preview = self.preview()

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["target"]["name"], "저장")
        self.assertTrue(preview["requiresExplicitConfirmation"])
        status = json.loads(
            (self.root / "status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(status["schema"], UI_ACTION_STATUS_SCHEMA)
        self.assertEqual(status["activePreviewCount"], 1)
        event_text = next((self.root / "events").glob("*.jsonl")).read_text(
            encoding="utf-8"
        )
        self.assertIn(UI_ACTION_EVENT_SCHEMA, event_text)
        self.assertNotIn("저장", event_text)
        self.assertNotIn(WINDOW_TITLE, event_text)
        self.assertNotIn("private UI text", event_text)
        self.assertNotIn(ELEMENT_ID, event_text)

    def test_discover_lists_only_enabled_buttons_without_authority(
        self,
    ) -> None:
        current = observation(now=self.clock)
        current["elements"].append(
            {
                "elementId": "b" * 20,
                "name": "사용 불가",
                "automationId": "disabled-button",
                "controlType": "Button",
                "isEnabled": False,
                "bounds": {
                    "x": 100.0,
                    "y": 20.0,
                    "width": 80.0,
                    "height": 30.0,
                },
            }
        )

        discovered = self.manager.discover(observation=current)
        status = self.manager.status()
        persisted = "\n".join(
            path.read_text(encoding="utf-8")
            for path in self.root.rglob("*")
            if path.is_file()
        )

        self.assertTrue(discovered["ok"])
        self.assertEqual(discovered["schema"], "ui_action.targets.v1")
        self.assertEqual(
            [target["name"] for target in discovered["targets"]],
            ["저장"],
        )
        self.assertTrue(discovered["policy"]["requiresPreview"])
        self.assertTrue(
            discovered["policy"]["requiresExplicitConfirmation"]
        )
        self.assertEqual(status["activePreviewCount"], 0)
        self.assertEqual(status["discoveryCount"], 1)
        self.assertEqual(status["state"], "authorization_required")
        self.assertNotIn(WINDOW_TITLE, persisted)
        self.assertNotIn("저장", persisted)
        self.assertNotIn("사용 불가", persisted)

    def test_discover_bounds_large_button_lists(self) -> None:
        current = observation(now=self.clock, include_target=False)
        current["elements"] = [
            {
                "elementId": f"{index:020x}",
                "name": f"Button {index}",
                "automationId": f"button-{index}",
                "controlType": "Button",
                "isEnabled": True,
                "bounds": {
                    "x": float(index),
                    "y": 20.0,
                    "width": 80.0,
                    "height": 30.0,
                },
            }
            for index in range(UI_ACTION_DISCOVERY_MAX_TARGETS + 5)
        ]

        discovered = self.manager.discover(observation=current)

        self.assertTrue(discovered["ok"])
        self.assertTrue(discovered["truncated"])
        self.assertEqual(
            len(discovered["targets"]),
            UI_ACTION_DISCOVERY_MAX_TARGETS,
        )

    def test_discover_preserves_source_truncation(self) -> None:
        current = observation(now=self.clock)
        current["truncated"] = True

        discovered = self.manager.discover(observation=current)

        self.assertTrue(discovered["ok"])
        self.assertTrue(discovered["truncated"])

    def test_apply_reobserves_exact_target_and_verifies_absence(self) -> None:
        preview = self.preview()
        begin = self.begin(preview)
        result = self.manager.finish_apply(
            operation_id=begin["operationId"],
            execution_result=self.execute_result(begin),
            post_observation=observation(
                now=self.clock,
                include_target=False,
            ),
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["executed"])
        self.assertTrue(result["verified"])
        self.assertFalse(result["automaticRetry"])

    def test_apply_verifies_disabled_and_changed_window_postconditions(self) -> None:
        disabled_preview = self.preview(postcondition="target_disabled")
        disabled_begin = self.begin(disabled_preview)
        disabled = self.manager.finish_apply(
            operation_id=disabled_begin["operationId"],
            execution_result=self.execute_result(disabled_begin),
            post_observation=observation(now=self.clock, enabled=False),
        )
        changed_preview = self.preview(postcondition="window_changed")
        changed_begin = self.begin(changed_preview)
        changed = self.manager.finish_apply(
            operation_id=changed_begin["operationId"],
            execution_result=self.execute_result(changed_begin),
            post_observation=observation(
                now=self.clock,
                title="Different Window",
                include_target=False,
            ),
        )

        self.assertTrue(disabled["verified"])
        self.assertTrue(changed["verified"])

    def test_token_is_single_use_and_expires(self) -> None:
        preview = self.preview()
        first = self.begin(preview)
        reused = self.manager.begin_apply(
            confirm_token=preview["confirmToken"],
            observation=observation(now=self.clock),
        )
        expired_preview = self.preview()
        self.clock += 31.0
        expired = self.manager.begin_apply(
            confirm_token=expired_preview["confirmToken"],
            observation=observation(now=self.clock),
        )

        self.assertTrue(first["ok"])
        self.assertEqual(reused["error"], "ui_action_confirm_token_reused")
        self.assertEqual(expired["error"], "ui_action_confirm_token_expired")

    def test_denials_are_audited_and_old_token_records_are_pruned(self) -> None:
        preview = self.preview()
        self.begin(preview)
        denied = self.manager.begin_apply(
            confirm_token=preview["confirmToken"],
            observation=observation(now=self.clock),
        )
        self.clock += UI_ACTION_TOKEN_RECORD_RETENTION_SEC + 1.0
        status = self.manager.status()
        event_text = next((self.root / "events").glob("*.jsonl")).read_text(
            encoding="utf-8"
        )

        self.assertEqual(denied["error"], "ui_action_confirm_token_reused")
        self.assertEqual(status["activePreviewCount"], 0)
        self.assertEqual(self.manager._tokens, {})
        self.assertIn("action_denied", event_text)
        self.assertIn("ui_action_confirm_token_reused", event_text)

    def test_stale_preview_and_stale_apply_observation_fail_closed(self) -> None:
        stale_preview = self.preview(
            observation=observation(now=self.clock - 6.0)
        )
        preview = self.preview()
        stale_apply = self.manager.begin_apply(
            confirm_token=preview["confirmToken"],
            observation=observation(now=self.clock - 6.0),
        )

        self.assertEqual(
            stale_preview["error"],
            "ui_action_observation_stale",
        )
        self.assertEqual(
            stale_apply["error"],
            "ui_action_observation_stale",
        )

    def test_window_target_and_enabled_state_changes_consume_token(self) -> None:
        cases = [
            (
                observation(now=self.clock, title="Other"),
                "ui_action_foreground_changed_since_preview",
            ),
            (
                observation(now=self.clock, name="다른 이름"),
                "ui_action_target_changed_since_preview",
            ),
            (
                observation(now=self.clock, enabled=False),
                "ui_action_target_disabled",
            ),
        ]
        for current, expected in cases:
            with self.subTest(expected=expected):
                preview = self.preview()
                result = self.manager.begin_apply(
                    confirm_token=preview["confirmToken"],
                    observation=current,
                )
                reused = self.manager.begin_apply(
                    confirm_token=preview["confirmToken"],
                    observation=observation(now=self.clock),
                )
                self.assertEqual(result["error"], expected)
                self.assertEqual(
                    reused["error"],
                    "ui_action_confirm_token_reused",
                )

    def test_unverified_outcome_never_becomes_success_or_retry(self) -> None:
        preview = self.preview()
        begin = self.begin(preview)
        result = self.manager.finish_apply(
            operation_id=begin["operationId"],
            execution_result=self.execute_result(begin),
            post_observation=observation(now=self.clock),
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["executed"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["state"], "outcome_unverified")
        self.assertFalse(result["automaticRetry"])

    def test_executor_contract_mismatch_fails_without_claiming_execution(self) -> None:
        preview = self.preview()
        begin = self.begin(preview)
        forged = self.execute_result(begin)
        forged["elementId"] = "b" * 20
        result = self.manager.finish_apply(
            operation_id=begin["operationId"],
            execution_result=forged,
            post_observation=observation(
                now=self.clock,
                include_target=False,
            ),
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["executed"])
        self.assertEqual(result["state"], "execution_failed")

    def test_executor_extra_fields_and_stale_result_fail_closed(self) -> None:
        for mutation in (
            lambda value: value.update({"command": "calc.exe"}),
            lambda value: value.update({"completedAt": self.clock - 6.0}),
        ):
            with self.subTest(mutation=mutation):
                preview = self.preview()
                begin = self.begin(preview)
                execution_result = self.execute_result(begin)
                mutation(execution_result)
                result = self.manager.finish_apply(
                    operation_id=begin["operationId"],
                    execution_result=execution_result,
                    post_observation=observation(
                        now=self.clock,
                        include_target=False,
                    ),
                )
                self.assertFalse(result["ok"])
                self.assertFalse(result["executed"])
                self.assertEqual(result["state"], "execution_failed")

    def test_restart_does_not_restore_confirmation_token(self) -> None:
        preview = self.preview()
        restarted = UiActionTargetManager(
            status_path=self.root / "status-2.json",
            events_dir=self.root / "events",
            now=lambda: self.clock,
            process_nonce="process-b",
        )

        result = restarted.begin_apply(
            confirm_token=preview["confirmToken"],
            observation=observation(now=self.clock),
        )

        self.assertEqual(result["error"], "ui_action_confirm_token_invalid")

    def test_only_named_enabled_visible_buttons_are_previewable(self) -> None:
        invalid_action = self.preview(action="click")
        invalid_postcondition = self.preview(postcondition="anything_changed")
        unnamed = self.preview(
            observation=observation(now=self.clock, name="")
        )
        disabled = self.preview(
            observation=observation(now=self.clock, enabled=False)
        )

        self.assertEqual(invalid_action["error"], "ui_action_not_allowed")
        self.assertEqual(
            invalid_postcondition["error"],
            "ui_action_postcondition_not_allowed",
        )
        self.assertEqual(unnamed["error"], "ui_action_target_missing")
        self.assertEqual(disabled["error"], "ui_action_target_disabled")


if __name__ == "__main__":
    unittest.main()
