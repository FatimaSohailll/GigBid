# ADR 0008: Two approvals before anything is sent on Upwork

Status: accepted

## Context

Upwork's tools create a proposal as a preview, and its own tool description asks for explicit
approval before confirming it.

## Decision

The first approval lets GigBid ask Upwork for a preview, which sends nothing; a second,
separate approval ("Send on Upwork") shows Upwork's Connects cost and balance and then confirms.
Messages have no preview on Upwork, so the reply approval is their only gate.

## Consequences

Nothing reaches a real client without two deliberate clicks. The live adapter is otherwise the
same port as DevSim, so the graph needed one extra interrupt node.
