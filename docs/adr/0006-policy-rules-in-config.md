# ADR 0006: Policy rules live in configuration

Status: accepted

## Context

The assignment announced a platform policy change and grades how cheaply it is absorbed. The
first rehearsal needed six files and a code change.

## Decision

Each rule is a config entry with an id, an explanation and an optional pattern that blocks or
redacts, validated when the config loads.

## Consequences

A new rule is two lines in two files with no code (`docs/CHANGE_REQUEST.md`). Rules that need
logic beyond a pattern still need code.
