# ADR 0007: Durable threads and idempotent writes

Status: accepted

## Context

A proposal waiting for approval may outlive an API or worker restart, and a retried submit must
not spend Connects twice.

## Decision

LangGraph's Postgres checkpointer for every thread; approvals are read from the checkpoint; each
write carries an idempotency key derived from the thread, the job and the draft version, which the
Marketplace server stores with the result.

## Consequences

Killing the API while an approval waits, restarting and approving submits exactly once
(`tests/integration/test_crash_recovery.py`). Any step can be replayed from its checkpoint, except
steps that send.
