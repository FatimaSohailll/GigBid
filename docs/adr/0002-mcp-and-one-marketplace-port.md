# ADR 0002: Every capability is an MCP tool behind one marketplace port

Status: accepted

## Context

GigBid must work against a simulator for grading and against Upwork for real use, and agents
must not touch a marketplace, database or file directly.

## Decision

Three MCP servers of our own (Portfolio, Marketplace, Workspace) and two third-party servers
(GitHub, Upwork). The Marketplace server is the only caller of the marketplace port
(`agent/marketplace_port.py`), whose adapters are DevSim/GigSim over REST and Upwork over MCP.
Each agent gets an allowlist of tool names.

## Consequences

Switching marketplace is a config change; the graph is unchanged. Writes are concentrated in one
server with idempotency keys, dry runs and an audit log, and only the submission step holds them.
The price is an extra network hop per tool call.
