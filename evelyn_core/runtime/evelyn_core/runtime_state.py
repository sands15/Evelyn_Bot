from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar


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


__all__ = ["LazyResourceProvider", "RuntimeCounter", "RuntimeValue"]
