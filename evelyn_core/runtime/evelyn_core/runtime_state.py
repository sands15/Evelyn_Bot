from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass
class RuntimeValue(Generic[T]):
    value: T

    def get(self) -> T:
        return self.value

    def set(self, value: T) -> None:
        self.value = value


@dataclass
class RuntimeCounter:
    value: int = 0

    def get(self) -> int:
        return self.value

    def increment(self) -> None:
        self.value += 1

    def decrement(self) -> None:
        self.value = max(0, self.value - 1)


class LazyResourceProvider(Generic[T]):
    def __init__(self, factory: Callable[[], T], expected_type: type[T]) -> None:
        self._factory = factory
        self._expected_type = expected_type
        self._value: T | None = None

    def __call__(self) -> T:
        if not isinstance(self._value, self._expected_type):
            self._value = self._factory()
        return self._value


class AsyncWorkerStarter:
    def __init__(
        self,
        *,
        before_start: Callable[[], Any],
        worker: Callable[[], Awaitable[Any]],
        create_task: Callable[[Awaitable[Any]], Any],
    ) -> None:
        self._before_start = before_start
        self._worker = worker
        self._create_task = create_task
        self._task: Any = None

    def ensure_started(self) -> None:
        self._before_start()
        if self._task is not None and not self._task.done():
            return
        self._task = self._create_task(self._worker())


__all__ = [
    "AsyncWorkerStarter",
    "LazyResourceProvider",
    "RuntimeCounter",
    "RuntimeValue",
]
