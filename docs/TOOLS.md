# MCP Tool Inventory

Annotations are read-only / destructive / idempotent. All Marketplace tools declare input and
output schemas and record to the append-only audit log.

| Tool | Server | Read or write | Annotations | Auth |
|---|---|---|---|---|
| `search_jobs` | Marketplace | Read | yes / no / yes | Bearer |
| `get_job` | Marketplace | Read | yes / no / yes | Bearer |
| `get_client_history` | Marketplace | Read | yes / no / yes | Bearer |
| `get_connects_balance` | Marketplace | Read | yes / no / yes | Bearer |
| `get_price_stats` | Marketplace | Read | yes / no / yes | Bearer |
| `get_proposal` | Marketplace | Read | yes / no / yes | Bearer |
| `list_conversations` | Marketplace | Read | yes / no / yes | Bearer |
| `list_messages` | Marketplace | Read | yes / no / yes | Bearer |
| `list_events` | Marketplace | Read | yes / no / yes | Bearer |
| `create_proposal_draft` | Marketplace | Write (`idempotency_key`, `dry_run`) | no / no / yes | Bearer |
| `submit_proposal` | Marketplace | Write, spends Connects (`idempotency_key`, `dry_run`) | no / yes / yes | Bearer |
| `send_message` | Marketplace | Write (`idempotency_key`, `dry_run`) | no / yes / yes | Bearer |

Portfolio, Workspace, Upwork and the second third-party server are added in their phases.
