from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


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


__all__ = ["RuntimeCounter", "RuntimeValue"]
