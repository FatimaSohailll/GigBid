from agent.config import ServiceBudget, get_config
from evals.runners.budget_report import measure, skip_early


def row(seconds: float, cost: float | None, **values: object) -> dict[str, object]:
    return {"latency_seconds": seconds, "cost_usd": cost, "error": None, **values}


def test_budgets_are_declared_for_jobs_and_proposals() -> None:
    budgets = get_config().budgets
    assert budgets.per_job_analysed.p95_seconds < budgets.per_proposal.p95_seconds
    assert budgets.per_job_analysed.usd < budgets.per_proposal.usd


def test_measured_values_are_compared_with_the_declared_ones() -> None:
    budget = ServiceBudget(p95_seconds=10, usd=0.01)
    result = measure([row(2, 0.001), row(4, 0.003), row(30, 0.002)], budget)
    assert result["measured_p95_seconds"] == 30
    assert result["within_latency"] is False and result["within_cost"] is True


def test_an_unpriced_model_leaves_cost_unknown_rather_than_zero() -> None:
    result = measure([row(1, 0.001), row(1, None)], ServiceBudget(p95_seconds=10, usd=0.01))
    assert result["measured_mean_usd"] is None and result["within_cost"] is False


def test_skipping_early_is_measured_from_the_rows() -> None:
    decisions = [row(1, 0.0, predicted="skip", tokens=4000), row(1, 0.0, predicted="bid", tokens=1)]
    proposals = [row(1, 0.0, submitted=True, tokens=20000)]
    result = skip_early(decisions, proposals)
    assert result["tokens_avoided_per_skip"] == 16000 and result["share_avoided_per_skip"] == 0.8
