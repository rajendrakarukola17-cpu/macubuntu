# Tracked-file secret-pattern candidate triage

The requested `git grep` over tracked `*.py` and `*.sh` returned **90 candidate lines**. Each is listed below without secret values. Triage uses the tracked source context, literal roles, and test context; no user credential files were consulted. No candidate was identified as a real credential. This regex is a narrow heuristic and cannot establish that the repository or its history contains no secrets.

| Classification | Count |
|---|---:|
| Configuration lookup | 11 |
| Configuration metadata | 6 |
| Credential lookup | 7 |
| Detection pattern | 3 |
| Non-secret code | 23 |
| Non-secret test code | 11 |
| Placeholder comparison | 7 |
| Runtime approval token | 1 |
| Runtime identifier | 3 |
| Runtime secret handling | 1 |
| Synthetic test fixture | 17 |
| **Total** | **90** |

## Line-by-line review

| Tracked file:line | Classification | Reason |
|---|---|---|
| `apps/macubuntu-center/backend.py:89` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `celina/tools/visual_intelligence.py:53` | Runtime identifier | Builds an identifier dynamically; no static credential assigned. |
| `celina/tools/visual_intelligence.py:71` | Runtime identifier | Builds an identifier dynamically; no static credential assigned. |
| `features/celina/celina_pkg/api_actuators.py:118` | Credential lookup | Selects runtime override or credential lookup; quoted literals identify the lookup. |
| `features/celina/celina_pkg/app_intents.py:334` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/automation.py:1028` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/automation.py:1950` | Runtime approval token | Obtains a bound approval token dynamically; literals specify request metadata. |
| `features/celina/celina_pkg/computer_use.py:355` | Placeholder comparison | Checks a runtime API key against a placeholder. |
| `features/celina/celina_pkg/computer_use.py:456` | Placeholder comparison | Checks a runtime API key against a placeholder. |
| `features/celina/celina_pkg/config.py:438` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:439` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:440` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:441` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:442` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:443` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:444` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:445` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:446` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:447` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/config.py:448` | Configuration lookup | Setting name and empty default; actual value is obtained at runtime. |
| `features/celina/celina_pkg/connectors/github.py:165` | Placeholder comparison | Compares runtime token against a placeholder; no actual token assigned. |
| `features/celina/celina_pkg/connectors/github.py:168` | Credential lookup | Looks up a runtime credential by field name with empty default. |
| `features/celina/celina_pkg/connectors/gmail.py:100` | Configuration metadata | OAuth endpoint or environment-variable name; not a credential value. |
| `features/celina/celina_pkg/connectors/gmail.py:106` | Configuration metadata | OAuth endpoint or environment-variable name; not a credential value. |
| `features/celina/celina_pkg/connectors/gmail.py:118` | Credential lookup | Looks up a runtime credential by field name with empty default. |
| `features/celina/celina_pkg/connectors/gmail.py:119` | Placeholder comparison | Compares runtime token against a placeholder; no actual token assigned. |
| `features/celina/celina_pkg/connectors/gmail.py:122` | Credential lookup | Looks up a runtime credential by field name with empty default. |
| `features/celina/celina_pkg/connectors/notion.py:128` | Configuration metadata | OAuth endpoint or environment-variable name; not a credential value. |
| `features/celina/celina_pkg/connectors/notion.py:131` | Configuration metadata | OAuth endpoint or environment-variable name; not a credential value. |
| `features/celina/celina_pkg/connectors/notion.py:143` | Credential lookup | Looks up a runtime credential by field name with empty default. |
| `features/celina/celina_pkg/connectors/notion.py:144` | Placeholder comparison | Compares runtime token against a placeholder; no actual token assigned. |
| `features/celina/celina_pkg/connectors/notion.py:147` | Placeholder comparison | Compares runtime token against a placeholder; no actual token assigned. |
| `features/celina/celina_pkg/connectors/slack.py:125` | Configuration metadata | OAuth endpoint or environment-variable name; not a credential value. |
| `features/celina/celina_pkg/connectors/slack.py:134` | Configuration metadata | OAuth endpoint or environment-variable name; not a credential value. |
| `features/celina/celina_pkg/connectors/slack.py:146` | Credential lookup | Looks up a runtime credential by field name with empty default. |
| `features/celina/celina_pkg/connectors/slack.py:147` | Placeholder comparison | Compares runtime token against a placeholder; no actual token assigned. |
| `features/celina/celina_pkg/connectors/slack.py:150` | Credential lookup | Looks up a runtime credential by field name with empty default. |
| `features/celina/celina_pkg/ebpf_sensor.py:48` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/event_bus.py:60` | Runtime identifier | Builds an identifier dynamically; no static credential assigned. |
| `features/celina/celina_pkg/everyday_agents.py:373` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/evidence.py:155` | Detection pattern | Regular expression or label for detecting sensitive content, not a credential. |
| `features/celina/celina_pkg/frontier_escalation.py:176` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/frontier_escalation.py:329` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/gear0_utils.py:107` | Detection pattern | Regular expression or label for detecting sensitive content, not a credential. |
| `features/celina/celina_pkg/keychain_login.py:55` | Runtime secret handling | Reads a subprocess result at runtime; quoted literal is a string operation. |
| `features/celina/celina_pkg/memory.py:73` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/memory.py:369` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/memory.py:2047` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/memory.py:2074` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/memory_mcp_server.py:271` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/memory_tiers.py:209` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/memory_tiers.py:303` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/memory_tiers.py:401` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/memory_tiers.py:426` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/privacy.py:20` | Detection pattern | Regular expression or label for detecting sensitive content, not a credential. |
| `features/celina/celina_pkg/router.py:1639` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/router.py:2358` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/runtime/hw_governor.py:89` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/seed_knowledge.py:93` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/semantic_blueprint.py:206` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/spotlight_index.py:354` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/celina_pkg/voice.py:325` | Non-secret code | Tokenization, token count, parser, SQL tokenizer option, pattern, or field metadata; no credential literal assigned. |
| `features/celina/tools/test_critical_a_b.py:219` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/audit/unit/test_connector_integration_stubs.py:47` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/audit/unit/test_connector_integration_stubs.py:407` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/audit/unit/test_connector_integration_stubs.py:425` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/audit/unit/test_net_policy_coverage_2.py:363` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/audit/unit/test_phase3b_remediation.py:116` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/audit/unit/test_phase3b_remediation.py:129` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_agent_harness.py:403` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/test_audit_fixes_and_parity.py:739` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/test_audit_p0_p1_fixes.py:834` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/test_audit_p0_p1_fixes.py:1075` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_context_compressor.py:267` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/test_context_compressor.py:292` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/test_ecosystem_hardening.py:297` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/test_hardware_biometrics.py:168` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/test_operational_trust.py:57` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_pass2b_hardening.py:197` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_pass2b_hardening.py:212` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_pass2b_hardening.py:227` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_pass2b_hardening.py:244` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_pass2b_hardening.py:259` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_pass2b_hardening.py:274` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_pass2b_hardening.py:294` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_pass2b_hardening.py:311` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_persona_and_sensory.py:138` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |
| `tests/test_runtime_tools_voice_scraper.py:392` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_security_net_approval_mcp.py:338` | Synthetic test fixture | Literal in test setup or negative security input; not a production credential. |
| `tests/test_token_speed.py:24` | Non-secret test code | Mock setup, assertion, parameter name, token budget, or test data rather than credential assignment. |

A detected live credential would require stopping, redaction, and user-led revocation/rotation; no such conclusion can be drawn solely from this regex.
