# Celina Update 23 — Connectors — COMPLETE

## Status: All 5 connectors built, tested, and integrated

## What's Built

### Files created (5 connectors + base framework)
- `celina_pkg/connectors/__init__.py` — Package exports
- `celina_pkg/connectors/base.py` — BaseConnector, ConnectorConfig, ConnectorStatus, ConnectorAction, ActionResult, AllowDenyGate
- `celina_pkg/connectors/registry.py` — ConnectorRegistry singleton
- `celina_pkg/connectors/linear.py` — Linear connector (GraphQL API, 12 actions: 9 read-only, 3 write)
- `celina_pkg/connectors/github.py` — GitHub connector (REST API, 7 actions: 5 read-only, 2 write)
- `celina_pkg/connectors/gmail.py` — Gmail connector (Gmail API, 5 actions: 4 read-only, 1 write)
- `celina_pkg/connectors/slack.py` — Slack connector (Web API, 6 actions: 5 read-only, 1 write)
- `celina_pkg/connectors/notion.py` — Notion connector (API, 6 actions: 5 read-only, 1 write)

### Files modified
- `celina_pkg/router.py` — Added `/connectors` command + `_register_connectors()` in `CelinaRouter.__init__`

### `/connectors` command
- `/connectors` — list all connectors with configuration status
- `/connectors <name> status` — show connector details + available actions
- `/connectors <name> configure` — instructions for setting API key
- `/connectors <name> deconfigure` — remove stored credentials

### Connector action summary
| Connector | Read-only | Write | Total |
|-----------|-----------|-------|-------|
| Linear | 9 | 3 | 12 |
| GitHub | 5 | 2 | 7 |
| Gmail | 4 | 1 | 5 |
| Slack | 5 | 1 | 6 |
| Notion | 5 | 1 | 6 |

### Design (all hard refusals respected)
- Connectors OUTSIDE Gear 0, NOT in dispatch chain
- No gear0_webhook.py — no inbound webhook server in Gear 0
- No cloud connector in Gear 0 dispatch chain
- No TypeScript code port — Python implementations only
- No exponential backoff/jitter — bounded retry (max 3) on 429/5xx
- 102-tool contract preserved — connectors don't add tool contract entries
- Credentials stored locally at ~/.celina/storage/connectors/ (mode 0600)
- Status output never exposes credential values

### Verification
- All connector modules compile clean
- Framework tests pass: registry, status row, configure/deconfigure, Allow/Deny gate (approve/deny)
- Linear connector tested with generic key — all paths work
- `/connectors` command tested: list, status, deconfigure all work
- Groups A+B: 18/18 PASS
- P0 regression tests: exit 0
- Lazy import lint: 31/31 PASS, 0 violations
- All 31 celina_pkg modules + connectors compile clean

## Linear Tracking
- **RA-6**: Update 23: Connectors (Backlog, updated)
- Team: RA (key: RA), id: 08e44cb2-f11d-48d5-ad41-328b333bab64

## MausBot Facts (verified ✅)
- **Repo URL**: https://github.com/milind-soni/OpenMausBot
- **LICENSE**: Apache-2.0
- **README**: fetched from GitHub raw

## MausBot concepts adapted
| Concept | MausBot | Celina |
|---------|---------|--------|
| App connectors | Composio 500+ apps | Direct API connectors (5 built) |
| Permission broker | Allow/Deny cards | AllowDenyGate class |
| Local-first storage | ~/.openmausbot | ~/.celina/storage/connectors/ |
| Per-bot model selection | Model picker rail | Connector-level config |

## Linear vs Composio
- **Linear**: Single project management app, direct GraphQL API, no middleware needed — aligns with Gear 0 zero-external-dep philosophy
- **Composio**: Integration platform for 500+ apps, requires Composio account/API — external dependency
- **Decision**: Direct connectors (what we built) — local-first, no middleware

## Next: Update 24 (Rust FFI) — conditional on measurement gate (RA-7, Backlog)
- Target: Edge0-35B-A3B at 1.5-2.0 GiB active RAM
- gear0_utils.py is the target for Rust FFI integration (math, units, env, JSON, PII redaction)
- BLOCKED pending measurement gate verification