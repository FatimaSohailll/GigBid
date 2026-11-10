# R&D question A: does tool granularity change agent behaviour?

Numbers come from `evals/results/tool_granularity.json`; task-level rows are in
`tool_granularity_rows.jsonl`. Regenerate with `python -m evals.runners.tool_granularity`.

## Hypothesis, stated before running

Keeping the current mid-sized tool set and rewriting its descriptions gives higher task-level
tool-selection accuracy than a fine-grained or a coarse set, and uses fewer tokens per task than
the fine-grained set.

## Setup

- Model: Qwen3 8B through Ollama, `qwen3:8b`, Q4_K_M, digest `500a1f067a9f`, Ollama 0.40.16, CPU only.
- The same model, settings, system prompt and 40 tasks were used for all three sets: temperature 0,
  seed 7, thinking off, context window 8192, at most 768 output tokens per reply.
- Tasks: 12 read, 8 near-miss, 8 write, 8 multi-step, 4 needing no tool. Tools were not executed;
  each call received a fixed reply. A task counts only if the whole call sequence is right.

## Results

| Tool set | Tools | Task-level selection | Argument accuracy | Task success | Tokens per task |
|---|---|---|---|---|---|
| Fine-grained | 34 | 0.875 | 0.9762 | 0.85 | 4109.5 |
| Coarse | 4 | 0.825 | 0.6316 | 0.525 | 1197.0 |
| Rewritten | 18 | 0.925 | 0.9512 | 0.875 | 4924.0 |

## Conclusion

The selection half of the hypothesis held: 37 of 40 for the rewritten set, 35 for fine-grained and
33 for coarse. With 40 tasks, a difference of two tasks is weak evidence. The token half failed: the
rewritten set used 20% more tokens per task than the fine-grained set.

Coarse tools were cheapest but worst. Hiding arguments inside a generic `params` object cost the
most: argument accuracy fell to 0.6316, and write tasks succeeded only 0.125 of the time.

Fine-grained tools won on near-miss questions (0.875 task success against 0.625). The rewritten set
won on reads and multi-step work, and in near-miss failures the model sometimes answered without
calling a tool.

**Shipped:** the 18 tools with rewritten descriptions, for the best selection and task success with
about half as many tools. They are frozen in `mcp_servers/tool_descriptions.py`.

## Limitations

- One small open model on one CPU machine.
- The fine-grained definitions are leaner than the real server schemas, which favours them on tokens.
- The prompt's sample idempotency key leaked into some calls, including an invented proposal id in
  the failed task `m07`. It affected all three sets equally and the run was not repeated.
- Tool replies are fixed, and the tasks were written by the author of the tool sets.
