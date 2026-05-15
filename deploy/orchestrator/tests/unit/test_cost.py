import pytest
from flyn_orchestrator.cost import CostTracker, BudgetExceeded


def test_under_budget_no_raise():
    c = CostTracker(budget_usd=1.0)
    c.add(0.2)
    c.add(0.3)
    assert c.total_usd == pytest.approx(0.5)


def test_over_budget_raises():
    c = CostTracker(budget_usd=1.0)
    c.add(0.8)
    with pytest.raises(BudgetExceeded):
        c.add(0.3)


def test_exact_budget_does_not_raise():
    c = CostTracker(budget_usd=1.0)
    c.add(1.0)
    assert c.remaining_usd == pytest.approx(0.0)
