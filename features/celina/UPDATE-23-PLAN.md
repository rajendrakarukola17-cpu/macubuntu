# Celina Update 23 — Connectors — PLAN

## Status: UNBLOCKED — MausBot verified ✅

### MausBot Facts (verified via web search, quoted literally)

- **Repo URL**: https://github.com/milind-soni/OpenMausBot
- **LICENSE**: Apache-2.0 (full text fetched from raw.githubusercontent.com)
- **README**: fetched from raw.githubusercontent.com/milind-soni/OpenMausBot/main/README.md

### MausBot concepts to adapt (Python only, no TypeScript port)

| Concept | MausBot implementation | Celina adaptation |
|---------|----------------------|-------------------|
| Per-bot model selection | Model picker with provider rail | Connector-level model selection outside Gear 0 |
| Composio app connectors | 500+ apps (Gmail, Slack, GitHub, Notion, Linear) via OAuth | Python OAuth connector framework |
| Permission broker | Allow/Deny/deny cards for risky actions | Allow/Deny wrapper for connector actions |
| Calendar routines + webhook triggers | Scheduled triggers + inbound webhooks | **No webhook server** (hard refusal); schedule-based only |
| Local-first storage | ~/.openmausbot | ~/.celina/storage/connectors/ |

### Hard refusals (must respect)

1. **No gear0_webhook.py** — no inbound webhook server in Gear 0
2. **No cloud connector in Gear 0 dispatch chain** — connectors are opt-in, outside Gear 0
3. **No TypeScript code port** — Python implementations only
4. **No exponential backoff/jitter** — violates "max 3 providers, no stacked retries, no sleep on 429"
5. **102-tool contract must not silently change** — explicit version delta if tools added

### Linear tracking
- **RA-6**: Update 23: Connectors (created, Backlog)
- **RA-7**: Update 24: Rust FFI (created, Backlog, conditional on measurement gate)
- Team: RA (key: RA), id: 08e44cb2-f11d-48d5-ad41-328b333bab64
- Authenticated as: RAJENDRA BABU KARUKOLA

## Proposed Update 23 Architecture

```
celina_pkg/connectors/
├── __init__.py
├── base.py           # BaseConnector class, OAuth flow, Allow/Deny gate
├── registry.py       # Connector registry, per-bot model selection
├── linear.py         # Linear connector (uses already-connected Linear app)
├── github.py         # GitHub connector
├── gmail.py          # Gmail connector
├── slack.py          # Slack connector
└── notion.py         # Notion connector
```

### Design constraints

- Connectors live **outside** celina_pkg/gear0_* and celina_pkg/plugins/
- Connectors are **not** in the Gear 0 dispatch chain
- Each connector is opt-in, requires explicit credential setup
- All connector actions go through an Allow/Deny gate (permission broker)
- No background webhook server — polling/schedule-based only
- 102-tool contract preserved: connectors add tools only via explicit version delta decision

## Next steps

1. Create connector base framework (base.py, registry.py)
2. Implement Linear connector (already connected app)
3. Add Allow/Deny permission broker
4. Register connector tools (explicit version delta decision if 102→N)
5. Verify all existing tests still pass