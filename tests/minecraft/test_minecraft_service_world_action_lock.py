from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from aiohttp import web


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import mindcraft_service  # noqa: E402
from evelyn_core import voyager_service  # noqa: E402
from evelyn_core.minecraft_owner_lock import (  # noqa: E402
    MinecraftOwnerLock,
    MinecraftOwnerLockBusy,
    MinecraftOwnerLockUnavailable,
)


class FakeRequest:
    can_read_body = True

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = dict(payload)

    async def json(self) -> dict[str, object]:
        return dict(self.payload)


class BlockingMindcraftState:
    def __init__(
        self,
        effect_entered: threading.Event,
        allow_effect: threading.Event,
    ) -> None:
        self.effect_entered = effect_entered
        self.allow_effect = allow_effect
        self.started_with: list[str] = []
        self.status_lock_observations: list[
            tuple[bool, Path | None]
        ] = []

    def get_goal(self) -> str:
        return "existing goal"

    def start(self, goal: str) -> None:
        self.effect_entered.set()
        if not self.allow_effect.wait(timeout=5.0):
            raise RuntimeError("mindcraft effect gate timed out")
        self.started_with.append(goal)

    def build_status(
        self,
        *,
        world_action_lock: MinecraftOwnerLock | None = None,
    ) -> dict[str, object]:
        self.status_lock_observations.append(
            (
                bool(
                    world_action_lock is not None
                    and world_action_lock.acquired
                ),
                (
                    world_action_lock.path
                    if world_action_lock is not None
                    else None
                ),
            )
        )
        return {"running": bool(self.started_with)}


class BlockingVoyagerState:
    runner_mode = ""

    def __init__(
        self,
        effect_entered: threading.Event,
        allow_effect: threading.Event,
    ) -> None:
        self.effect_entered = effect_entered
        self.allow_effect = allow_effect
        self.persisted_goals: list[str] = []

    def persist_goal_override(self, goal: str) -> None:
        self.effect_entered.set()
        if not self.allow_effect.wait(timeout=5.0):
            raise RuntimeError("voyager effect gate timed out")
        self.persisted_goals.append(goal)

    def _process_alive(self) -> bool:
        return False

    def build_status(self) -> dict[str, object]:
        return {"running": False, "goal": self.persisted_goals[-1]}


class MinecraftServiceWorldActionLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.lock_path = Path(self.temp_dir.name) / "world_action.lock"

    @staticmethod
    def _run_handler(
        handler,
        request: FakeRequest,
        responses: list[web.Response],
        errors: list[BaseException],
    ) -> None:
        try:
            responses.append(asyncio.run(handler(request)))
        except BaseException as exc:  # pragma: no cover - asserted by caller
            errors.append(exc)

    def test_mindcraft_start_holds_lock_from_validation_through_effect(
        self,
    ) -> None:
        validated = threading.Event()
        effect_entered = threading.Event()
        allow_effect = threading.Event()
        state = BlockingMindcraftState(effect_entered, allow_effect)
        responses: list[web.Response] = []
        errors: list[BaseException] = []

        def validate(*_args, **_kwargs) -> tuple[bool, str]:
            validated.set()
            return True, ""

        with (
            patch.object(mindcraft_service, "STATE", state),
            patch.object(
                mindcraft_service,
                "WORLD_ACTION_LOCK_PATH",
                self.lock_path,
            ),
            patch.object(
                mindcraft_service,
                "validate_world_lease_request",
                side_effect=validate,
            ),
        ):
            worker = threading.Thread(
                target=self._run_handler,
                args=(
                    mindcraft_service.start,
                    FakeRequest({"goal": "new goal"}),
                    responses,
                    errors,
                ),
                daemon=True,
            )
            worker.start()
            self.assertTrue(validated.wait(timeout=5.0))
            self.assertTrue(effect_entered.wait(timeout=5.0))
            contender = MinecraftOwnerLock(self.lock_path)
            try:
                with self.assertRaises(MinecraftOwnerLockBusy):
                    contender.acquire()
            finally:
                contender.release()
                allow_effect.set()
                worker.join(timeout=5.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual([response.status for response in responses], [200])
        self.assertEqual(state.started_with, ["new goal"])
        self.assertEqual(
            state.status_lock_observations,
            [(True, self.lock_path)],
        )
        self.assertTrue(self.lock_path.exists())

    def test_mindcraft_goal_reuses_acquired_lock_for_status_reconcile(
        self,
    ) -> None:
        observations: list[tuple[bool, Path | None]] = []
        state = Mock()

        def build_status(
            *,
            world_action_lock: MinecraftOwnerLock | None = None,
        ) -> dict[str, object]:
            observations.append(
                (
                    bool(
                        world_action_lock is not None
                        and world_action_lock.acquired
                    ),
                    (
                        world_action_lock.path
                        if world_action_lock is not None
                        else None
                    ),
                )
            )
            return {"running": True}

        state.build_status.side_effect = build_status
        with (
            patch.object(mindcraft_service, "STATE", state),
            patch.object(
                mindcraft_service,
                "WORLD_ACTION_LOCK_PATH",
                self.lock_path,
            ),
            patch.object(
                mindcraft_service,
                "validate_world_lease_request",
                return_value=(True, ""),
            ),
        ):
            response = asyncio.run(
                mindcraft_service.set_goal(
                    FakeRequest({"goal": "replacement goal"})
                )
            )

        self.assertEqual(response.status, 200)
        state.restart_for_goal.assert_called_once_with("replacement goal")
        self.assertEqual(observations, [(True, self.lock_path)])

    def test_voyager_goal_holds_lock_from_validation_through_effect(
        self,
    ) -> None:
        validated = threading.Event()
        effect_entered = threading.Event()
        allow_effect = threading.Event()
        state = BlockingVoyagerState(effect_entered, allow_effect)
        responses: list[web.Response] = []
        errors: list[BaseException] = []

        def validate(*_args, **_kwargs) -> tuple[bool, str]:
            validated.set()
            return True, ""

        with (
            patch.object(voyager_service, "STATE", state),
            patch.object(
                voyager_service,
                "WORLD_ACTION_LOCK_PATH",
                self.lock_path,
            ),
            patch.object(
                voyager_service,
                "validate_world_lease_request",
                side_effect=validate,
            ),
        ):
            worker = threading.Thread(
                target=self._run_handler,
                args=(
                    voyager_service.set_goal,
                    FakeRequest({"goal": "replacement goal"}),
                    responses,
                    errors,
                ),
                daemon=True,
            )
            worker.start()
            self.assertTrue(validated.wait(timeout=5.0))
            self.assertTrue(effect_entered.wait(timeout=5.0))
            contender = MinecraftOwnerLock(self.lock_path)
            try:
                with self.assertRaises(MinecraftOwnerLockBusy):
                    contender.acquire()
            finally:
                contender.release()
                allow_effect.set()
                worker.join(timeout=5.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual([response.status for response in responses], [200])
        self.assertEqual(state.persisted_goals, ["replacement goal"])
        self.assertTrue(self.lock_path.exists())

    def test_all_admitted_mutations_map_busy_to_fixed_503(self) -> None:
        cases = (
            (mindcraft_service, mindcraft_service.start),
            (mindcraft_service, mindcraft_service.set_goal),
            (voyager_service, voyager_service.start),
            (voyager_service, voyager_service.set_goal),
        )
        for index, (module, handler) in enumerate(cases):
            lock_path = self.lock_path.with_name(f"busy-{index}.lock")
            holder = MinecraftOwnerLock(lock_path)
            holder.acquire()
            validator = Mock(return_value=(True, ""))
            try:
                with (
                    self.subTest(module=module.__name__, handler=handler.__name__),
                    patch.object(module, "WORLD_ACTION_LOCK_PATH", lock_path),
                    patch.object(
                        module,
                        "validate_world_lease_request",
                        validator,
                    ),
                ):
                    with self.assertRaises(
                        web.HTTPServiceUnavailable
                    ) as raised:
                        asyncio.run(
                            handler(FakeRequest({"goal": "test goal"}))
                        )
                    self.assertEqual(raised.exception.status_code, 503)
                    self.assertEqual(
                        json.loads(raised.exception.text),
                        {"error": "minecraft_world_action_lock_busy"},
                    )
                    validator.assert_not_called()
            finally:
                holder.release()

    def test_all_admitted_mutations_map_unavailable_to_fixed_503(
        self,
    ) -> None:
        cases = (
            (mindcraft_service, mindcraft_service.start),
            (mindcraft_service, mindcraft_service.set_goal),
            (voyager_service, voyager_service.start),
            (voyager_service, voyager_service.set_goal),
        )
        for index, (module, handler) in enumerate(cases):
            validator = Mock(return_value=(True, ""))
            with (
                self.subTest(module=module.__name__, handler=handler.__name__),
                patch.object(
                    module,
                    "WORLD_ACTION_LOCK_PATH",
                    self.lock_path.with_name(f"unavailable-{index}.lock"),
                ),
                patch.object(
                    module,
                    "validate_world_lease_request",
                    validator,
                ),
                patch.object(
                    module.MinecraftOwnerLock,
                    "acquire",
                    side_effect=MinecraftOwnerLockUnavailable(
                        "minecraft_owner_lock_unavailable"
                    ),
                ),
            ):
                with self.assertRaises(
                    web.HTTPServiceUnavailable
                ) as raised:
                    asyncio.run(
                        handler(FakeRequest({"goal": "test goal"}))
                    )
                self.assertEqual(raised.exception.status_code, 503)
                self.assertEqual(
                    json.loads(raised.exception.text),
                    {
                        "error": (
                            "minecraft_world_action_lock_unavailable"
                        )
                    },
                )
                validator.assert_not_called()

    def test_rejected_proof_releases_lock_for_every_mutation(self) -> None:
        cases = (
            (mindcraft_service, mindcraft_service.start),
            (mindcraft_service, mindcraft_service.set_goal),
            (voyager_service, voyager_service.start),
            (voyager_service, voyager_service.set_goal),
        )
        for index, (module, handler) in enumerate(cases):
            lock_path = self.lock_path.with_name(f"rejected-{index}.lock")
            with (
                self.subTest(module=module.__name__, handler=handler.__name__),
                patch.object(module, "WORLD_ACTION_LOCK_PATH", lock_path),
                patch.object(
                    module,
                    "validate_world_lease_request",
                    return_value=(False, "minecraft_world_lease_expired"),
                ),
            ):
                with self.assertRaises(web.HTTPForbidden):
                    asyncio.run(
                        handler(FakeRequest({"goal": "test goal"}))
                    )

            probe = MinecraftOwnerLock(lock_path)
            try:
                probe.acquire()
                self.assertTrue(probe.acquired)
            finally:
                probe.release()
            self.assertTrue(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
