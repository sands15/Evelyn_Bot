from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.http_session_runtime import (  # noqa: E402
    HttpSessionProvider,
    ensure_http_session_from_runtime,
)


class HttpSessionRuntimeTests(unittest.TestCase):
    def test_reuses_open_session(self) -> None:
        session = SimpleNamespace(closed=False)

        result = ensure_http_session_from_runtime(
            session,
            client_timeout_factory=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
            client_session_factory=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
        )

        self.assertIs(result, session)

    def test_creates_session_when_missing_or_closed(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def make_timeout(**kwargs: Any) -> dict[str, Any]:
            calls.append(("timeout", kwargs))
            return kwargs

        def make_session(**kwargs: Any) -> SimpleNamespace:
            calls.append(("session", kwargs))
            return SimpleNamespace(closed=False, timeout=kwargs["timeout"])

        result = ensure_http_session_from_runtime(
            SimpleNamespace(closed=True),
            client_timeout_factory=make_timeout,
            client_session_factory=make_session,
        )

        self.assertFalse(result.closed)
        self.assertEqual(result.timeout, {"total": None, "connect": 10, "sock_connect": 10})
        self.assertEqual(calls[0], ("timeout", {"total": None, "connect": 10, "sock_connect": 10}))
        self.assertEqual(calls[1][0], "session")

    def test_provider_reuses_session_across_awaits(self) -> None:
        import asyncio

        calls: list[str] = []
        provider = HttpSessionProvider(
            client_timeout_factory=lambda **kwargs: kwargs,
            client_session_factory=lambda **kwargs: (
                calls.append("session"),
                SimpleNamespace(closed=False, **kwargs),
            )[1],
        )

        async def run() -> None:
            self.assertIs(await provider(), await provider())

        asyncio.run(run())
        self.assertEqual(calls, ["session"])

    def test_provider_closes_and_forgets_session(self) -> None:
        import asyncio

        class Session:
            closed = False

            async def close(self) -> None:
                self.closed = True

        session = Session()
        provider = HttpSessionProvider(
            client_timeout_factory=lambda **kwargs: kwargs,
            client_session_factory=lambda **_kwargs: session,
        )

        async def run() -> None:
            self.assertIs(await provider(), session)
            await provider.close()
            self.assertTrue(session.closed)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
