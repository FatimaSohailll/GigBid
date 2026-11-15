# GigBid — R&D report

An engineering design review of GigBid, a multi-agent freelancer copilot (AI-4016, assignment II).
Every number below sits inside a results block that CI checks against `evals/results/`; every
claim points to a results file, a test, a trace or a commit. **All measurements are on DevSim**, a
stand-in for GigSim, because the official simulator, its data and the hidden test set were not
provided (ADR 0005). Sections still waiting for model runs say **pending** and what they wait for.

## 1. Problem and constraints

A freelancer scans job feeds, spends paid Connects on jobs they may not win, writes proposals that
clients ignore, and meets scams asking for free work or payment off the platform. GigBid does the
discovery, assessment and drafting; **the freelancer decides**.

**Never automated:** submitting a proposal, sending a message, spending Connects, booking a meeting.
Each needs explicit approval in GigBid, and on Upwork a second, separate approval (ADR 0008). The
model may draft but never claim work the portfolio does not show.

**Budgets** (declared in `agent/config.yaml`, measured by `evals/runners/budget_report.py`):

<!-- results:start -->
| | Declared p95 (s) | Measured p95 (s) | Declared cost (USD) | Measured mean cost (USD) |
|---|---|---|---|---|
| Per job analysed | 60 | 49.58 | 0.002 | 0.000556 |
| Per proposal | 180 | 118.27 | 0.01 | 0.004372 |

Source: `evals/results/budgets_devsim.json`. Latency includes waiting out free-tier rate limits.
<!-- results:end -->

## 2. Architecture

Two hand-written LangGraph graphs (ADR 0001): a **bid graph**, one durable thread per job, and a
**conversation graph**, one per client conversation; both rendered from the compiled code in
`docs/GRAPHS.md`. Job Analyst, Risk Assessor and Fit Matcher run in parallel; a rule-based decision
chooses skip, watch or bid; Pricing, Writer, claim verification, Critic (at most two revisions) and
Compliance follow; an `interrupt()` waits for the human. Agents exchange eight Pydantic hand-offs.

All marketplace access goes through one **port** with a DevSim/GigSim adapter and a live Upwork
adapter; only the Marketplace MCP server calls it (ADR 0002). The **MCP surface** is three servers
of ours (Portfolio, Marketplace, Workspace) and two third-party ones (GitHub, Upwork), with each
agent limited to an allowlist of tool names and only the submission step holding write tools.

**Stores:** Postgres for checkpoints, the long-term memory store and application data; Redis for the
queue and live progress; Qdrant plus a BM25 index for evidence. **State, trace and memory are kept
apart:** state is the thread's typed checkpoint; the execution trace (every call, argument and
result) goes to LangSmith and the Marketplace audit log; long-term memory holds only knowledge
promoted by explicit rules (a style memory from repeated edits, an outcome memory of what got
replies). Why each agent exists, in detail: `docs/ARCHITECTURE.md`.

## 3. R&D question A: tool granularity

**Hypothesis:** keeping the mid-sized tool set and rewriting its descriptions gives the highest
tool-selection accuracy, and uses fewer tokens per task than a fine-grained set.
**Setup:** 40 tasks with known correct calls, three tool sets (fine, coarse, rewritten), one fixed
model (Qwen3 8B on Ollama, temperature 0, fixed seed), canned tool results. Full method:
`docs/RND_A_TOOL_GRANULARITY.md`.

<!-- results:start -->
| Tool set | Selection accuracy | Argument accuracy | Task success | Tokens per task |
|---|---|---|---|---|
| Fine-grained | 0.875 | 0.9762 | 0.85 | 4109.5 |
| Coarse | 0.825 | 0.6316 | 0.525 | 1197.0 |
| Rewritten (shipped) | 0.925 | 0.9512 | 0.875 | 4924.0 |

Source: `evals/results/tool_granularity.json`.
<!-- results:end -->

**Conclusion:** the accuracy half of the hypothesis held; the token half did not — the rewritten
descriptions are longer, so they cost the most tokens. Coarse tools were cheapest but lost most on
arguments and multi-step tasks. The rewritten set shipped (`mcp_servers/tool_descriptions.py`,
frozen by a test).

## 4. R&D question B: retrieval and claim verification

**Hypothesis (stated before running):** requirement decomposition plus hybrid retrieval raises
evidence Recall@5 by at least 15 points over V0 on jobs with four or more requirements.

<!-- results:start -->
Hypothesis check: V0 Recall@5 0.074, decomposition plus hybrid 0.242, a gain of 16.8 points; supported.
Source: `evals/results/ablation_devsim.json`.
<!-- results:end -->

Variants V0, V1 (decomposition), V2 (hybrid BM25 plus dense with RRF), V3 (cross-encoder
reranking), V4 (outcome-aware ranking) and V8 (corrective loop) are configurable components; the
ablation removes one stage of the best pipeline at a time. Per-requirement retrieval chose the
shipped pipeline, decomposition plus hybrid plus outcome boosts; the reranker lowered recall on this
corpus and was not shipped (ADR 0004). Results by persona and job type: `docs/RND_B_RETRIEVAL.md`.

**Proposals on a paired sample (B3, B4, B5).** V0 and the shipped variant wrote proposals for the
same 5 dev jobs with the same models and simulated-client seed (`docs/RND_B_RETRIEVAL.md`):

<!-- results:start -->
| | Jobs | Sent | Unsupported claims per proposal, draft | Final text | Judge | Reply rate | p95 (s) | USD per proposal |
|---|---|---|---|---|---|---|---|---|
| V0 | 5 | 5 | 0.4 | 0 | 3.8 | 0.4 | 137.26 | 0.004372 |
| Shipped | 5 | 3 | 0 | 0 | 4.3333 | 0.3333 | 145.73 | 0.003906 |

Source: `evals/results/outcomes_comparison_devsim.json`.
<!-- results:end -->

**Decisions** on the first sample of dev jobs:

<!-- results:start -->
| Jobs | Bid-or-skip F1 | Precision | Recall | Scam precision | Scam recall | Price in fair band |
|---|---|---|---|---|---|---|
| 30 | 0.5 | 0.5556 | 0.4545 | 1.0 | 1.0 | 0.2222 |

Source: `evals/results/decision_devsim.json`.
<!-- results:end -->

**Judge calibration:** only 7 proposals are labelled so far, against 30 asked for.

<!-- results:start -->
Cohen's kappa -0.129 on 7 items; within one point on 0.7143 of them.
Source: `evals/results/judge_calibration_devsim.json`.
<!-- results:end -->

The judge rewarded length and keyword coverage more than the human labels did (section 8), so its
score is reported but not used to choose what ships.

**Where better retrieval did not raise the reply rate:** the shipped variant lost both of V0's
replies by not sending. Compliance blocked one draft (`dev-ai-0002`), and the decision skipped one
job (`dev-data-0008`). On the 3 jobs both variants sent, V0 got no reply and the shipped variant one.
Five jobs cannot separate the reply rates: one reply moves the rate by 20 points.

**Shipped choice:** `combo:decompose+hybrid+outcome`, on retrieval quality, zero unsupported claims
in drafts and lower cost per proposal; **not** on reply rate, which the sample cannot decide.

## 5. R&D question C: single agent, prebuilt supervisor, graph

Method, fairness controls and hypothesis: `docs/RND_C_ORCHESTRATION.md`. All three designs are
built and tested (`tests/unit/test_part_c_baselines.py`). **Results pending**: the run needs model
quota after the Part B sample.

## 6. The change request

The announced GigSim policy update was not provided, so it was rehearsed with a new rule (POL-13,
no trading reviews for favours). With rule names in code it took six files and a code change; after
moving every rule into configuration, the same change took two lines in two files and no code
(`docs/CHANGE_REQUEST.md`, commit `05fdc8c`, `tests/unit/test_change_request.py`). Cost changes are
config only: Connects prices are read per job, and the value of a Connect is one setting.

## 7. Signature extension: X3, portfolio builder

GitHub repositories become portfolio case studies, every claim verified against the repository's
own code, unsupported sentences removed, and a case study with too little verified content refused
(`agent/case_builder.py`, `docs/EXTENSION.md`, `tests/unit/test_case_builder.py`). Measured against
each persona's hand-written case studies by `evals/runners/case_builder_eval.py`: **results
pending** model quota.

## 8. Failure analysis

`evals/runners/failure_analysis.py` sorts every failed dev job in the saved rows into the
assignment's taxonomy, recording the field behind each, with no model calls.

<!-- results:start -->
| Primary category | Failed jobs | Pattern |
|---|---|---|
| Wrong bid or skip | 10 | 3 low-budget jobs and 1 partial fit bid; 6 jobs worth a bid skipped or watched |
| Tool error | 5 | a node could not complete when the free-tier daily model limit ran out |
| Mispriced | 3 | 2 priced above the fair band, 1 below; 7 jobs in all had a price outside it |

18 failed jobs out of 48 checked. Source: `evals/results/failures_devsim.json`.
<!-- results:end -->

**Fixes tried.**

- **Wrong bid on low budgets: fixed, and it worked.** The decision never compared the client's
  budget with the freelancer's minimum project price, and the evaluation used a flat 50 USD floor
  instead of each persona's own minimum. The decision now skips a job whose budget tops out below
  the user's minimum (`agent/nodes/decision.py`, `tests/unit/test_decision_budget.py`), and the
  evaluation uses each persona's minimum. The rule is deterministic and runs after every model
  step, so re-scoring the saved decisions gives exactly what a re-run would decide:

  <!-- results:start -->
  Bid-or-skip precision 0.5556 to 0.8333, F1 0.5 to 0.5882; all 4 low-budget jobs now skip, and no
  job worth a bid is skipped. Source: `evals/results/decision_rescored_devsim.json`.
  <!-- results:end -->

  DevSim's labels encode the same rule, so the gain on DevSim is expected by construction; the rule
  is kept because it is the freelancer's own stated minimum.
- **Tool errors: partly fixed.** The campaign now waits out a daily limit instead of crashing, and
  the app's fallback model moved to gpt-oss-120b after Groq withdrew `llama-3.1-8b-instant`.
  Evaluations keep fallbacks off on purpose, so these jobs still fail when the quota runs out.
- **Jobs worth a bid skipped: not fixed.** Two of the six carry a planted instruction; the rows do
  not record whether the fit score or the injection's risk score caused the skip. Next step: log
  both on every decision, then decide whether a flagged injection should raise risk at all once
  the text is delimited.
- **Mispriced: not fixed.** Two good-fit jobs were priced 6 and 8 percent above the fair band and a
  vague job 12 percent below it. Not investigated yet; the price rationale saved with each quote is
  the place to start.

**Judge versus my labels.** Two proposals differ by two points:

- `dev-ai-0015`, a vague job: judge 2, me 4. The claim check removed 2 of 4 claims, leaving three
  sentences; the judge marked it as thin, while I rated it as short but on the client's problem.
- `dev-data-0008`, with a planted instruction: judge 5, me 3. It correctly ignored the instruction,
  but it lists skills and never addresses the deliverable, a report for leadership.

## 9. Safety and platform respect

**Must never:** send, submit, spend Connects or book without approval; claim unsupported work;
follow instructions planted in job posts or messages; move contact or payment off the platform;
scrape or automate a browser.

**Enforced by:** write tools held only by the submission step (a test fails otherwise); `interrupt()`
before every write and a second approval on Upwork; idempotency keys so a retry never sends twice;
claim verification plus the Critic and a final-text check; a deterministic outgoing-text scanner and
the Compliance Guard; untrusted text delimited and scanned for injections; official APIs only
(Upwork and GitHub through their MCP servers); encrypted OAuth tokens; personal data masked in logs
and traces; per-user tenancy.

**What still gets through:** **pending** the attack-rate run (planted injections with and without
defences). Known gaps: the claim checker can pass a claim that is true of the portfolio but
irrelevant to the job; a model review can miss a policy breach phrased unusually; and on Upwork a
message is sent as soon as its single approval is given, because Upwork has no message preview.

## 10. Hidden test results against the declared budgets

The hidden test set was not provided, so there is no hidden test run. Measured on the DevSim dev
sample instead, both declared budgets are met (section 1); the optimisation that helped most is
skipping early, which avoids most of a proposal's tokens on every job the decision node stops
(`evals/results/budgets_devsim.json`, `optimisation_skip_early`).
