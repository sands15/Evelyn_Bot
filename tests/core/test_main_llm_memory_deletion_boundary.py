from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

try:
    import aiohttp  # noqa: E402,F401
except ModuleNotFoundError:
    main_llm_runtime = None
else:
    from evelyn_core import main_llm_runtime  # noqa: E402
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    MemoryDeletionJournalIntegrityError,
)


class _NeverEnteredResponse:
    async def __aenter__(self):
        raise AssertionError("response must not be entered")

    async def __aexit__(self, exc_type, exc, tb):
        return False


@unittest.skipIf(
    main_llm_runtime is None,
    "aiohttp is required for the production Main LLM sink",
)
class MainLlmMemoryDeletionBoundaryTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_stale_boundary_fails_before_session_post(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.post_calls = 0

            def post(self, *_args, **_kwargs):
                self.post_calls += 1
                return _NeverEnteredResponse()

        session = Session()

        async def get_http_session():
            return session

        with tempfile.TemporaryDirectory() as temp_dir:
            memory_index_dir = Path(temp_dir) / "memory_index"
            deps = SimpleNamespace(
                get_http_session=get_http_session,
                llm_server_url=(
                    "http://llm.invalid/v1/chat/completions"
                ),
                memory_index_dir=memory_index_dir,
            )
            guarded_indexes: list[Path] = []

            @contextmanager
            def reject_stale_boundary(*, index_dir: Path):
                guarded_indexes.append(index_dir)
                raise MemoryDeletionJournalIntegrityError()
                yield  # pragma: no cover

            with patch.object(
                main_llm_runtime,
                "memory_exposure_guard",
                reject_stale_boundary,
            ):
                with self.assertRaises(
                    MemoryDeletionJournalIntegrityError
                ) as raised:
                    await main_llm_runtime.execute_main_llm_once_from_runtime(
                        deps=deps,
                        payload={
                            "messages": [
                                {
                                    "role": "system",
                                    "content": (
                                        "PRIVATE deleted memory canary"
                                    ),
                                }
                            ]
                        },
                        user_text="question",
                    )
            self.assertEqual(guarded_indexes, [memory_index_dir])
        self.assertEqual(
            str(raised.exception),
            MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
        )
        self.assertEqual(session.post_calls, 0)


class EvelynCorePythonMemoryLlmTransportInventoryTests(
    unittest.TestCase
):
    def test_recognized_core_llm_call_site_inventory_is_stable(
        self,
    ) -> None:
        # This inventories canonical runtime syntax. Dynamic boundary tests
        # separately prove request-factory rejection for protected sinks.
        runtime_dir = RUNTIME_ROOT / "evelyn_core"
        isolated_direct_sinks = {
            (
                "control_page_ui_runtime.py",
                "generate_control_page_welcome_text_from_runtime",
            ),
            (
                "llm_warmup_runtime.py",
                "warmup_llm_from_runtime",
            ),
        }

        def name(node: ast.AST) -> str:
            if isinstance(node, ast.Call):
                node = node.func
            if isinstance(node, ast.Name):
                return node.id
            return node.attr if isinstance(node, ast.Attribute) else ""

        def enabled_keyword(node: ast.Call, keyword_name: str) -> bool:
            keyword = next(
                (
                    item
                    for item in node.keywords
                    if item.arg == keyword_name
                ),
                None,
            )
            return keyword is not None and not (
                isinstance(keyword.value, ast.Constant)
                and keyword.value.value in {None, False}
            )

        inventory: set[str] = set()

        for path in runtime_dir.rglob("*.py"):
            relative_path = path.relative_to(runtime_dir).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {
                child: parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                ancestors: list[ast.AST] = []
                current = node
                while current in parents:
                    current = parents[current]
                    ancestors.append(current)
                function = next(
                    (
                        ancestor.name
                        for ancestor in ancestors
                        if isinstance(
                            ancestor,
                            (ast.FunctionDef, ast.AsyncFunctionDef),
                        )
                    ),
                    "",
                )
                if not function:
                    continue
                contexts = {
                    name(item.context_expr)
                    for ancestor in ancestors
                    if isinstance(ancestor, (ast.With, ast.AsyncWith))
                    for item in ancestor.items
                }
                transport = name(node)
                keywords = {keyword.arg for keyword in node.keywords}
                url_arg = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "url"
                    ),
                    None,
                )
                url_index = 1 if transport == "request" else 0
                if url_arg is None and len(node.args) > url_index:
                    url_arg = node.args[url_index]
                if transport in {
                    "memory_exposure_request",
                    "memory_deletion_outbound_request",
                }:
                    boundary = (
                        "explicit_args"
                        if enabled_keyword(node, "expected_position")
                        and enabled_keyword(
                            node,
                            "memory_boundary_required",
                        )
                        else "contextual_args"
                    )
                    if "memory_index_dir" in keywords:
                        boundary += "_di"
                elif transport == "request_sub_llm_json":
                    boundary = (
                        "shared_guard_scope"
                        if "memory_deletion_journal_read_guard" in contexts
                        else "none"
                    )
                elif (
                    transport == "urlopen"
                    and function == "request_sub_llm_json"
                ):
                    boundary = "raw_transport"
                elif transport in {"post", "request"} and (
                    url_arg is not None
                ) and (
                    "llm" in ast.unparse(url_arg).lower()
                    or "llm" in function.lower()
                ):
                    transport = "direct_llm_http"
                    boundary = (
                        "exposure_guard_scope"
                        if "memory_exposure_guard" in contexts
                        else (
                            "isolated"
                            if (relative_path, function)
                            in isolated_direct_sinks
                            else "unclassified"
                        )
                    )
                else:
                    continue
                inventory.add(
                    f"{relative_path}:{function}:{transport}:{boundary}"
                )

        self.assertEqual(
            inventory,
            set(
                """
control_page_ui_runtime.py:generate_control_page_welcome_text_from_runtime:direct_llm_http:isolated
fast_control_api.py:synthesize_tool_evidence_reply:memory_exposure_request:explicit_args
fast_control_api.py:iter_main_llm_deltas:memory_exposure_request:explicit_args
fast_tool_planner.py:default_router_provider:memory_exposure_request:explicit_args_di
json_llm_request_runtime.py:ask_json_llm_from_runtime:memory_deletion_outbound_request:explicit_args_di
llm_warmup_runtime.py:warmup_llm_from_runtime:direct_llm_http:isolated
main_llm_runtime.py:execute_main_llm_once_from_runtime:direct_llm_http:exposure_guard_scope
mindcraft_llm_broker.py:mindcraft_llm_handler:memory_exposure_request:explicit_args_di
memory_vault.py:request_sub_llm_json:urlopen:raw_transport
memory_vault.py:run_semantic_memory_consolidation_once:request_sub_llm_json:shared_guard_scope
memory_vault.py:run_memory_derivation_recomposition_once:request_sub_llm_json:shared_guard_scope
search_answer_runtime.py:answer_from_search_results_from_runtime:memory_exposure_request:contextual_args_di
voice_response_runtime.py:build_first_response_from_runtime:memory_exposure_request:contextual_args_di
voice_response_runtime.py:build_followup_response_from_runtime:memory_exposure_request:explicit_args_di
voice_route_execution.py:execute_main_llm_streaming_turn:memory_exposure_request:explicit_args_di
                """.split()
            ),
        )


if __name__ == "__main__":
    unittest.main()
