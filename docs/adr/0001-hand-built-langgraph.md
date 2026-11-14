# ADR 0001: Hand-built LangGraph graphs, not a prebuilt agent

Status: accepted

## Context

The bid workflow has fixed obligations: analyse before deciding, verify claims before the Critic,
run Compliance before a human, and never send without approval. A ReAct agent or a prebuilt
supervisor decides its own next step, so any of these can be skipped by the model.

## Decision

Two hand-written `StateGraph`s: a bid graph (one thread per job) and a conversation graph (one
thread per conversation). Parallel analysis uses `Send`; loops have explicit limits; approval is an
`interrupt()`.

## Consequences

Every obligation is a node the model cannot skip, and every route is testable without a model.
The cost is more code than a prebuilt agent, and the graph runs every step even on easy jobs. R&D
question C measures this trade against a ReAct agent and a prebuilt supervisor
(`docs/RND_C_ORCHESTRATION.md`).
