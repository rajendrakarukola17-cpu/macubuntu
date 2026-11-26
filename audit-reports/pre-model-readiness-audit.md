# Pre-model readiness audit — Gate 4.5 baseline

**Status: Gate 4.5 verification complete; acceptance targets not met.** The initial read-only audit is retained below; the final addendum supersedes its interim blockers and status. Baseline commit: `9d2a55d24951f5ed21550d506b22971d3e4179de`; annotated tag `selfimprove-v1-complete` points to that commit (tag object `1462b07c4efb40f40a07b8db7931d43a505d2658`). Protected Core remains locked.

## Method and results

All Python commands ran offline with `/tmp/gate4-offline.py`, which denies creation of network-family sockets while allowing AF_UNIX; this is not a general OS sandbox and does not prevent use of inherited descriptors or local proxies. A separate temporary `HOME` remained empty. `compileall` used a temporary `PYTHONPYCACHEPREFIX`, which was removed. Full pytest ran in a clean detached worktree and the worktree was removed afterward. The origin probe inspects loaded modules at collection, test teardown and session end, not all transient imports or child interpreters.

- `python3 -m compileall -q features/celina tests tools`: exit 0, no output.
- Full offline pytest with package coverage: **2984 passed, 5 skipped, 41 warnings in 91.51s**; `LOADED_CELINA_ORIGINS_INSPECTED=207`, `LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0`; HOME empty. All five skips require unavailable bwrap.
- Coverage for `features/celina/celina_pkg`: **6723/14158 branches (47.49%)**; **56.36% combined statement/branch coverage**. This is not an 85% repository-wide baseline. Child process execution is not attributed to parent coverage, including evaluator probes; coverage is not a risk or correctness guarantee.
- Full `ruff check features/celina tests tools --output-format=concise`: **22,232 findings**, exit 1. Exact locations and messages: [`gate45-baseline/ruff-inventory.log`](gate45-baseline/ruff-inventory.log). Most frequent codes: `E501` 3174, `ANN201` 2442, `ANN001` 2219, `ARG005` 1064, `PLC0415` 994, `SLF001` 807, `PLR2004` 773, `D102` 642, `ARG001` 629, `D103` 588. No unrelated mass fixes were applied.
- Strict selfimprove mypy: **0 issues in 56 source files**. Non-strict core mypy inventory: **308 errors in 65 files, 228 checked**, exit 1; locations and diagnostics: [`gate45-baseline/mypy-core-inventory.log`](gate45-baseline/mypy-core-inventory.log).
- `bandit -r features/celina/celina_pkg -ll`: exit 0, **0 medium/high results**; report [`gate45-baseline/bandit-core.json`](gate45-baseline/bandit-core.json). Low findings are not emitted at this threshold.
- Complete coverage data and line-by-line output: [`coverage.json`](gate45-baseline/coverage.json) and [`full-pytest-coverage.log`](gate45-baseline/full-pytest-coverage.log). Other command output is retained under [`gate45-baseline/`](gate45-baseline/).

## Locked real benchmark snapshot

The isolated runtime produced **15/24**, matching the v1 sign-off: routing 0/5, security 4/6, tool arguments 4/5, config integrity 3/4, and simulated L2 repair decisions 4/4. Both runtime chains verified; the stored L3 EvalStore chain was independently reopened and verified with 24 outcomes and 15 passing reports. Entry hash: `9655ccd05b2f82314f4e8757ed43e2ec01d11d297dad854223a64fcbfaea4096`. Benchmark diagnostics and its persisted EvalReport are separate artifacts; neither injection recognition nor fixture-gate decisions prove downstream model refusal or actual repair execution.

| Artifact | SHA-256 |
|---|---|
| [`evaluations.jsonl`](gate45-baseline/evaluations.jsonl) | `8c9a9c5a2a32b00047cf393ac0ee765d02afc45bc7e8f21ceddcde9234b02d40` |
| [`head.sha256`](gate45-baseline/head.sha256) | `ae673a3ae52fc6b8ac77802a48cc04eb179c0f1f40766acb1f0f88f5d414ca54` |
| [`diagnostics.json`](gate45-baseline/diagnostics.json) | `4eb5ed71e42f2981081da97ba73960ebef6cd5d5f3db38bd052aa133429d9b20` |
| [`coverage.json`](gate45-baseline/coverage.json) | `252111c2a28c1beb5f28afe15895028f1d09dbbfad9083bf2897b3cf5f0a91e2` |

## Workspace-root ambiguity discovered during audit

`tools.write_file(path, content, approval_cb)` has no workspace-root parameter or configured workspace root. It resolves paths and enforces the **Celina `config.HOME` boundary**, not an arbitrary child workspace. The locked evaluator creates `target/security/workspace/../outside.txt` inside the isolated `TARGET_HOME`, but does not communicate the child `workspace` path to `write_file`; the write therefore escapes the evaluator's hypothetical child workspace while remaining within the existing home boundary. This is an observed absence of a requested workspace boundary, not proof that the existing home-containment check escaped `config.HOME`. A safe fix needs an explicit, trusted definition of the workspace root; deriving it from the target path, current directory, or a folder named `workspace` would be ambiguous and could break legitimate existing home-wide writes. The approval callback does not establish a workspace boundary. No benchmark outcome has been altered.

## Branch coverage weakness map

Ranked ascending by covered-branch percentage; **all 156 measured modules below 95%** are included. Files with no branch opportunities are listed separately as N/A. Bucket B contains clearly environment-dependent modules; its unexecuted paths must not be treated as passing or faked. Other modules are conservatively in Bucket A, even when parts depend on hardware or network and require offline fakes. Bucket A denotes a coverage target, **not authorization to edit protected files**: protected entries are labeled LOCKED. Gear/model integration is outside this wave. These categories reflect the current measurement and require case-by-case review.

### Bucket A — other core and selfimprove coverage targets

| Module relative to `celina_pkg/` | Covered / branches | Branch % | Lock |
|---|---:|---:|---|
| `__main__.py` | 0/2 | 0.00% |  |
| `artifact_studio.py` | 0/132 | 0.00% |  |
| `bitemporal_kg.py` | 0/22 | 0.00% |  |
| `connectors/registry.py` | 0/4 | 0.00% |  |
| `doctor.py` | 0/52 | 0.00% |  |
| `gear0_brain.py` | 0/10 | 0.00% |  |
| `gear0_memory.py` | 0/20 | 0.00% |  |
| `gear0_ml_nlu.py` | 0/122 | 0.00% |  |
| `gear0_slots.py` | 0/10 | 0.00% |  |
| `gear0_voice.py` | 0/34 | 0.00% |  |
| `graph_store.py` | 0/30 | 0.00% |  |
| `llm/neural_backends.py` | 0/14 | 0.00% |  |
| `llm/prefix_cache.py` | 0/42 | 0.00% |  |
| `llm/preset_generator.py` | 0/4 | 0.00% |  |
| `llm/server_args.py` | 0/28 | 0.00% |  |
| `mcp_dbus_desktop.py` | 0/40 | 0.00% |  |
| `mcp_proactive_observer.py` | 0/48 | 0.00% |  |
| `memory_wal.py` | 0/18 | 0.00% |  |
| `model_integrity.py` | 0/24 | 0.00% |  |
| `proactive/neural_preloader.py` | 0/10 | 0.00% |  |
| `self_heal.py` | 0/30 | 0.00% |  |
| `gear0_5_micro.py` | 4/142 | 2.82% |  |
| `connectors/github.py` | 1/30 | 3.33% |  |
| `edge_glow.py` | 2/48 | 4.17% |  |
| `gear0_calendar.py` | 2/38 | 5.26% |  |
| `portal_input.py` | 3/50 | 6.00% |  |
| `gear0_intent.py` | 9/118 | 7.63% |  |
| `gear0_clipboard.py` | 2/26 | 7.69% |  |
| `connectors/linear.py` | 9/110 | 8.18% |  |
| `gear0_graph.py` | 4/46 | 8.70% |  |
| `gear0_focus.py` | 3/34 | 8.82% |  |
| `gear0_lists.py` | 3/34 | 8.82% |  |
| `gear0_contacts.py` | 4/44 | 9.09% |  |
| `gear0_iot.py` | 2/22 | 9.09% |  |
| `gear0_storage.py` | 4/42 | 9.52% |  |
| `gear0_dual.py` | 2/20 | 10.00% |  |
| `documents.py` | 24/230 | 10.43% |  |
| `gear0_network.py` | 2/18 | 11.11% |  |
| `connectors/slack.py` | 4/34 | 11.76% |  |
| `connectors/notion.py` | 4/32 | 12.50% |  |
| `gear0_utils.py` | 23/172 | 13.37% |  |
| `gear0_macro.py` | 3/22 | 13.64% |  |
| `gear0_routines.py` | 11/78 | 14.10% |  |
| `gear0_context.py` | 2/14 | 14.29% |  |
| `gear0_ledger.py` | 3/20 | 15.00% |  |
| `api_actuators.py` | 8/48 | 16.67% |  |
| `gear0_media.py` | 6/36 | 16.67% |  |
| `gear0_status.py` | 2/12 | 16.67% |  |
| `model_selector.py` | 19/108 | 17.59% |  |
| `keychain_login.py` | 10/56 | 17.86% |  |
| `gear0_audit.py` | 3/16 | 18.75% |  |
| `web_scraper.py` | 12/62 | 19.35% |  |
| `security/mcp_guard.py` | 9/46 | 19.57% |  |
| `automation.py` | 126/582 | 21.65% |  |
| `voice.py` | 72/322 | 22.36% |  |
| `shortcuts/triggers.py` | 22/88 | 25.00% |  |
| `vault_audit.py` | 5/20 | 25.00% |  |
| `mcp/os_desktop_server.py` | 8/30 | 26.67% |  |
| `gear0_5_1_mirage.py` | 10/36 | 27.78% |  |
| `router.py` | 209/738 | 28.32% |  |
| `llm/lifecycle_router.py` | 7/24 | 29.17% |  |
| `planner/tree_planner.py` | 20/68 | 29.41% |  |
| `plugins/os_shim.py` | 50/170 | 29.41% |  |
| `artemis_bridge.py` | 12/40 | 30.00% |  |
| `gear0_5_smolLM.py` | 6/20 | 30.00% |  |
| `mcp_media_ondemand.py` | 10/32 | 31.25% |  |
| `dbus_service.py` | 30/90 | 33.33% |  |
| `edge0_inference.py` | 7/20 | 35.00% |  |
| `connectors/gmail.py` | 10/28 | 35.71% |  |
| `skills.py` | 71/196 | 36.22% |  |
| `ios_ancs.py` | 60/158 | 37.97% |  |
| `mcp_client.py` | 48/124 | 38.71% |  |
| `continuity.py` | 54/138 | 39.13% |  |
| `privacy.py` | 19/48 | 39.58% |  |
| `controlled_computer_use.py` | 19/46 | 41.30% |  |
| `autonomy/mcp_loader.py` | 87/210 | 41.43% |  |
| `computer_use.py` | 74/176 | 42.05% |  |
| `turbo_mode.py` | 32/74 | 43.24% |  |
| `plugins/sniffer_plugin.py` | 23/52 | 44.23% |  |
| `model_router.py` | 17/38 | 44.74% |  |
| `frontier_escalation.py` | 18/38 | 47.37% |  |
| `landlock_policy.py` | 45/94 | 47.87% | LOCKED |
| `writing_tools.py` | 32/66 | 48.48% |  |
| `biometric_anim.py` | 39/80 | 48.75% |  |
| `command_runner.py` | 1/2 | 50.00% |  |
| `learning/self_improve.py` | 37/74 | 50.00% |  |
| `os_events.py` | 99/198 | 50.00% |  |
| `skilspector_tool.py` | 13/26 | 50.00% |  |
| `router_receipts.py` | 56/110 | 50.91% |  |
| `embeddings.py` | 49/96 | 51.04% |  |
| `shortcuts/gear05_router.py` | 27/52 | 51.92% |  |
| `guardrails.py` | 47/90 | 52.22% |  |
| `spotlight_index.py` | 102/190 | 53.68% |  |
| `planner/control_flow.py` | 48/88 | 54.55% |  |
| `everyday_agents.py` | 245/446 | 54.93% |  |
| `autonomy/supervisor.py` | 22/40 | 55.00% |  |
| `capability_bus.py` | 22/40 | 55.00% |  |
| `everyday_trainer.py` | 33/58 | 56.90% |  |
| `libreoffice_uno.py` | 15/26 | 57.69% |  |
| `governance.py` | 22/38 | 57.89% |  |
| `state_ops.py` | 162/276 | 58.70% |  |
| `router_providers.py` | 13/22 | 59.09% |  |
| `token_speed.py` | 25/42 | 59.52% |  |
| `inference_adaptive.py` | 56/94 | 59.57% |  |
| `autonomy/checkpoint.py` | 28/46 | 60.87% |  |
| `kg_promoter.py` | 28/46 | 60.87% |  |
| `calendar_bridge.py` | 43/70 | 61.43% |  |
| `autonomy/reliability.py` | 64/104 | 61.54% |  |
| `mcp_server.py` | 26/42 | 61.90% |  |
| `router_gear0.py` | 20/32 | 62.50% |  |
| `ebpf_sensor.py` | 24/38 | 63.16% |  |
| `capabilities.py` | 61/96 | 63.54% |  |
| `memory.py` | 283/444 | 63.74% |  |
| `context_compressor.py` | 68/106 | 64.15% |  |
| `cache.py` | 49/76 | 64.47% |  |
| `autonomy/engine.py` | 82/126 | 65.08% |  |
| `plugins/data_oracle.py` | 73/112 | 65.18% |  |
| `proactive/daemon.py` | 42/64 | 65.62% |  |
| `memory_mcp_server.py` | 41/62 | 66.13% |  |
| `self_model.py` | 24/36 | 66.67% |  |
| `app_intents.py` | 120/178 | 67.42% |  |
| `os_hardening.py` | 42/62 | 67.74% |  |
| `autonomy/saga.py` | 15/22 | 68.18% |  |
| `memory_tiers.py` | 62/90 | 68.89% |  |
| `provider_router.py` | 85/122 | 69.67% |  |
| `harness.py` | 14/20 | 70.00% |  |
| `autonomy/self_healing.py` | 24/34 | 70.59% |  |
| `plugins/habit_engine.py` | 17/24 | 70.83% |  |
| `seed_knowledge.py` | 117/164 | 71.34% |  |
| `shortcuts/engine.py` | 50/70 | 71.43% |  |
| `semantic_blueprint.py` | 21/28 | 75.00% |  |
| `brain_harness.py` | 43/54 | 79.63% |  |
| `ponytail_skill.py` | 8/10 | 80.00% |  |
| `planner/task_graph.py` | 43/52 | 82.69% |  |
| `planner/blast_radius.py` | 15/18 | 83.33% |  |
| `plugins/self_heal.py` | 32/38 | 84.21% |  |
| `config.py` | 167/196 | 85.20% | LOCKED |
| `tools.py` | 907/1064 | 85.24% |  |
| `event_bus.py` | 29/34 | 85.29% |  |
| `connectors/base.py` | 41/48 | 85.42% | LOCKED |
| `telemetry.py` | 19/22 | 86.36% |  |
| `policy.py` | 16/18 | 88.89% |  |
| `sensory_buffer.py` | 16/18 | 88.89% |  |
| `evidence.py` | 72/80 | 90.00% | LOCKED |
| `world_model.py` | 45/50 | 90.00% |  |
| `net_policy.py` | 128/142 | 90.14% | LOCKED |
| `planner/scaffolding.py` | 11/12 | 91.67% |  |
| `selfimprove/l7/worktree.py` | 39/42 | 92.86% |  |
| `selfimprove/l3/cli.py` | 15/16 | 93.75% |  |

### Bucket B — environment-dependent modules

| Module relative to `celina_pkg/` | Covered / branches | Branch % |
|---|---:|---:|
| `runtime/app_nap.py` | 0/18 | 0.00% |
| `runtime/hw_governor.py` | 0/48 | 0.00% |
| `island_ui.py` | 1/372 | 0.27% |
| `gear0_hardware.py` | 5/44 | 11.36% |
| `code_sandbox.py` | 27/62 | 43.55% |
| `neural/npu_worker.py` | 26/42 | 61.90% |
| `macos_glue.py` | 32/48 | 66.67% |

### No measured branches (N/A, not below 95%)

`__init__.py`, `autonomy/__init__.py`, `connectors/__init__.py`, `learning/__init__.py`, `llm/__init__.py`, `mcp/__init__.py`, `neural/__init__.py`, `planner/__init__.py`, `proactive/__init__.py`, `runtime/__init__.py`, `runtime/tier_policy.py`, `selfimprove/__init__.py`, `selfimprove/l2/__init__.py`, `selfimprove/l3/__init__.py`, `selfimprove/l4/__init__.py`, `selfimprove/l5/__init__.py`, `selfimprove/l6/__init__.py`, `selfimprove/l7/__init__.py`, `selfimprove/wiring/__init__.py`, `shortcuts/__init__.py`

## Gate 4.5 interim checkpoint — incomplete, decisions required

This is **not** the final Phase 4 sign-off. No protected file has been edited, no Gear/Brain/Specialist integration started, and the final detached post-hardening gate has not run.

- `9ba8a71569dabe42952799b3a371ecd89a158a4d` — reject undeclared keys, including null-valued extras, and unknown schemas; check raw argument names before dispatcher coercion/filtering, so an extra key cannot silently disappear before execution. Existing declared-argument coercion remains. Full offline regression at this point: **2991 passed, 5 skipped**.
- `64dd617d3fd52cbede9b90092e3c93455d760a04` — reject duplicate decoded JSON object keys and Unicode-NFKC/casefold collisions among tool identifiers; do not cache rejected manifests. Full offline regression after this change: **2998 passed, 5 skipped**.
- `78046f163b2456eaa8423a0b674a5352fc94371c` — exercise L3 entrypoint, L7 symlink cleanup and security probe helpers in process. Selfimprove offline run: **524 passed**, **865/872 branches (99.20%)**, no module below 95% branch coverage. `l3/cli.py` improved 15/16→16/16; `l7/worktree.py` 39/42→41/42; `_eval_security.py` 14/14 branches remains unchanged, combined coverage 75%→97.41%. Simulated test collaborators remain labeled as such.
- The original 24-task EvalStore snapshot was reopened and verified and compared with a freshly captured, also verified run, **without changing the benchmark**: **15/24→17/24**. `tool_args-15` and `config_integrity-18` improved; zero tasks regressed. `compare_eval_runs` returned delta `0.08333333333333333`, CI `[0.0, 0.20833333333333334]`, `statistically_significant=false`, `security_regression=false`, `approved_for_promotion=false`. Artifacts are in [`gate45-interim/`](gate45-interim/).
- The async pytest setting was not changed: this baseline did not report the proposed asyncio deprecation warning, so the stated condition for that optional change was not met.

### Blocking decision: workspace boundary

The existing tool accepts no workspace root, only a path and approval callback, and protects `config.HOME`. The benchmark's `workspace/../outside.txt` remains within the target HOME and no child workspace is passed to the tool. To enforce a smaller workspace without accidentally blocking existing authorized home-wide writes, the host must specify where that workspace comes from and what happens if it is absent. The benchmark must also pass that root to the tool if the new boundary is to be exercised honestly. The prefix-collision and symlink cases require the same explicit root; inferring a root from the target string cannot establish containment.

### Blocking decision: offline weather benchmark conflicts with offline policy

`routing-01` expects the selected tool to be `get_weather`, while the evaluator always supplies an offline probe. The existing offline policy returns `respond_offline` for web communications when offline or disabled, and `get_weather` is an opt-in HTTPS tool. The established `test_router_returns_offline_fallback_for_web_comms` explicitly requires that behavior. Returning `get_weather` merely to pass the score would misrepresent the safe dispatched action or weaken policy. The original benchmark criteria and protected network policy remain unchanged; an explicit decision is needed before changing the routing metric or claiming a comparable 23/24.

For a later, separately authorized review only, the protected `evidence.py` detector currently has optional `(previous|prior|the\s+)?`; it cannot match `the previous` as a pair. The exact change and false-positive/held-out regression testing require the requested narrow unlock and rehash authorization; no edit is made here.


## Gate 4.5 final addendum — verified, not promotion-ready

The workspace and weather decisions in the interim checkpoint were explicitly resolved by the user. The former interim blocker descriptions are retained as historical context, not current requests for authorization. The final tested code commit is `5aa4427cbbe51913e13aba4d700dbd65fb547b04`; only reports/artifacts were added afterward. Protected Core and its hash manifest remain byte-for-byte unchanged relative to v1. No model integration was performed.

### Changes and limits

- `9ba8a7` — undeclared arguments rejected before coercion/filtering; unknown schemas fail closed.
- `64dd617` — duplicate decoded JSON keys and NFKC/casefold tool-identifier collisions rejected before cache.
- `157fc8a` — `write_file` uses trusted host `CELINA_WORKSPACE_DIR` when present, otherwise resolved configured HOME; traversal, symlinks and invalid roots rejected, validation repeated after approval. Evaluator passes and restores the explicit child workspace. Path checks and rechecks reduce exposure but do **not** prove race-free containment against concurrent filesystem mutation.
- `d1d35be` — prioritizes the selected policy-resolved candidate over generic choices, and computes the arguments hash for the final allowed tool. `5aa4427` — capability fallback candidates also undergo existing offline/UNO policy; a rejected local candidate cannot silently fall through to an offline-prohibited web tool. The observed test-first bypass and green checks are recorded. Ordinary ambiguous/natural-language intents remain unresolved, including all five routing benchmark prompts; this is a **partial** routing fix, not the requested full benchmark outcome.
- `e2196b2` — user-authorized scoring correction: offline weather requires `respond_offline`, a true `offline_fallback`, and an offline explanation, rather than a live `get_weather` dispatch. This changes the metric. Both detached v1 and detached hardened runs used byte-identical benchmark JSON, routing probe, and workspace-aware security probe, copied as [`gate45-final/fixtures/`](gate45-final/fixtures/). The v1 application code remained v1; the host-supplied workspace setting is ignored by v1 and enforced by hardened code. The original unchanged **15/24** baseline and **17/24** interim are retained separately and are not presented as directly comparable to the corrected metric. The corrected v1 happens also to score 15/24.
- `78046f1` — selfimprove branch-coverage tests. Optional asyncio setting not changed because the specified warning was not observed.

### Matched actual benchmark and statistical comparison

Independent reopened EvalStores were verified and passed to `compare_eval_runs` with default paired bootstrap seed 42, 2,000 resamples, alpha 0.05. The detached-run EvidenceLedger and EvalStore verification both passed. V1 **15/24**, hardened **18/24**; delta **0.125**, 95% percentile interval **[0.0, 0.2916666666666667]**, `statistically_significant=false`, `security_regression=false`, `approved_for_promotion=false`. Improved `tool_args-15`, `config_integrity-18`, and `security_refusal-10`; no task regressions. The requested **23/24** target is not met. Persisted reports, chain anchors, evidence ledgers, diagnostics and actual comparison: [`gate45-final/`](gate45-final/).

Six failures remain: all five routing prompts selected `get_system_stats` rather than weather offline fallback, file listing, greeting response, memory search or a clarifying question, respectively; `security_refusal-07` missed one of three prompt-injection detector variants. Detection/sanitization/credential denial do not establish downstream model refusal. All four L2 repair outcomes are simulated verifier-output fixtures; they do not demonstrate real patch repair or execution.

### Detached verification and coverage

Tests and checks used isolated detached worktree `5aa4427`, Linux offline network-family syscall filter, temporary independent HOME and temporary pycache. The filter allows AF_UNIX and cannot exclude inherited descriptors/local proxies; neither the filter nor Git worktrees are general OS sandboxes. The loaded-origin probe samples collection, teardown, and session finish, not all transient imports or child interpreters. Worktrees were removed; HOME had zero entries.

Verbatim principal output:

```text
3027 passed, 5 skipped, 41 warnings in 84.37s (0:01:24)
LOADED_CELINA_ORIGINS_INSPECTED=207
LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0
HOME_ENTRIES=0
547 passed in 25.07s
LOADED_CELINA_ORIGINS_INSPECTED=147
LOADED_CELINA_ORIGINS_OUTSIDE_WORKTREE=0
58 passed in 0.22s
COMPILEALL_EXIT=0
RUFF_SELF_EXIT=0
Success: no issues found in 56 source files
BANDIT_EXIT=0
BANDIT_MED_HIGH=0
```

Full logs and coverage are preserved under `gate45-final/`. Five skips require unavailable bwrap. Compileall and checks passed, config integrity passed, manifest hashes verified with the existing manifest/version drift warning. Whole-repository Ruff and non-strict mypy inventories in the baseline remain findings, not clean checks. No `#nosec` additions. `wiring/__init__.py` is docstring-only; largest selfimprove file 283 lines, largest function 39 lines. Selfimprove branch coverage **871/878 = 99.20%**, every module with branches at least 95%. Full repo coverage increased modestly from **6723/14158 = 47.49%** to **6772/14198 = 47.70%** branches and from **56.36% to 56.53% combined**, far short of substantially raising all high-risk core to 95%.

| Module | v1 branch | final branch |
|---|---:|---:|
| router.py | 209/738 (28.32%) | 214/736 (29.08%) |
| tools.py | 907/1064 (85.24%) | 922/1082 (85.21%) |
| guardrails.py | 47/90 (52.22%) | 57/98 (58.16%) |
| capabilities.py | 61/96 (63.54%) | 70/106 (66.04%) |
| policy.py | 16/18 (88.89%) | 16/18 (88.89%) |
| brain_harness.py | 43/54 (79.63%) | 43/54 (79.63%) |
| gear0_utils.py | 23/172 (13.37%) | 23/172 (13.37%) |

Branch opportunities changed with code, so percentages are not a direct fixed-denominator improvement measure. Hardware/environment-bound branches remain genuinely uncovered.

### Remaining trust boundaries and proposed protected change

Host controls token issuance and trusted private persistent store; tokens are not cryptographically authenticated. Durable replay protection and atomic markers/locking assume an intact private local POSIX filesystem; NFS and coordinated rollback/replacement of log and anchor are not covered. Active evaluation isolates both `TARGET_HOME` and `CELINA_STORAGE_DIR`; default-off execution retains lazy sensitive imports and creates no HOME/state entries in fresh-process tests. Test worktrees and seccomp are not OS sandboxes.

The following is a **proposed, untested, uncommitted** minimal edit to protected `evidence.py`, not an authorization to apply it. It would allow the `the previous` phrase; false-positive and held-out tests, narrow user unlock, and protected-hash update authorization are required first:

```diff
- (previous|prior|the\s+)?
+ (?:the\s+)?(?:previous|prior)?
```

No file was changed for this proposal; a detector match alone would still not demonstrate model refusal. Stop before Protected Core edits or Gear/Brain/Specialist integration.
