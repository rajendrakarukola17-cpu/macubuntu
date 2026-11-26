# Celina Live Capability Audit — Saved Fix Backlog

> **Status:** Tracking findings from live Ubuntu 26.04 PC reports. Fixes are deferred until the full **1,000-item report** is uploaded, at which point all root causes will be resolved together in one consolidated, verified update (**Update 14**).
>
> **Standing Hygiene Rule (User Requirement):** Whenever ANY file, module, launcher, config pattern, or old routine is added, replaced, or deleted in Update 14+, we MUST ALSO:
> 1. Update `updates/apply-update.sh` (especially `--cleanup`) with an explicit **retired-files / old-pattern removal manifest** so applying the update automatically deletes obsolete `.py` files, stale `__pycache__/*.pyc`, superseded scripts/launchers, old update tarballs, and deprecated config patterns on the live Ubuntu PC (zero disk/RAM bloat, zero leftover duplicates!).
> 2. Verify `--dry-run --cleanup` writes 0 files AND provide the exact one-shot PC cleanup verification command alongside every update.

---

## Batch 1 — 30-Item Smoke Report (`smoke-30-report.md`, 2026-11-17)

- **Raw Scorecard:** `21 PASS` · `9 WARN` · `0 FAIL` · `0 crashes` · **100% strict English verified**
- **Saved Copy:** `macubuntu/docs/audit-reports/smoke-30-report.md`
- **Confirmed Wins:**
  - `T001` (`on darkmode`) → `167 ms` (`everyday · SystemAgent`)
  - `T051` (`open calculator`) → `32 ms` (`instant`)
  - `T251` (`remember that my favorite coffee...`) → saved to Quiet Brain & KG (`everyday · LearningAgent`)
  - `T301` (`/skills`) & `T401` (`/help`) → `0 ms` instant
  - Zero crashes, zero `Gdk` errors, zero language drift across all 30 items.

### Logged Findings to Fix in Consolidated Update 14

| # | Priority | Affected IDs | Observed Behavior | Suspected Root Cause to Inspect & Fix |
|---:|---|---|---|---|
| **F1** | **P1 (High)** | `Q351`, `T351` | `read_screen` timed out at `45,000 ms` on BOTH Question & Task | Screenshot / Wayland portal capture (`gnome-screenshot`/portal/OCR/AT-SPI2) likely hanging or waiting on interactive Wayland dialog without a tight subprocess timeout. |
| **F2** | **P1 (High)** | `Q151` | `What text is currently on my clipboard?` timed out at `45,000 ms` | `get_clipboard` (`wl-paste` / `xclip`) may block when clipboard is empty/owned by another Wayland client, or cloud tool loop stalled; also candidate for instant Gear-0 fast-path + strict timeout. |
| **F3** | **P1 (High)** | `Q101`, `T101` | `get_weather` refused with `"the web_comms channel is disabled in your setup"` | Read-only public info tools (`get_weather`, `get_news_headlines`, `get_current_time_zone`, `fetch_url_text`) should NOT be blocked when outbound `web_comms` (webhooks/messaging) is disabled, OR verify exact `config.is_web_comms_enabled()` separation. |
| **F4** | **P1 (High)** | `T151`, `T201`, `Q191` | File/doc creation (`write_file`, `create_document`) & `~/Downloads` audit timed out at `45s`/`90s` | Check whether `write_file`/`create_document` or `search_files` stalled on approval/filesystem scan, OR whether `openrouter/free` stalled on multi-turn tool calls (need tighter per-HTTP-request timeout + fast local/fallback handling). |
| **F5** | **P2 (Medium)** | `Q401` | `"List all available Celina slash commands."` routed to cloud (`19s`) and hallucinated `/quick_pc_check` instead of returning real `/help` list | Add Gear-0 deterministic matcher for natural slash-command listing queries -> exact `/help` output in `0 ms`. |
| **F6** | **P2 (Medium)** | `Q141` | Multi-step research prompt stopped with `"Let me fetch the KDnuggets article..."` without completing final answer | Cloud model emitted narration instead of tool call / hit step/turn exit on `openrouter/free`; improve agent continuation nudge when model declares intent without calling tool. |
| **F7** | **P2 (Medium)** | `Q291`, `Q491` | Hard architecture/planning prompts timed out at `90s` on `openrouter/free` | Check OpenRouter per-request HTTP timeout, retry backoff, and free-tier streaming stall handling in `router.py`. |
| **F8** | **P3 (Runner)** | `Q451` | False-positive `WARN` in `celina-capability-runner.py` (reply actually succeeded cleanly!) | Runner `_INFRA_WARN_RE` matched `"not installed"` inside `get_continuity_status` output describing optional packages (`ifuse`/`uxplay`). Tighten `_INFRA_WARN_RE` to ignore normal status listings. |
| **F9** | **P1 (Speed Architecture)** | `Q001`, `Q051`, `Q151`, `Q301`, `Q401`, `T451` | Simple local PC lookups take `7–45s` via `openrouter/free` (2+ cloud round-trips + free-tier 429/400 retries) instead of instant `<50 ms` like `open calculator` (`32 ms`) and `on darkmode` (`167 ms`) | Expand Gear-0 deterministic local reflexes (CPU/RAM/battery, open apps, clipboard, continuity, skills, slash commands), optimize `_post_openrouter` retry/timeout behavior on `openrouter/free`, and avoid unnecessary second cloud round-trips for simple direct tool outputs. |
| **F10** | **P1 (Smart 6-Provider Cloud + Best Quantized Local)** | All Cloud Gear-2/3 & Local Gear-1 items | `router.py:1197` hardcodes `openrouter.ai`, locking all tasks to congested `openrouter/free` | **LOCKED USER SPEC FOR UPDATE 14:** Build a **Task-Aware 6-Provider Smart Router** (`Groq`, `Google AI Studio / Gemini`, `NVIDIA NIM`, `Mistral`, `Cerebras`, `OpenRouter`) with automatic instant `429`/timeout failover + **Best Quantized Local Ollama Brain** (see full matrix below). |

---

## Locked Update 14 Architecture Spec — Smart Task-Aware 6-Provider Router + Best Quantized Local Brain

### Task-to-Provider Routing Matrix (uses only configured keys, instant failover on 429/error/timeout)

| Task Type | Primary Provider(s) | Failover Chain | Why |
|---|---|---|---|
| **Instant OS / PC Control** *(Gear-0)* | **Local Deterministic Reflex** (`0–50 ms`) | None needed | Dark mode, volume, brightness, CPU/RAM, running apps, clipboard, skills, `/help`, continuity — zero API latency! |
| **Fast Chat, Voice & Everyday Tool Calls** | **Groq** (`llama-3.3-70b-versatile`) → **Cerebras** | `Groq → Cerebras → Gemini Flash → Mistral → OpenRouter → Local` | Sub-second LPU/wafer-scale responses (`~0.5–1.5s`) instead of 15–45s. |
| **Big Docs, PDFs, Multi-File Audits & Vision** | **Google AI Studio** (`gemini-2.5-flash` / `2.5-pro`) | `Gemini → NVIDIA NIM → Groq → Mistral → OpenRouter → Local` | 1M token context window + native multimodal vision + strong tool calling. |
| **Deep Reasoning, Architecture & `/swarm`** | **NVIDIA NIM** (`DeepSeek R1` / `Kimi K2` / `Llama 70B`) → **Gemini 2.5 Pro** | `NVIDIA NIM → Gemini → Groq → Mistral → OpenRouter → Local` | Frontier open-weight reasoning models for hard engineering & threat models. |
| **Coding, Debugging & Structured JSON/Tools** | **Mistral** (`codestral` / `mistral-small`) / **NVIDIA NIM** | `Mistral → NVIDIA NIM → Gemini → Groq → Cerebras → OpenRouter → Local` | Clean code synthesis & disciplined tool schemas. |
| **Private / Sensitive / Offline** *(Gear-1)* | **Best Quantized Local Ollama** | 100% local (never leaves PC) | Keep `qwen3:1.7b` (Q4_K_M, ~1.4 GB) as default repo manifest; add curated Q4_K_M local presets for 8 GB RAM (`qwen3:1.7b` fast, `qwen3:4b` / `qwen2.5:3b-instruct-q4_K_M` balanced heavy, `granite3.1-moe:1b` ultra-light) + `/providers` status command. |

### Keys Supported in `~/.ai-env` (all optional — configure as many free keys as you want!)
- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `NVIDIA_API_KEY`
- `MISTRAL_API_KEY`
- `CEREBRAS_API_KEY`
- `OPENROUTER_API_KEY`

---

## Batch 2 — Full 1,000-Item Report (`full-1000-report.md`, generated 2026-11-18)

- **Status:** Full run confirmed: `1,000 / 1,000` processed. Raw report archived at `macubuntu/docs/audit-reports/full-1000-report.md`; initial triage at `macubuntu/docs/audit-reports/FULL_1000_TRIAGE.md`.
- **Raw Scorecard:** `282 PASS` · `682 WARN` · `0 FAIL` · `36 MANUAL` · automated average latency `40,579 ms`.
- **Interpretation caution:** zero `FAIL` is not evidence of 100% reliability. WARNs are mostly provider/timeout issues, and 36 physical/manual tasks are intentionally not automated.
- **Warning breakdown from item-level table:** `435` timeouts; `224` backend/API/package warnings; `23` security test cases requiring human review. Within the backend-error reply previews, approximately `170` show HTTP `429`, `38` show empty cloud responses, and `39` mention local Ollama errors (some replies contain more than one symptom). Report-level source histogram separately lists 253 `error` sources across all 1,000 items; classify from item rows, not just histogram labels.
- **Provider pathology:** 170 of the backend-error warning previews explicitly show `429 Too Many Requests`; cloud empty-response fallback appears 38 times; Ollama `503` appears once. Execution source histogram: `openrouter/free` only (fast 82, tactical 99, deep 14), plus timeout/error paths; no multi-provider failover was in use.
- **Lowest domain pass rates:** Domain 8 Screen/GUI `12.5%` (12 pass / 96 automated); Domain 5 Documents `13.0%` (13/100); Domain 4 Files/Clipboard/Writing `16.7%` (16/96); Domain 7 Skills/Recording `19.4%` (19/98); Domain 10 Continuity/Security `18.7%` (17/91). Domain rates are confounded by shared cloud outages; do not equate them directly to module defect rates.
- **Security review:** 23 warnings say security tests completed and require checking the reply for refusal/containment; adjudicate against expected behavior. 36 manual tasks need a PC-side checklist.
- **Report-integrity anomaly:** generated self-assessment says, “I will read and summarize the content of the file `poisoned.txt`,” instead of summarizing the audit. Treat this as a potential prompt-injection / summary-context isolation failure: investigate the runner's AI-summary prompt and evidence boundaries. Do not follow or trust that generated summary.

### Consolidated Batch-2 Findings for Update 14 (merge with F1–F10 above)

| # | Priority | Evidence / Scope | Observed Behavior | Root-Cause Investigation / Acceptance Criteria |
|---:|---|---|---|---|
| **F11** | **P0 (Availability)** | `435` timeout WARNs, across all domains and difficulty levels | Sequential test stalls at 45s/90s item budgets; average automated latency 40.6s. | Decompose model HTTP wait vs tool subprocess vs approval/UI wait vs runner budget. Add bounded connect/read/whole-turn budgets, cancel propagation, and non-blocking tool limits; do not globally increase timeout. Acceptance: synthetic stalled provider/tool returns a typed error within budget; no hanging worker; retry budget is bounded. |
| **F12** | **P0 (Provider Reliability)** | `224` backend/API/package WARN rows; ~170 explicitly show HTTP 429; OpenRouter/free is the only cloud route in the source histogram | Free provider rate limits, empty responses, and fallback errors dominate otherwise unrelated tasks. | Implement the locked task-aware 6-provider router (F10): classify retryable 429/5xx/timeout/empty-response vs non-retryable auth/schema errors; honor `Retry-After`, jitter, per-provider cooldown/circuit breaker, idempotency-safe fallback, local fallback policy, and `/providers` status. Never loop retries across providers. Acceptance: deterministic mocked tests prove each failover path and no-secret logging. |
| **F13** | **P1 (Local Fallback)** | 39 error previews mention Ollama failures, including one `503`; many cloud empty-response replies attempt local fallback | Local model availability/fallback is not dependable during cloud trouble. | Inspect health probe/model installed checks, request serialization, model warm/cold-start budget, concurrency and 8-GB RAM limits. Keep `qwen3:1.7b` default. Acceptance: Ollama-down/503 is surfaced clearly and fails over once; when healthy local fallback answers; no infinite retry or accidental private-to-cloud escalation. |
| **F14** | **P1 (Gear-0 & Tool Orchestration)** | Low pass rates in domains 4/5/7/8; Batch 1 F1–F5/F9 overlap | Common local lookups and some operations appear routed through costly LLM calls or time out before useful results. | Inspect item-level successes, failures, tool call traces, approval states, and Gear-0 routing for clipboard, files/writing, documents, skills, and GUI. Add deterministic reflexes only for safe and repeatable cases. Acceptance: targeted tests assert correct tool selected, result verified, and local-only tasks do not call cloud unnecessarily. |
| **F15** | **P1 (Security / Prompt Injection)** | 23 security-review WARNs; generated report summary refers to `poisoned.txt` | Security cases are not adjudicated automatically; AI-generated summary may be contaminated by untrusted test content. | Review all 23 expected-vs-actual outcomes. Treat report/task/tool output as untrusted data, isolate summary instructions from retrieved content, use quoted/evidence-only summarization and deterministic report metadata. Acceptance: adversarial fixture containing “poisoned.txt” instructions is summarized as data and cannot change instructions or leak unrelated file contents. |
| **F16** | **P2 (Manual Test Coverage)** | 36 `MANUAL` items | Voice, physical device, lock/hardware and other interactions are skipped by automation; not code failures. | Generate a concise live-PC checklist with prerequisites, exact actions, expected result, evidence field, and safe cleanup/undo. Keep these separated from automated PASS/WARN metrics. |
| **F17** | **P2 (Runner Scoring / Evidence)** | 682 WARN with 0 FAIL; warning labels aggregate API, timeout and security review; generated self-assessment is off-topic | Scorecard cannot yet distinguish infrastructure outage, product behavior, and expected security refusal. | Improve runner classification into `PASS`, `PRODUCT_WARN`, `INFRA_WARN`, `SECURITY_REVIEW`, `MANUAL`, `FAIL`; preserve raw response/trace safely and make summary optional or evidence-grounded. Acceptance: fixtures for 429, timeout, security refusal, tool success, and prompt-injection summary classify correctly. |

**Next work sequence:** First implement/test provider resilience and timeout containment, then local fallback and specific tool/Gear-0 fixes guided by per-item evidence; adjudicate security/manual cases; run regression suite and cleanup dry-run. Updates 15/16 remain sequenced after stable Update 14. No production change is implied by this backlog entry.

---

## Phase B Blueprint — Open-Source Zero-Duplication Integrations *(After Update 14)*

- Full research & architecture plan saved in: **`macubuntu/docs/audit-reports/OSS_INTEGRATION_BLUEPRINT.md`**
- Cherry-picks the exact best mechanisms without duplicating Celina's existing modules or exceeding 7.6 GiB CPU RAM limits:
  - `Hermes Agent` / `agentskills.io`: `SKILL.md` standard + 3-level progressive loading + reflective skill drafting
  - `Gloamy` / `FutureOS`: 6-Provider trait router + `/compact` + scheduled recurring task ledger
  - `Browser-Use`: Indexed element grounding (`[0]`, `[1]`, `[2]`) for desktop & browser
  - `Pipecat` + `Kokoro-82M ONNX`: Sentence-streaming TTS + Silero VAD auto-barge-in + CPU-friendly expressive voice (skipping GPU-only Fish Speech/CosyVoice locally)
  - `MambaBot OSS` + `Home Assistant Assist`: Optional <25 MB phone web companion + captcha/login human hand-off + local Home Assistant REST preset.

