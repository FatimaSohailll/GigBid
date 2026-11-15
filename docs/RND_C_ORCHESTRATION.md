# R&D question C: is the multi-agent design worth it?

**Question (assignment).** On dev jobs, compare (a) one ReAct agent holding all tools, (b) a
prebuilt supervisor (`langgraph-supervisor`) and (c) the hand-built graph. Report bid-or-skip
accuracy, unsupported claims, compliance violations caught, GigSim reply rate, tokens and latency.
If (a) wins on some job types, say so and explain why.

**Hypothesis, stated before running.** The graph sends fewer unsupported claims and fewer policy
problems than either baseline, because claim verification and the Compliance Guard are fixed
steps it cannot skip, while the baselines run them only when the model decides to. The ReAct
agent uses fewer tokens and less time on clear-cut jobs (an obvious scam, an obvious fit), where
the graph still runs every step. Bid-or-skip accuracy is similar across designs, because all three
see the same job post and client history.

## The three designs

| | (a) ReAct agent | (b) Prebuilt supervisor | (c) GigBid graph |
|---|---|---|---|
| Built with | `langchain.agents.create_agent` | `langgraph_supervisor.create_supervisor` | Hand-written `StateGraph` |
| Tools | All 19 GigBid tools in one list | Each worker gets its role's tools, the same allowlists as the graph | Per node, by name |
| Control flow | The model decides every step | A lead model routes between seven role workers | Fixed: parallel analysis, decision, then pricing, writing, verification, Critic, Compliance |
| Models | `gpt-oss-120b` | Each role's configured model; the lead on `gpt-oss-120b` | Each node's configured model |
| Prompts | `agent/prompts/react_baseline.v1.md` | `supervisor.v1.md`, plus each role's own prompt | The node prompts |
| Code | `evals/baselines.py` (`ReactJobRunner`) | `evals/baselines.py` (`SupervisorJobRunner`) | `agent/graphs/bid_graph.py` |

## Keeping the comparison fair

- **Same jobs, same order**, chosen by the same selection as every other evaluation.
- **Same scoring for all three.** Each final proposal goes through the same final-text claim check
  and the same outgoing-text scanner, whether or not the design ran its own checks.
- **Same submission and simulated client**, with a fixed seed, so a reply depends on the proposal
  and not on chance.
- **Same model settings**: temperature 0, the same open models, fallback models switched off, the
  same rate limiting.
- **Writes the agents attempt are counted, not executed.** The baselines hold sending tools only in
  evaluation (`with_baseline_tools`); the harness submits once, after an automatic approval, just as
  it does for the graph. `writes_attempted_by_agent` shows how often a design tried to act on its
  own, which is a safety finding in itself.

## Metrics (`evals/runners/orchestration_compare.py`, `summarise`)

| Assignment metric | Field |
|---|---|
| Bid-or-skip accuracy | `bid_or_skip_accuracy`, and precision, recall and F1 in `bid_or_skip` |
| Unsupported claims | `unsupported_claims_per_proposal`, from the final-text claim check |
| Compliance violations caught | `compliance_caught` (the design's own checks) and `compliance_issues_in_output` (what reached the client) |
| Reply rate | `reply_rate`, from the simulated client |
| Tokens and latency | `tokens_per_job`, `cost_per_job_usd`, `latency_seconds` p50 and p95 |
| Job types where (a) wins | Every metric is also broken down `by_job_type` |

## Running it

```bash
uv run --locked --env-file .env python -m evals.runners.orchestration_compare --limit 10
```

Results go to `evals/results/rqc_*_devsim.json`. Finished jobs are kept, so a run stopped by a
provider limit continues when run again. It cannot run at the same time as another evaluation,
because the portfolio index allows one process at a time.

## Results

**Not completed: the comparison has no result.** On 15 November 2026 the free-tier daily limits of
both Groq models (200,000 tokens each) ran out before the two baselines finished a job, so only the
graph has a row. One job from one design answers nothing about the hypothesis, and none of it is
claimed as a finding.

| Design | Jobs | Bid-or-skip accuracy | Unsupported claims per proposal | Compliance caught / reached client | Reply rate | Tokens per job | Latency (s) |
|---|---|---|---|---|---|---|---|
| (a) ReAct | 0 | | | | | | |
| (b) Supervisor | 0 | | | | | | |
| (c) Graph | 1 (`dev-ai-0002`, good fit) | 1.0 | 0 | 0 / 0 | 1.0 | 21507 | 107.85 |

The graph row ran with `MODEL_PROFILE=open_large` (gpt-oss-120b for every node), because
gpt-oss-20b's daily limit was already spent; source `evals/results/rqc_graph_devsim.json`.

**What the attempt did find.** The first runs against the real provider exposed two faults in the
baselines that the unit tests, which use a fake model, could not:

1. The ReAct agent asked for its final answer in the provider's JSON mode while also offering
   tools, which Groq refuses ("json mode cannot be combined with tool/function calling"). It now
   returns the answer as a tool call (`ToolStrategy`, `evals/baselines.py`).
2. The baselines call their models directly, so a per-minute rate limit ended the whole run,
   while GigBid's own nodes wait it out in the model router. The baseline models now let the
   provider's client retry after the wait Groq asks for (`BASELINE_RETRIES`).

Both are fairness fixes: without them the baselines would have scored as failures for reasons
unrelated to their design.

**To finish it**, when the daily limits have reset (resumable; the graph row is kept):

```bash
MODEL_PROFILE=open_large uv run --locked --env-file .env python -m evals.runners.orchestration_compare --limit 10
```

## Threats to validity

- **Small samples.** A free-tier run covers far fewer than 50 jobs; differences of one or two jobs
  are not evidence.
- **Prompt effort is uneven by nature.** The graph's prompts were tuned over the project; the
  baselines' prompts were written once. That is part of what is being compared, but it favours (c).
- **DevSim, not GigSim.** The simulated client's reply model is DevSim's, not the official one.
