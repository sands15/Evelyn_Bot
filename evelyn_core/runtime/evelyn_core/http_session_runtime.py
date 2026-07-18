from __future__ import annotations

from typing import Any, Callable


class HttpSessionProvider:
    def __init__(
        self,
        *,
        client_timeout_factory: Callable[..., Any],
        client_session_factory: Callable[..., Any],
    ) -> None:
        self._client_timeout_factory = client_timeout_factory
        self._client_session_factory = client_session_factory
        self._session: Any = None

    async def __call__(self) -> Any:
        self._session = ensure_http_session_from_runtime(
            self._session,
            client_timeout_factory=self._client_timeout_factory,
            client_session_factory=self._client_session_factory,
        )
        return self._session


def ensure_http_session_from_runtime(
    current_session: Any,
    *,
    client_timeout_factory: Callable[..., Any],
    client_session_factory: Callable[..., Any],
) -> Any:
    if current_session is None or getattr(current_session, "closed", False):
        timeout = client_timeout_factory(total=None, connect=10, sock_connect=10)
        return client_session_factory(timeout=timeout)
    return current_session
