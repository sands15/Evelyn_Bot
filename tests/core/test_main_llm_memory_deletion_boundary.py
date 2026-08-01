from __future__ import annotations

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

    def test_all_primary_memory_sinks_use_exposure_guard(self) -> None:
        expected = {
            "main_llm_runtime.py": (
                "memory_exposure_guard",
                "index_dir=deps.memory_index_dir",
            ),
            "voice_route_execution.py": (
                "memory_exposure_request",
                "memory_index_dir=deps.memory_index_dir",
            ),
            "voice_response_runtime.py": (
                "memory_exposure_request",
                "memory_index_dir=deps.memory_index_dir",
            ),
            "fast_control_api.py": ("memory_exposure_request",),
        }
        runtime_dir = RUNTIME_ROOT / "evelyn_core"
        for filename, required_fragments in expected.items():
            with self.subTest(filename=filename):
                source = (runtime_dir / filename).read_text(
                    encoding="utf-8"
                )
                for fragment in required_fragments:
                    self.assertIn(fragment, source)
        self.assertNotIn(
            "async with session.post(deps.llm_server_url",
            (runtime_dir / "voice_route_execution.py").read_text(
                encoding="utf-8"
            ),
        )


if __name__ == "__main__":
    unittest.main()
