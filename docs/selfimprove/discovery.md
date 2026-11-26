# Self-improvement integration discovery

Static source discovery at the pre-L0 baseline. Line numbers are repository-relative. These are existing integration points, not proof that L0–L7 are implemented or enabled.

| # | Capability | Evidence | Existing role / integration consideration |
|---|---|---|---|
| 1 | Interactive request routing | `features/celina/celina_pkg/router.py:2516` | `CelinaRouter.ask` is the interactive routing entry point. |
| 2 | Autonomy route decisions | `features/celina/celina_pkg/router.py:521` | `route_for_autonomy` builds structured decisions and applies offline/UNO policy. |
| 3 | Tier-0 deterministic dispatch | `features/celina/celina_pkg/router.py:1900` | `_tier0_dispatch` handles fast local dispatch; do not bypass policy. |
| 4 | Routing receipts | `features/celina/celina_pkg/router_receipts.py:97` | `TurnTrace` carries per-turn trace information. |
| 5 | Bound, single-use approval | `features/celina/celina_pkg/approval.py:319`, `features/celina/celina_pkg/approval.py:353` | Atomic token verification/consumption and bound approval request. |
| 6 | Evidence integrity | `features/celina/celina_pkg/evidence.py:208`, `features/celina/celina_pkg/evidence.py:263` | Evidence ledger and fail-closed anchor-on-open verification. |
| 7 | Manifest capability boundary | `features/celina/celina_pkg/capabilities.py:137`, `features/celina/celina_pkg/capabilities.py:170` | Execution-boundary and integrity checks; preserve 106-tool parity. |
| 8 | Typed CLI | `features/celina/celina_pkg/cli_bridge.py:57` | Allowlisted typed CLI execution with mutation approval. |
| 9 | Network and sandbox boundaries | `features/celina/celina_pkg/net_policy.py:125`, `features/celina/celina_pkg/landlock_policy.py:426`, `features/celina/celina_pkg/landlock_policy.py:539` | Network policy check and sandbox command/run entry points. |
| 10 | Existing guarded improvement loop | `features/celina/celina_pkg/learning/self_improve.py:62`, `features/celina/celina_pkg/learning/self_improve.py:133`, `features/celina/celina_pkg/learning/self_improve.py:143`, `features/celina/celina_pkg/learning/self_improve.py:155` | Proposal, safety validation, sandbox testing, and pending/rejected persistence. |
