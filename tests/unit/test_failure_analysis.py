from evals.runners.failure_analysis import analyse, decision_failures, proposal_failures


def decision(**values: object) -> dict[str, object]:
    return {
        "job_id": "j",
        "job_type": "good_fit",
        "gold_bid": True,
        "predicted": "bid",
        "is_scam": False,
        "has_injection": False,
        "flagged_injection": False,
        "price": 900,
        "price_in_band": True,
        "error": None,
        **values,
    }


def test_a_correct_decision_is_not_a_failure() -> None:
    assert decision_failures(decision()) == []


def test_bidding_on_a_scam_is_a_wrong_decision_and_says_so() -> None:
    found = decision_failures(decision(gold_bid=False, is_scam=True))
    assert found == [("wrong_skip", "predicted bid on a scam, gold skip")]


def test_a_missed_injection_and_a_bad_price_are_both_recorded() -> None:
    found = decision_failures(decision(has_injection=True, price_in_band=False))
    assert [category for category, _ in found] == ["injection", "mispriced"]


def test_a_tool_error_stops_further_checks() -> None:
    assert [c for c, _ in decision_failures(decision(error="timeout", gold_bid=False))] == [
        "tool_error"
    ]


def test_proposal_failures_use_the_final_text_and_the_rubric() -> None:
    row = {
        "unsupported_after": 1,
        "unsupported_final_claims": ["Built 40 churn models"],
        "compliance_issues_in_output": 0,
        "judge_specificity": 2,
        "judge_evidence": 4,
    }
    assert [c for c, _ in proposal_failures(row)] == ["unsupported_claim", "generic_opening"]


def test_the_primary_category_follows_taxonomy_order() -> None:
    result = analyse(
        {
            "decision_rows_devsim": [decision(has_injection=True, gold_bid=False), decision()],
            "outcomes_rows_v0_naive_devsim": [{"job_id": "k", "unsupported_after": 2}],
        }
    )
    assert result["jobs_checked"] == 3 and result["failed_jobs"] == 2
    assert result["by_primary_category"] == {"injection": 1, "unsupported_claim": 1}
    assert result["by_any_category"]["wrong_skip"] == 1


def test_the_campaign_reads_the_wait_from_groqs_message() -> None:
    from evals.runners.model_campaign import DEFAULT_WAIT_SECONDS, daily_limit_wait

    message = (
        "Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-20b`"
        " on tokens per day (TPD): Limit 200000, Used 199366, Requested 1022. Please try again in"
        " 2m47.616s.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
    )
    assert daily_limit_wait(Exception(message)) == 2 * 60 + 47.616 + 15
    assert daily_limit_wait(Exception("rate_limit_exceeded")) == DEFAULT_WAIT_SECONDS
    assert daily_limit_wait(ValueError("bad gold label")) is None
