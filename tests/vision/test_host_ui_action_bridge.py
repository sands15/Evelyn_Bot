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

from evelyn_core.host_ui_action_bridge import HostUiActionBridge  # noqa: E402
from evelyn_core.host_ui_action_contract import (  # noqa: E402
    HOST_UI_ACTION_REQUEST_SCHEMA,
)
from evelyn_core.ui_action_target import UiActionTargetManager  # noqa: E402


ELEMENT_ID = "a" * 20


def observation(
    *,
    now: float,
    title: str = "Evelyn",
    include_target: bool = True,
    enabled: bool = True,
) -> dict:
    elements = (
        [
            {
                "elementId": ELEMENT_ID,
                "name": "확인",
                "automationId": "confirm",
                "controlType": "Button",
                "isEnabled": enabled,
                "bounds": {
                    "x": 1.0,
                    "y": 2.0,
                    "width": 80.0,
                    "height": 30.0,
                },
            }
        ]
        if include_target
        else []
    )
    return {
        "schema": "windows_accessibility.observation.v1",
        "attempted": True,
        "available": True,
        "capturedAt": now,
        "windowTitle": title,
        "windowClass": "Chrome_WidgetWin_1",
        "truncated": False,
        "elements": elements,
        "text": "Window: Evelyn\nButton: 확인",
    }


class FakeAccessibility:
    def __init__(self, observations: list[dict]) -> None:
        self.observations = list(observations)
        self.calls = 0

    async def read(self) -> dict:
        self.calls += 1
        if not self.observations:
            raise RuntimeError("no_fake_observation")
        return self.observations.pop(0)


class FakeInvoker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def invoke(self, **kwargs) -> dict:
        self.calls.append(dict(kwargs))
        return {
            "schema": "windows_ui_action.result.v1",
            "ok": True,
            "errorCode": "",
            "completedAt": 1000.0,
            "executed": True,
            **kwargs,
        }


def write_request(
    path: Path,
    *,
    request_id: str,
    now: float,
    operation: str,
    action: str = "",
    element_id: str = "",
    postcondition: str = "",
    confirm_token: str = "",
    extra: dict | None = None,
) -> None:
    payload = {
        "schema": HOST_UI_ACTION_REQUEST_SCHEMA,
        "requestId": request_id,
        "createdAt": now,
        "expiresAt": now + 15.0,
        "operation": operation,
        "action": action,
        "elementId": element_id,
        "postcondition": postcondition,
        "confirmToken": confirm_token,
    }
    if extra:
        payload.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class HostUiActionBridgeTests(unittest.IsolatedAsyncioTestCase):
    def build_bridge(
        self,
        root: Path,
        *,
        observations: list[dict],
    ) -> tuple[HostUiActionBridge, FakeAccessibility, FakeInvoker]:
        accessibility = FakeAccessibility(observations)
        invoker = FakeInvoker()
        manager = UiActionTargetManager(
            status_path=root / "host_ui_action" / "authorization.json",
            events_dir=root / "host_ui_action" / "events",
            now=lambda: 1000.0,
            process_nonce="test-process",
        )
        bridge = HostUiActionBridge(
            artifacts_root=root,
            accessibility=accessibility,
            invoker=invoker,
            manager=manager,
            now=lambda: 1000.0,
        )
        return bridge, accessibility, invoker

    async def test_preview_and_apply_reobserve_execute_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            bridge, accessibility, invoker = self.build_bridge(
                root,
                observations=[
                    observation(now=1000.0),
                    observation(now=1000.0),
                    observation(now=1000.0, include_target=False),
                ],
            )
            preview_id = "a" * 32
            preview_path = (
                root
                / "host_ui_action"
                / "requests"
                / f"{preview_id}.json"
            )
            write_request(
                preview_path,
                request_id=preview_id,
                now=1000.0,
                operation="preview",
                action="invoke",
                element_id=ELEMENT_ID,
                postcondition="target_absent",
            )
            await bridge.process_pending()
            preview_response = json.loads(
                (
                    root
                    / "host_ui_action"
                    / "responses"
                    / f"{preview_id}.json"
                ).read_text(encoding="utf-8")
            )
            token = preview_response["preview"]["confirmToken"]
            apply_id = "b" * 32
            apply_path = (
                root
                / "host_ui_action"
                / "requests"
                / f"{apply_id}.json"
            )
            write_request(
                apply_path,
                request_id=apply_id,
                now=1000.0,
                operation="apply",
                confirm_token=token,
            )
            await bridge.process_pending()
            apply_response = json.loads(
                (
                    root
                    / "host_ui_action"
                    / "responses"
                    / f"{apply_id}.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(preview_response["ok"])
        self.assertEqual(preview_response["preview"]["target"]["name"], "확인")
        self.assertTrue(apply_response["ok"])
        self.assertTrue(apply_response["result"]["verified"])
        self.assertEqual(accessibility.calls, 3)
        self.assertEqual(len(invoker.calls), 1)
        self.assertEqual(invoker.calls[0]["action"], "invoke")
        self.assertNotIn("확인", json.dumps(bridge.snapshot(), ensure_ascii=False))

    async def test_changed_window_consumes_token_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            bridge, _accessibility, invoker = self.build_bridge(
                root,
                observations=[
                    observation(now=1000.0),
                    observation(now=1000.0, title="Other"),
                ],
            )
            preview_id = "c" * 32
            write_request(
                root
                / "host_ui_action"
                / "requests"
                / f"{preview_id}.json",
                request_id=preview_id,
                now=1000.0,
                operation="preview",
                action="invoke",
                element_id=ELEMENT_ID,
                postcondition="target_absent",
            )
            await bridge.process_pending()
            preview = json.loads(
                (
                    root
                    / "host_ui_action"
                    / "responses"
                    / f"{preview_id}.json"
                ).read_text(encoding="utf-8")
            )["preview"]
            apply_id = "d" * 32
            write_request(
                root
                / "host_ui_action"
                / "requests"
                / f"{apply_id}.json",
                request_id=apply_id,
                now=1000.0,
                operation="apply",
                confirm_token=preview["confirmToken"],
            )
            await bridge.process_pending()
            response = json.loads(
                (
                    root
                    / "host_ui_action"
                    / "responses"
                    / f"{apply_id}.json"
                ).read_text(encoding="utf-8")
            )

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["errorCode"],
            "ui_action_foreground_changed_since_preview",
        )
        self.assertEqual(invoker.calls, [])

    async def test_unknown_command_or_path_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            bridge, accessibility, invoker = self.build_bridge(
                root,
                observations=[observation(now=1000.0)],
            )
            request_id = "e" * 32
            write_request(
                root
                / "host_ui_action"
                / "requests"
                / f"{request_id}.json",
                request_id=request_id,
                now=1000.0,
                operation="preview",
                action="invoke",
                element_id=ELEMENT_ID,
                postcondition="target_absent",
                extra={"command": "calc.exe", "path": "C:/private"},
            )
            await bridge.process_pending()
            response = json.loads(
                (
                    root
                    / "host_ui_action"
                    / "responses"
                    / f"{request_id}.json"
                ).read_text(encoding="utf-8")
            )

        self.assertFalse(response["ok"])
        self.assertEqual(response["errorCode"], "ui_action_invalid_request")
        self.assertEqual(accessibility.calls, 0)
        self.assertEqual(invoker.calls, [])


if __name__ == "__main__":
    unittest.main()
