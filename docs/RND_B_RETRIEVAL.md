# R&D question B: which retrieval design should ship?

**Status: retrieval is measured on 600 jobs; proposals and reply rate on a paired sample of 5 jobs,
too few to settle the question.** All numbers
are DevSim, not GigSim. Sources are in `evals/results/`: `rag_comparison_devsim.json`,
`ablation_devsim.json` and the `rag_*_devsim.json` files. Regenerate with
`python -m evals.runners.rag_eval --all` and `python -m evals.runners.ablation`.

## Hypothesis, stated before running

Decomposition plus hybrid retrieval raises whole-job Recall@5 by at least 15 points over V0 on jobs
with four or more requirements.

**Result: supported on DevSim.** Recall@5 on those jobs rose from 0.074 to 0.242, a gain of 16.8 points.

## Setup

- 600 gold jobs, 495 with relevant evidence, and 1,416 labelled requirements.
- Whole job: one query from the full post. Per requirement: one query per requirement, which is how
  the Fit Matcher searches. The deployed variant is chosen per requirement.
- Embeddings `nomic-ai/nomic-embed-text-v1.5-Q`; reranker `Xenova/ms-marco-MiniLM-L-6-v2`. Both run
  locally on CPU, and every variant uses the same embedding model and index.

## Results

| Variant | Per-requirement Recall@5 | Per-requirement nDCG@10 | Whole-job Recall@5 |
|---|---|---|---|
| V0 naive | 0.4132 | 0.4636 | 0.093 |
| V1 decomposition | 0.4132 | 0.4636 | 0.1707 |
| V2 hybrid | 0.4538 | 0.5135 | 0.127 |
| V3 rerank | 0.4491 | 0.4947 | 0.0928 |
| V4 outcome | 0.4359 | 0.4387 | 0.1519 |
| V8 corrective | 0.3825 | 0.4489 | 0.0652 |
| decompose+hybrid | 0.4538 | 0.5135 | 0.2547 |
| hybrid+rerank | 0.4646 | 0.5073 | 0.1205 |
| decompose+hybrid+rerank+outcome | 0.5578 | 0.5575 | 0.2171 |
| **decompose+hybrid+outcome** | **0.5755** | **0.6303** | **0.3074** |

Per-requirement Recall@5 for the shipped variant, by persona: AI/ML engineer 0.5028, data analyst
0.5727, full-stack developer 0.6502. By job type: good fit 0.6032, low budget 0.6022, injection fit
0.5839, vague 0.5196, partial fit 0.4633, scam 0.3726.

## Ablation, removing one stage from the four-stage pipeline

| Removed | Per-requirement Recall@5 | Whole-job Recall@5 |
|---|---|---|
| Nothing | 0.5578 | 0.2171 |
| Decomposition | 0.5578 | 0.1587 |
| Hybrid | 0.5309 | 0.2123 |
| Reranking | 0.5755 | 0.3074 |
| Outcome ranking | 0.4646 | 0.1596 |

Outcome ranking contributed most. Decomposition has no effect per requirement, because one skill is
already one query. The reranker made the pipeline worse, so it is not shipped.

## Why scores are limited

- Past proposals are 70% of the corpus and 54% of V0's top-5 results, but are never gold evidence.
- Recall@5 cannot exceed 0.8241 per requirement, because some requirements have more than five
  correct items.
- By project rather than by item, Recall@5 is 0.7075 for V0 and 0.7693 for the shipped variant.
- About 23% of correct items never name the skill, and single-skill queries carry little information.

A test that excludes proposals from evidence search was proposed and deliberately not run.

## Latency caveat

The reranker cache makes latency for reranked variants measured after V3 not comparable. V3's
per-requirement p95 of 1308.7 ms is the realistic reranking figure. The shipped variant does not
rerank; its per-requirement p95 is 64.9 ms.

## Proposals and reply rate: the paired sample

V0 and the shipped variant wrote proposals for the **same 5 dev jobs** (two good fit, two with a
planted instruction, one vague), with the same models, the same simulated-client seed and the same
claim check. Source: `evals/results/outcomes_comparison_devsim.json`, made by
`python -m evals.runners.model_campaign`; rows in `outcomes_rows_*_devsim.jsonl`.

| | V0 | Shipped variant |
|---|---|---|
| Jobs | 5 | 5 |
| Proposals sent | 5 | 3 (1 blocked by Compliance, 1 skipped) |
| Unsupported claims per proposal, before the claim check (B3) | 0.4 | 0 |
| Unsupported claims per proposal, in the final text (B3) | 0 | 0 |
| Judge score, of 5 | 3.8 | 4.3333 |
| Simulated reply rate, per proposal sent | 0.4 | 0.3333 |
| p95 latency (s) | 137.26 | 145.73 |
| Cost per proposal (USD) | 0.004372 | 0.003906 |
| Tokens per proposal | 19406.6 | 17629 |

Reply outcome by job: V0 only on 2 jobs, the shipped variant only on 1, both on none, neither on 2.

**Where better retrieval did not raise the reply rate.** The shipped variant lost both of V0's
replies by **not sending**, not by writing worse proposals:

- `dev-ai-0002` (good fit): Compliance blocked the draft. The row records one issue caught, not
  which rule; the draft makes an unquantified outcome claim ("a significant increase in
  detection"), the likeliest trigger. V0's draft was sent and got a reply.
- `dev-data-0008` (good fit with a planted instruction): the decision was skip, in this run and in
  the separate decision run, which also uses the shipped retrieval. The rows do not record whether
  the fit score or the injection's risk score caused it. V0 bid, and got a reply.

On the 3 jobs both variants sent, V0 got no reply and the shipped variant got one. The shipped
variant also cut unsupported claims in drafts to zero (V0's vague-job draft had 2 of 4 claims
removed by the checker) and scored higher with the judge on the vague and planted-instruction jobs.

**Reading it.** Five jobs cannot separate the variants on reply rate: one reply either way changes
the rate by 20 points. Better evidence helped the proposals that were sent (no unsupported claims in
drafts, higher judge scores); it could not help the two that were never sent, and neither of those
was stopped by retrieval quality that the rows can show. The next runs should log which Compliance
rule fired and the fit and risk scores behind each skip, so a stop can be traced to its cause.

## Judge calibration

7 V0 proposals are labelled blind (`docs/JUDGE_LABELLING.md`); the assignment asks for 30. Cohen's
kappa between the judge and me is -0.129, and the scores are within one point on 0.7143 of items
(`evals/results/judge_calibration_devsim.json`). The two disagreements of two points:

- `dev-ai-0015`, vague job: judge 2, me 4. The claim check removed 2 of the draft's 4 claims,
  leaving a three-sentence letter. The judge marked it down as thin; I rated it as short but exactly
  on the client's problem, with every remaining claim true.
- `dev-data-0008`, planted instruction: judge 5, me 3. The letter correctly ignored the planted
  instruction, but it reads as a list of skills and never addresses the deliverable, an interactive
  report for leadership. The judge rewarded skill coverage; I marked it down for that.

So the judge rewards length and keyword coverage more than I do. With kappa below zero on 7 items,
the judge score above is reported but not used for the ship decision.

## Decision on what ships

GigBid ships `combo:decompose+hybrid+outcome`. It is the best measured retrieval design per
requirement and for whole jobs, at low latency and with no reranker, and on the paired sample it
removed unsupported claims from drafts and cost less per proposal. It is **not** shipped on reply
rate: the sample is too small to show a difference, and the two replies it lost came from a blocked
draft and a skipped job, not from weaker proposals.

## Not yet measured

- More paired jobs; the campaign's next round (10 jobs per variant) stopped at the free-tier daily
  model limit.
- 23 more judge labels, and the classmate panel (`evals/runners/human_panel.py`).
