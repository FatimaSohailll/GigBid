# ADR 0003: Open-weight models, routed by node

Status: accepted

## Context

The assignment requires an open profile that runs without paying for an API, and most nodes do
small, frequent work.

## Decision

`gpt-oss-20b` on Groq for the frequent small steps, `gpt-oss-120b` for the Writer, Critic and
Client Liaison, a `gpt-oss-120b` fallback for cheap nodes (it has its own daily limit; Groq withdrew `llama-3.1-8b-instant`, the first choice), all set per node in
`agent/config.yaml`. A local profile runs Qwen3 8B through Ollama; a paid profile exists but is
unused.

## Consequences

Cost per proposal stays small (`evals/results/budgets_devsim.json`). The free tier's daily token
limit, not money, became the binding constraint on how much could be evaluated, and evaluations
switch the fallback off so every result comes from the configured model.
