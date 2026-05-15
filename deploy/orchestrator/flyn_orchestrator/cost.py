from __future__ import annotations


class BudgetExceeded(Exception):
    pass


class CostTracker:
    def __init__(self, budget_usd: float) -> None:
        self._budget = budget_usd
        self._spent = 0.0

    @property
    def total_usd(self) -> float:
        return self._spent

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self._budget - self._spent)

    def add(self, cost_usd: float) -> None:
        if self._spent + cost_usd > self._budget + 1e-9:
            raise BudgetExceeded(f"budget {self._budget} exceeded by {(self._spent + cost_usd) - self._budget:.4f}")
        self._spent += cost_usd
