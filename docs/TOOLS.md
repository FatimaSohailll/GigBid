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
| `search_evidence` | Portfolio | Read | yes / no / yes | Bearer, or stdio for a local host |
| `get_case_study` | Portfolio | Read | yes / no / yes | Bearer, or stdio for a local host |
| `list_skills` | Portfolio | Read | yes / no / yes | Bearer, or stdio for a local host |
| `free_slots` | Workspace | Read | yes / no / yes | Bearer |
| `create_interview_hold` | Workspace | Write, local calendar only (`idempotency_key`) | no / no / yes | Bearer |
| `create_email_draft` | Workspace | Write, draft only (`idempotency_key`) | no / no / yes | Bearer |

The Workspace server uses a documented local backend, as A3 permits. It never sends anything.

The Portfolio server also exposes the resource templates `portfolio://case/{id}` and
`portfolio://proposal/{id}`, and the prompts `answer_screening_question` and `prove_requirement`.

Upwork and the second third-party server are added in their phase.
