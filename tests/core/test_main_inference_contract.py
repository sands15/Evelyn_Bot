from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.main_inference_contract import (  # noqa: E402
    MainInferenceLane,
    MainLlmPayload,
    MainRequestKind,
    compile_main_prompt,
    main_prompt_exact_identity_required,
)


class PromptCompilerTests(unittest.TestCase):
    def test_dynamic_context_does_not_change_prompt_abi(self) -> None:
        first = compile_main_prompt(
            model_name="main-model",
            messages=[
                {
                    "role": "system",
                    "content": "stable\n\n[Runtime State]\nPRIVATE_A",
                },
                {"role": "assistant", "content": "old answer"},
            ],
            final_user_text="first question",
        )
        second = compile_main_prompt(
            model_name="main-model",
            messages=[
                {
                    "role": "system",
                    "content": "stable\n\n[Runtime State]\nPRIVATE_B",
                }
            ],
            final_user_text="different question",
        )

        self.assertEqual(first.abi.prompt_abi_id, second.abi.prompt_abi_id)
        self.assertEqual(first.messages[-1]["content"], "first question")
        self.assertNotIn("PRIVATE", repr(first.abi.public_summary()))

    def test_stable_prefix_or_wire_format_changes_prompt_abi(self) -> None:
        plain = compile_main_prompt(
            model_name="main-model",
            messages=[{"role": "system", "content": "stable-a"}],
            final_user_text="question",
        )
        changed_prefix = compile_main_prompt(
            model_name="main-model",
            messages=[{"role": "system", "content": "stable-b"}],
            final_user_text="question",
        )
        content_array = compile_main_prompt(
            model_name="main-model",
            messages=[{"role": "system", "content": "stable-a"}],
            final_user_text="question",
            content_format="openai",
        )

        self.assertNotEqual(
            plain.abi.prompt_abi_id,
            changed_prefix.abi.prompt_abi_id,
        )
        self.assertNotEqual(
            plain.abi.prompt_abi_id,
            content_array.abi.prompt_abi_id,
        )
        self.assertEqual(
            content_array.messages[-1]["content"],
            [{"type": "text", "text": "question"}],
        )

    def test_exact_binary_identities_are_required_for_exact_flag(self) -> None:
        names = {
            "MAIN_LLM_MODEL_SHA256": "a" * 64,
            "MAIN_LLM_TOKENIZER_SHA256": "b" * 64,
            "MAIN_LLM_CHAT_TEMPLATE_SHA256": "c" * 64,
            "MAIN_LLM_SERVER_SHA256": "d" * 64,
            "MAIN_LLM_RUNTIME_TEMPLATE_SHA256": "e" * 64,
        }
        with patch.dict(os.environ, names, clear=True):
            compiled = compile_main_prompt(
                model_name="main-model",
                messages=[{"role": "system", "content": "stable"}],
                final_user_text="question",
            )

        self.assertIs(compiled.abi.exact_runtime_identity, True)

    def test_embedded_model_identity_binds_tokenizer_and_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identity_path = Path(directory) / "identity"
            server_identity_path = Path(directory) / "server-identity"
            runtime_template_path = Path(directory) / "runtime-template-identity"
            identity_path.write_text("d" * 64, encoding="ascii")
            server_identity_path.write_text("e" * 64, encoding="ascii")
            runtime_template_path.write_text("f" * 64, encoding="ascii")
            environment = {
                "MAIN_LLM_IDENTITY_FILE": str(identity_path),
                "MAIN_LLM_SERVER_IDENTITY_FILE": str(server_identity_path),
                "MAIN_LLM_RUNTIME_TEMPLATE_IDENTITY_FILE": str(
                    runtime_template_path
                ),
                "MAIN_LLM_PROMPT_ASSETS_EMBEDDED": "true",
            }
            with patch.dict(os.environ, environment, clear=True):
                compiled = compile_main_prompt(
                    model_name="main-model",
                    messages=[{"role": "system", "content": "stable"}],
                    final_user_text="question",
                )

        self.assertIs(compiled.abi.exact_runtime_identity, True)
        self.assertEqual(compiled.abi.model_identity_sha256, "d" * 64)

    def test_native_embedded_profile_accepts_three_owner_hashes(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAIN_LLM_MODEL_SHA256": "a" * 64,
                "MAIN_LLM_SERVER_SHA256": "b" * 64,
                "MAIN_LLM_RUNTIME_TEMPLATE_SHA256": "c" * 64,
                "MAIN_LLM_PROMPT_ASSETS_EMBEDDED": "true",
            },
            clear=True,
        ):
            compiled = compile_main_prompt(
                model_name="main-model",
                messages=[{"role": "system", "content": "stable"}],
                final_user_text="question",
            )

        self.assertIs(compiled.abi.exact_runtime_identity, True)
        self.assertEqual(compiled.abi.model_identity_sha256, "a" * 64)

    def test_server_build_and_runtime_arguments_are_part_of_prompt_abi(self) -> None:
        common = {
            "MAIN_LLM_MODEL_SHA256": "a" * 64,
            "MAIN_LLM_TOKENIZER_SHA256": "b" * 64,
            "MAIN_LLM_CHAT_TEMPLATE_SHA256": "c" * 64,
        }

        def compile_for(server_sha: str, runtime_sha: str):
            with patch.dict(
                os.environ,
                {
                    **common,
                    "MAIN_LLM_SERVER_SHA256": server_sha,
                    "MAIN_LLM_RUNTIME_TEMPLATE_SHA256": runtime_sha,
                },
                clear=True,
            ):
                return compile_main_prompt(
                    model_name="main-model",
                    messages=[{"role": "system", "content": "stable"}],
                    final_user_text="question",
                )

        baseline = compile_for("d" * 64, "e" * 64)
        changed_server = compile_for("f" * 64, "e" * 64)
        changed_runtime = compile_for("d" * 64, "f" * 64)

        self.assertEqual(baseline.abi.schema, "evelyn.main-prompt-abi.v2")
        self.assertNotEqual(
            baseline.abi.prompt_abi_id,
            changed_server.abi.prompt_abi_id,
        )
        self.assertNotEqual(
            baseline.abi.prompt_abi_id,
            changed_runtime.abi.prompt_abi_id,
        )

    def test_missing_server_identity_fails_closed(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAIN_LLM_MODEL_SHA256": "a" * 64,
                "MAIN_LLM_TOKENIZER_SHA256": "b" * 64,
                "MAIN_LLM_CHAT_TEMPLATE_SHA256": "c" * 64,
                "MAIN_LLM_RUNTIME_TEMPLATE_SHA256": "e" * 64,
            },
            clear=True,
        ):
            compiled = compile_main_prompt(
                model_name="main-model",
                messages=[{"role": "system", "content": "stable"}],
                final_user_text="question",
            )

        self.assertIs(compiled.abi.exact_runtime_identity, False)

    def test_invalid_exact_identity_requirement_fails_closed(self) -> None:
        with patch.dict(
            os.environ,
            {"MAIN_LLM_REQUIRE_EXACT_PROMPT_ABI": "treu"},
            clear=False,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "main_llm_exact_prompt_abi_config_invalid",
            ):
                main_prompt_exact_identity_required()

    def test_payload_metadata_is_not_sent_in_json(self) -> None:
        compiled = compile_main_prompt(
            model_name="main-model",
            messages=[{"role": "system", "content": "stable"}],
            final_user_text="question",
        )
        payload = MainLlmPayload(
            {"model": "main-model", "messages": compiled.wire_messages()},
            prompt_abi=compiled.abi,
            request_kind=MainRequestKind.REALTIME,
        )

        encoded = json.loads(json.dumps(payload))
        self.assertEqual(set(encoded), {"model", "messages"})
        self.assertNotIn("promptAbi", encoded)


class MainInferenceLaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_realtime_overtakes_queued_background(self) -> None:
        lane = MainInferenceLane()
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()
        order: list[str] = []

        async def holder() -> None:
            async with lane.admit(MainRequestKind.INTERACTIVE):
                holder_entered.set()
                await release_holder.wait()

        async def waiter(kind: MainRequestKind, label: str) -> None:
            async with lane.admit(kind):
                order.append(label)

        active = asyncio.create_task(holder())
        await holder_entered.wait()
        background = asyncio.create_task(
            waiter(MainRequestKind.BACKGROUND, "background")
        )
        await asyncio.sleep(0)
        realtime = asyncio.create_task(
            waiter(MainRequestKind.REALTIME, "realtime")
        )
        await asyncio.sleep(0)
        release_holder.set()
        await asyncio.gather(active, background, realtime)

        self.assertEqual(order, ["realtime", "background"])

    async def test_same_task_reentry_keeps_one_owner(self) -> None:
        lane = MainInferenceLane()

        async with lane.admit(MainRequestKind.REALTIME) as outer:
            async with lane.admit(MainRequestKind.INTERACTIVE) as inner:
                self.assertEqual(inner.request_id, outer.request_id)
                self.assertEqual(inner.kind, MainRequestKind.REALTIME)

    async def test_cancelled_waiter_does_not_poison_lane(self) -> None:
        lane = MainInferenceLane()
        release_holder = asyncio.Event()
        holder_entered = asyncio.Event()

        async def holder() -> None:
            async with lane.admit(MainRequestKind.INTERACTIVE):
                holder_entered.set()
                await release_holder.wait()

        async def blocked_waiter() -> None:
            async with lane.admit(MainRequestKind.BACKGROUND):
                raise AssertionError("cancelled waiter must not enter")

        active = asyncio.create_task(holder())
        await holder_entered.wait()
        blocked = asyncio.create_task(blocked_waiter())
        await asyncio.sleep(0)
        blocked.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await blocked
        release_holder.set()
        await active

        async with asyncio.timeout(1.0):
            async with lane.admit(MainRequestKind.REALTIME):
                pass


if __name__ == "__main__":
    unittest.main()
