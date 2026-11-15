# ADR 0004: Requirement decomposition, hybrid search and outcome boosts; no reranker

Status: accepted

## Context

Naive dense retrieval on the whole job post misses evidence for individual requirements and
blurs exact tool names.

## Decision

Ship `combo:decompose+hybrid+outcome`: one retrieval per extracted requirement, BM25 and dense
results fused with reciprocal rank fusion, and a boost for evidence from proposals that won or got
replies. The cross-encoder reranker is not shipped.

## Consequences

Chosen on per-requirement retrieval metrics over the full dev set
(`evals/results/rag_comparison_devsim.json`, `docs/RND_B_RETRIEVAL.md`). The reranker lowered
recall on this corpus, so it would have added latency for a worse result.
