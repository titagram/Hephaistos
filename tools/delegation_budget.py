"""Process-local, thread-safe budget shared by one delegation tree."""

from __future__ import annotations

from dataclasses import dataclass
import threading


class BudgetExhausted(RuntimeError):
    pass


class DelegationTreeBudget:
    def __init__(self, *, max_children: int, max_iterations: int) -> None:
        if max_children <= 0 or max_iterations <= 0:
            raise ValueError("delegation budget limits must be positive")
        self.max_children = max_children
        self.max_iterations = max_iterations
        self._reserved_children = 0
        self._reserved_iterations = 0
        self._started_children = 0
        self._failures = 0
        self._replans = 0
        self._lock = threading.Lock()

    def reserve_child(self, *, iterations: int) -> "BudgetReservation":
        if iterations <= 0:
            raise ValueError("iterations must be positive")
        with self._lock:
            if self._started_children + self._reserved_children >= self.max_children:
                raise BudgetExhausted("delegation child budget exhausted")
            if self._reserved_iterations + iterations > self.max_iterations:
                raise BudgetExhausted("delegation iteration budget exhausted")
            self._reserved_children += 1
            self._reserved_iterations += iterations
        return BudgetReservation(self, iterations)

    def _finish(self, iterations: int, *, committed: bool) -> None:
        with self._lock:
            self._reserved_children -= 1
            self._reserved_iterations -= iterations
            if committed:
                self._started_children += 1

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1

    def reserve_replan(self) -> None:
        with self._lock:
            self._replans += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "max_children": self.max_children,
                "max_iterations": self.max_iterations,
                "reserved_children": self._reserved_children,
                "reserved_iterations": self._reserved_iterations,
                "started_children": self._started_children,
                "failures": self._failures,
                "replans": self._replans,
            }


@dataclass
class BudgetReservation:
    _budget: DelegationTreeBudget
    _iterations: int
    _finished: bool = False

    def commit(self) -> None:
        if self._finished:
            raise RuntimeError("reservation already finalized")
        self._finished = True
        self._budget._finish(self._iterations, committed=True)

    def rollback(self) -> None:
        if self._finished:
            raise RuntimeError("reservation already finalized")
        self._finished = True
        self._budget._finish(self._iterations, committed=False)
