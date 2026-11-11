# R&D question B: which retrieval design should ship?

**Status: retrieval is measured; the proposal and reply-rate comparison is incomplete.** All numbers
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

## Decision on what ships

GigBid ships `combo:decompose+hybrid+outcome`, the best measured design per requirement and for
whole jobs, at low latency and with no reranker. This choice rests on retrieval only. It has not yet
been shown to improve proposals or reply rate, because the model-based comparison is incomplete.

## Not yet measured

- Proposal comparison between V0 and the shipped variant, including where better retrieval did not
  improve reply rate. So far only V0 has run, on 4 submitted proposals, too few to report a finding.
- Calibrated judge agreement and the classmate panel.
