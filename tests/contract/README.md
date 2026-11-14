Contract tests over in-memory MCP clients.

- `test_*_tool_schemas.py` snapshot each server's tools; CI fails on any unintended change.
  After an intended change: `UPDATE_SNAPSHOTS=1 make test`.
- `test_tool_rules.py` checks rules every tool must keep: annotations, output schemas,
  idempotency keys on writes, `dry_run` on Marketplace writes, a read-only Portfolio, and
  the descriptions frozen in `mcp_servers/tool_descriptions.py`.
