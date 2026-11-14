# The change request

The assignment announced a GigSim "platform policy update" for 2 November 2026: a new rule or cost
affecting bidding, with the efficiency of absorbing it part of the grade. The official GigSim and
its update were not provided to this project, so the change was **rehearsed** on DevSim with a
realistic rule of each kind, measured, and the design changed where the rehearsal showed a cost.

## A new rule: POL-13

> **POL-13.** Do not ask for reviews, ratings or feedback in exchange for a discount, refund or
> extra work. (block)

Marketplaces forbid trading reviews for favours, and a drafting model could plausibly write
"a 10% discount for a 5-star review", so the rule is a fair test.

### First rehearsal: rule names in code (before)

| | |
|---|---|
| Files changed | 6: `agent/config.py`, `agent/config.yaml`, `agent/guardrails.py`, `agent/nodes/compliance.py`, the policy pack, a new test |
| Lines | 30 added, 2 removed (12 of them the test) |
| Time from edit to a verified block | about 5 minutes, including one debugging round |
| What went wrong | `agent/config.py` listed the allowed rule names as a fixed type, so the new name failed config validation until the type was edited too |

The rule ids lived in configuration, but the rules did not: each new rule needed a new regular
expression in the scanner, a new explanation in the Compliance node, and a change to a type. A
policy update meant a code change and a deployment.

### The design change

Every rule became a configuration entry (`compliance.rules` in `agent/config.yaml`): an id, the
explanation shown in the UI, and optionally a pattern that blocks or redacts. The five built-in
rules keep their tuned scanners in code, but their ids and wording moved to configuration. The
config is validated when it loads: a new rule must have a pattern, the pattern must compile, and
the built-in rules cannot be dropped. Commit `05fdc8c`.

### Second rehearsal: rules in configuration (after)

The whole change, applied to a clean copy of the repository:

```diff
# agent/config.yaml, under compliance.rules
+    review_trade: {rule_id: POL-13, explanation: Reviews must not be traded for discounts or extra work., pattern: '\b(?:discount|refund|free|extra)\b.{0,60}\b(?:5[- ]star|review|rating)s?\b'}
# devsim/src/devsim/datagen/policy_pack.md
+| POL-13 | Do not ask for reviews, ratings or feedback in exchange for a discount, refund or extra work. | block |
```

| | Before | After |
|---|---|---|
| Files changed | 6 | **2** (config and the policy pack) |
| Lines | 30 | **2** |
| Code changed | yes | **none** |
| Time to a verified block | about 5 minutes | **28 seconds** |

Verified: "I will give a 10% discount for a 5-star review." is blocked with rule POL-13 and its
explanation, by the deterministic scanner before any model sees the text. The Compliance Guard's
model review reads the policy pack at run time, so it applies the new rule as well, with no prompt
change. `tests/unit/test_change_request.py` keeps this true: a rule added only in configuration
blocks a draft, a redacting rule removes the text without blocking, and a broken rule is refused
when the config loads.

## A new cost

Two cost changes are absorbed without code:

- **A different Connects price per job:** the cost is read from each job (`connects_cost`), so the
  pricing step and the approval preview use the new value at once.
- **A change in what a Connect is worth:** `pricing.connect_value_usd` in `agent/config.yaml`,
  one line. It feeds the "is this bid worth its Connects" check.

A new *kind* of cost, such as a fee on each submission, would need a field in the marketplace port
and one line in the pricing step's check; the approval preview already shows whatever the
marketplace reports.

## What this says about the design

- **Behaviour that changes with policy belongs in data, not code.** Rules, ids, wording, prices and
  thresholds are configuration; only how a check runs is code.
- **One choke point helped.** Every outgoing text passes through the same scanner (proposals,
  screening answers and client replies), so one rule covers all three, and evaluation uses the same
  scanner, so measurements follow the change automatically.
- **Validation at load time matters as much as flexibility.** A typo in a rule fails at start-up
  with a clear message instead of silently letting text through. The rehearsal found exactly that
  gap: a bad pattern used to crash with an unhelpful error; it is now a validation error.
