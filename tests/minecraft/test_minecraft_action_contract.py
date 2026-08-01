from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.autonomy import (  # noqa: E402
    AutonomyExecutionContext,
)
from evelyn_core.minecraft_action_contract import (  # noqa: E402
    MINECRAFT_ACTION_DISPATCH_SCHEMA,
    MINECRAFT_ACTION_REQUEST_SCHEMA,
    MINECRAFT_ACTION_RESULT_SCHEMA,
    MinecraftActionContractError,
    bind_minecraft_action_request,
    build_minecraft_action_request,
    validate_minecraft_action_request,
    validate_minecraft_action_dispatch,
    validate_minecraft_action_result,
)


class MinecraftActionContractTests(unittest.TestCase):
    def context(self) -> AutonomyExecutionContext:
        return AutonomyExecutionContext(
            guild_id=7,
            action_key="minecraft:find_food_source",
            action_run_id="action-run-1",
            authorization_grant_id="grant-1",
        )

    def request(self) -> dict:
        return build_minecraft_action_request(
            {
                "domain": "minecraft",
                "action": "find_food_source",
                "reason": "low_health_no_food",
            },
            context=self.context(),
        )

    def bound_request(self) -> dict:
        return bind_minecraft_action_request(
            self.request(),
            goal_run_id="goal-run-1",
            lease_id="lease-1",
            lease_process_nonce="lease-process-1",
        )

    def result(self) -> dict:
        request = self.bound_request()
        return {
            "schema": MINECRAFT_ACTION_RESULT_SCHEMA,
            "status": "completed",
            "guildId": request["guildId"],
            "actionKey": request["actionKey"],
            "actionRunId": request["actionRunId"],
            "authorizationGrantId": request[
                "authorizationGrantId"
            ],
            "goalRunId": request["goalRunId"],
            "leaseId": request["leaseId"],
            "leaseProcessNonce": request[
                "leaseProcessNonce"
            ],
            "contractCode": "mindcraft_food_recovery.v1",
            "postconditionCode": "food_reserve_ready",
            "evidenceCode": (
                "minecraft_find_food_source_completed"
            ),
            "verified": True,
            "contentFree": True,
        }

    def dispatch(self, status: str = "accepted") -> dict:
        request = self.bound_request()
        return {
            "schema": MINECRAFT_ACTION_DISPATCH_SCHEMA,
            "status": status,
            "guildId": request["guildId"],
            "actionKey": request["actionKey"],
            "actionRunId": request["actionRunId"],
            "authorizationGrantId": request[
                "authorizationGrantId"
            ],
            "goalRunId": request["goalRunId"],
            "leaseId": request["leaseId"],
            "leaseProcessNonce": request[
                "leaseProcessNonce"
            ],
            "contractCode": request["contractCode"],
            "accepted": status in {"accepted", "running"},
            "contentFree": True,
            "errorCode": (
                "" if status in {"accepted", "running"}
                else "minecraft_action_failed"
            ),
        }

    def test_builds_fixed_content_free_request(self) -> None:
        request = self.request()

        self.assertEqual(
            request,
            {
                "schema": MINECRAFT_ACTION_REQUEST_SCHEMA,
                "guildId": 7,
                "actionKey": "minecraft:find_food_source",
                "actionRunId": "action-run-1",
                "authorizationGrantId": "grant-1",
                "contractCode": "mindcraft_food_recovery.v1",
                "parameters": {},
            },
        )

    def test_missing_execution_context_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            MinecraftActionContractError,
            "minecraft_action_context_required",
        ):
            build_minecraft_action_request(
                {
                    "domain": "minecraft",
                    "action": "find_food_source",
                },
                context=None,
            )

    def test_unsupported_action_and_arbitrary_fields_are_rejected(
        self,
    ) -> None:
        unsupported = self.context().__class__(
            guild_id=7,
            action_key="minecraft:generated_skill",
            action_run_id="action-run-1",
            authorization_grant_id="grant-1",
        )
        with self.assertRaisesRegex(
            MinecraftActionContractError,
            "minecraft_action_unsupported",
        ):
            build_minecraft_action_request(
                {
                    "domain": "minecraft",
                    "action": "generated_skill",
                },
                context=unsupported,
            )

        for forbidden in (
            "goal",
            "command",
            "code",
            "coordinates",
            "rawArguments",
        ):
            step = {
                "domain": "minecraft",
                "action": "find_food_source",
                forbidden: "private",
            }
            with self.assertRaisesRegex(
                MinecraftActionContractError,
                "minecraft_action_step_fields_invalid",
            ):
                build_minecraft_action_request(
                    step,
                    context=self.context(),
                )

    def test_request_requires_exact_fields_and_empty_parameters(
        self,
    ) -> None:
        request = self.request()
        request["extra"] = True
        with self.assertRaisesRegex(
            MinecraftActionContractError,
            "minecraft_action_request_fields_invalid",
        ):
            validate_minecraft_action_request(
                request,
                bound=False,
            )

        request = self.request()
        request["parameters"] = {"target": "bread"}
        with self.assertRaisesRegex(
            MinecraftActionContractError,
            "minecraft_action_parameters_invalid",
        ):
            validate_minecraft_action_request(
                request,
                bound=False,
            )

    def test_binding_requires_exact_typed_lease_identity(self) -> None:
        bound = self.bound_request()
        self.assertEqual(
            validate_minecraft_action_request(
                bound,
                bound=True,
            ),
            bound,
        )
        with self.assertRaisesRegex(
            MinecraftActionContractError,
            "minecraft_action_lease_process_invalid",
        ):
            bind_minecraft_action_request(
                self.request(),
                goal_run_id="goal-run-1",
                lease_id="lease-1",
                lease_process_nonce="bad value",
            )

    def test_result_requires_every_exact_correlation_field(
        self,
    ) -> None:
        result = self.result()
        self.assertEqual(
            validate_minecraft_action_result(
                result,
                expected_request=self.bound_request(),
            ),
            result,
        )

        for field in (
            "actionRunId",
            "authorizationGrantId",
            "goalRunId",
            "leaseId",
            "leaseProcessNonce",
            "contractCode",
            "postconditionCode",
            "evidenceCode",
            "verified",
        ):
            mismatched = self.result()
            mismatched[field] = (
                False if field == "verified" else "other"
            )
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    MinecraftActionContractError,
                    "minecraft_action_result_mismatch",
                ):
                    validate_minecraft_action_result(
                        mismatched,
                        expected_request=self.bound_request(),
                    )

    def test_dispatch_requires_exact_correlation_and_no_raw_fields(
        self,
    ) -> None:
        dispatch = self.dispatch()
        self.assertEqual(
            validate_minecraft_action_dispatch(
                dispatch,
                expected_request=self.bound_request(),
            ),
            dispatch,
        )
        for field in (
            "actionRunId",
            "goalRunId",
            "leaseId",
            "leaseProcessNonce",
            "contractCode",
        ):
            mismatched = self.dispatch()
            mismatched[field] = "other"
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    MinecraftActionContractError,
                    "minecraft_action_dispatch_mismatch",
                ):
                    validate_minecraft_action_dispatch(
                        mismatched,
                        expected_request=self.bound_request(),
                    )
        raw = self.dispatch()
        raw["goal"] = "find food"
        with self.assertRaisesRegex(
            MinecraftActionContractError,
            "minecraft_action_dispatch_fields_invalid",
        ):
            validate_minecraft_action_dispatch(
                raw,
                expected_request=self.bound_request(),
            )

    def test_goal_echo_or_readiness_never_counts_as_result(self) -> None:
        for payload in (
            {
                "goal": "find food",
                "outcome_verified": True,
                "outcome_code": "minecraft_goal_confirmed",
            },
            {
                "schema": "minecraft_autonomy.readiness.v1",
                "state": "ready",
                "ready": True,
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(
                    MinecraftActionContractError
                ):
                    validate_minecraft_action_result(
                        payload,
                        expected_request=self.bound_request(),
                    )


if __name__ == "__main__":
    unittest.main()
