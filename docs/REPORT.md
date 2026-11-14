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

**Model-based results (B3, B4, B5)** come from the paired sample run. The B5 table (V0 against the
shipped variant: Recall@5, nDCG@10, unsupported claims, judge score, reply rate, p95 latency and
cost per proposal) is generated into README.md by `python -m evals.results_table`, and is copied
here when the sample is final.

**Decisions** on the first sample of dev jobs:

<!-- results:start -->
| Jobs | Bid-or-skip F1 | Precision | Recall | Scam precision | Scam recall | Price in fair band |
|---|---|---|---|---|---|---|
| 30 | 0.5 | 0.5556 | 0.4545 | 1.0 | 1.0 | 0.2222 |

Source: `evals/results/decision_devsim.json`.
<!-- results:end -->

**Judge calibration:** **pending** more labels; agreement on the first labelled items is in the
generated table above and in `evals/results/judge_calibration_devsim.json`. Two disagreements are
discussed in section 8.

**Where better retrieval did not raise the reply rate:** **pending** the best variant's proposals
in the paired sample. **Shipped choice:** **pending** that comparison; retrieval alone favours
decomposition plus hybrid plus outcome boosts.

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

`evals/runners/failure_analysis.py` sorts failed dev jobs into the assignment's taxonomy (missed
evidence, wrong skip, mispriced, unsupported claim, generic opening, compliance miss, injection,
tool error) from the saved rows, recording the field behind each. **Pending** the finished sample:
the table of at least ten failures, the fix tried for each category and whether it worked.

**Judge versus my labels:** two proposals where the judge and the human label differed by two or
more points are discussed here once labelling is complete.

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
