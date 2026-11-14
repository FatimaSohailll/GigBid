# ADR 0005: DevSim as a labelled stand-in for GigSim

Status: accepted

## Context

The official GigSim simulator, its data, the policy pack and the hidden test set were not
provided.

## Decision

Build DevSim (`devsim/`), which follows the published GigSim interface, with generated personas,
jobs, clients and a seeded client simulator. Every result file records `data_source: devsim`, and
the README and report say so.

## Consequences

Everything could be built and measured, and the GigSim adapter is the same code a real GigSim
would use. Numbers are not comparable with classmates' GigSim numbers, and the hidden test run
could not be done.
