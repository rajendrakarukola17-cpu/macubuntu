# Pre-L0 self-improvement context bundle

Read-only extraction of tracked source at commit `e5924ceea56e8d759cb0c08003823188408c6021`. Runtime/user credential files were not read. Function signatures are static AST extractions, not runtime imports. File line numbers refer to this baseline. The CODEOWNERS block below includes the Gate 2 updates that were subsequently committed with the guardrails.

## Protected Core SHA-256

| File | SHA-256 |
|---|---|
| `features/celina/celina_pkg/approval.py` | `e029e14e7f7af3cb2fde6f23876400f0306c82ee42b686843420554e9a7224f2` |
| `features/celina/celina_pkg/evidence.py` | `b1fe25dd173f0789db358e012a19785739554bf1fde8ba4ec922a1a7a8baefab` |
| `features/celina/celina_pkg/net_policy.py` | `f22025c0993d399a543c5fc87440efa83c4cfd5237c6c300a9361e47ad50ab2f` |
| `features/celina/celina_pkg/landlock_policy.py` | `1ae8540a951dbff682b4352c757abc2e0ec091bd5fa2e80d787a87f0fff07b22` |
| `features/celina/celina_pkg/config.py` | `bd5e327dc12a113292054919d4627bdb06d3592b1dd4558ac66e2ca777996727` |
| `features/celina/celina_pkg/connectors/base.py` | `ab669c1f41009e4d68b705ef63901d92cb141b32ccb2570e6654ad8f917cf19f` |

## Protected Core public classes and function signatures

### `approval.py`

```python
L29 class RiskLevel(str, Enum)
L216 def compute_arguments_hash(tool: str, arguments: dict[str, Any] | None) -> str
L226 def classify_risk(tool: str, arguments: dict[str, Any] | None=None, active_window: str='') -> RiskLevel
L253 def requires_fresh_confirmation(tool: str, arguments: dict[str, Any] | None=None, active_window: str='') -> bool
L276 class ApprovalToken()
  L287 def to_record(self) -> dict[str, Any]
  L299 def verify_for(self, tool: str, arguments: dict[str, Any] | None, now: float | None=None) -> tuple[bool, str]
  L316 def consume(self) -> None
  L319 def verify_and_consume_atomic(self, tool: str, arguments: dict[str, Any] | None, now: float | None=None) -> tuple[bool, str]
L330 def infer_target(tool: str, arguments: dict[str, Any] | None) -> str
L353 def request_bound_approval(tool: str, arguments: dict[str, Any] | None, approval_cb: ApprovalCallback | None, *, title: str | None=None, target: str | None=None, active_window: str='', extra_warning: str='', ttl_seconds: float=60.0) -> tuple[ApprovalToken | None, str | None]
L414 def verify_and_consume(token: ApprovalToken | None, tool: str, arguments: dict[str, Any] | None, *, now: float | None=None) -> tuple[bool, str]
L426 class ApprovalEngine()
  L436 def __init__(self, approval_cb: Callable[[str, str], bool] | None=None)
  L440 def request_sync(self, *, tool: str, arguments_hash: str, context: str, timeout: int=300) -> bool
```

### `evidence.py`

```python
L164 def detect_prompt_injection(text: str) -> list[str]
L183 class EvidenceIntegrityError(RuntimeError)
L208 class EvidenceLedger()
  L211 def __init__(self, ledger_path: Optional[Path]=None, *, durable_store: Any=None, anchor_path: Optional[Path]=None, verify_anchor_on_open: bool=True)
  L287 def reset_anchor(self, *, confirm: bool=False) -> None
  L311 def record_execution(self, *, tool_name: str, arguments: dict[str, Any], result_text: str, why_reasoning: str='', undo_action_id: Optional[str]=None, tier: str='tier2', root_goal_id: str='', task_id: str='', parent_task_id: str='', trace_id: str='', span_id: Optional[str]=None, actor_identity: str='', policy_decision: str='allow', event_type: str='tool_execution', durable_store: Any=None) -> dict[str, Any]
  L413 def record_prediction(self, *, tool_name: str, arguments: dict[str, Any], predicted_success: float, root_goal_id: str='', task_id: str='', trace_id: str='', actor_identity: str='', sim_digest: str='') -> dict[str, Any]
  L445 def record_approval(self, *, tool_name: str, arguments: dict[str, Any], sim_digest: str, decision: str='allow', root_goal_id: str='', task_id: str='', trace_id: str='', actor_identity: str='') -> dict[str, Any]
  L472 def record_verification(self, *, tool_name: str, arguments: dict[str, Any], verified_success: bool, verification_method: str='independent_read_after_write', root_goal_id: str='', task_id: str='', trace_id: str='', actor_identity: str='') -> dict[str, Any]
  L504 def record_calibration(self, *, tool_name: str, context_key: str, signed_bias: float, wilson_lower: float, is_unproven: bool, root_goal_id: str='', task_id: str='', trace_id: str='', actor_identity: str='') -> dict[str, Any]
  L541 def record_security_event(self, *, event_type: str, tool_name: str='security', detail: str='', summary: str='', root_goal_id: str='', task_id: str='', parent_task_id: str='', trace_id: str='', span_id: str='', actor_identity: str='', policy_decision: str='', durable_store: Any=None) -> dict[str, Any]
  L576 def list_entries(self, limit: int=25) -> list[dict[str, Any]]
  L588 def verify_chain(self) -> tuple[bool, str, int]
  L637 def export_evidence(self, output_path: Optional[Path | str]=None) -> dict[str, Any]
L672 def set_current_why_context(summary: str) -> None
L676 def get_current_why_context() -> str
L680 def get_evidence_ledger() -> EvidenceLedger
```

### `net_policy.py`

```python
L67 class URLPolicyError(ValueError)
L71 class OfflineError(RuntimeError)
L86 def is_online(host: str='1.1.1.1', port: int=443, timeout: float=2.0) -> bool
L95 def is_web_comms_tool(tool_name: str) -> bool
L104 def validate_web_comms_host(url: str, allowed_hosts: Iterable[str] | None=None) -> None
L125 def check_web_comms_allowed(tool_name: str, url: str | None=None, *, online_probe: Callable[[], bool] | None=None) -> None
L155 def guarded_call(tool_name: str, *args: Any, online_probe: Callable[[], bool] | None=None, **kwargs: Any) -> Any
L191 class ValidatedURL()
L200 class SafeHTTPResponse()
  L208 def text(self) -> str
  L211 def json(self) -> Any
L215 def is_forbidden_public_ip(ip_str: str) -> tuple[bool, str]
L253 def default_dns_resolver(host: str, port: int) -> list[str]
L272 def validate_public_url(raw_url: str, *, resolver: ResolverFn | None=None) -> ValidatedURL
L331 def load_iot_allowlist(path: Path) -> dict[str, Any]
L353 def validate_iot_url(raw_url: str, method: str, allowlist: dict[str, Any], *, resolver: ResolverFn | None=None) -> ValidatedURL
L434 def enforce_sensitive_egress(url: str, *, payload_text: str='', classification: str='PUBLIC', allowed_destinations: Iterable[str] | None=None) -> None
L501 def safe_request(method: str, url: str, *, policy: str='public', iot_allowlist: dict[str, Any] | None=None, allow_redirects: bool=False, max_redirects: int=0, max_bytes: int=2 * 1024 * 1024, timeout: tuple[float, float] | float=(5.0, 10.0), allowed_content_types: Iterable[str] | None=DEFAULT_ALLOWED_CONTENT_TYPES, headers: dict[str, str] | None=None, params: dict[str, Any] | None=None, json_body: dict[str, Any] | None=None, resolver: ResolverFn | None=None, session: requests.Session | None=None, fail_closed_peer_ip: bool | None=None, classification: str='PUBLIC', allowed_destinations: Iterable[str] | None=None) -> SafeHTTPResponse
L616 def safe_get(url: str, *, allow_redirects: bool=False, max_redirects: int=0, max_bytes: int=2 * 1024 * 1024, timeout: tuple[float, float] | float=(5.0, 10.0), allowed_content_types: Iterable[str] | None=DEFAULT_ALLOWED_CONTENT_TYPES, headers: dict[str, str] | None=None, params: dict[str, Any] | None=None, resolver: ResolverFn | None=None, session: requests.Session | None=None) -> SafeHTTPResponse
```

### `landlock_policy.py`

```python
L71 class LandlockEnforcementError(RuntimeError)
L87 class LandlockProfile()
  L95 def canonical_dict(self) -> dict[str, Any]
  L106 def policy_hash(self) -> str
L122 def probe_landlock_abi() -> int
L141 def profile_for_tool(tool_name: str, *, workdir: Path | None=None, manifest: dict[str, Any] | None=None) -> LandlockProfile
L202 def apply_landlock_profile(profile: LandlockProfile, *, strict: bool=False) -> dict[str, Any]
L309 def build_landlock_preexec(profile: LandlockProfile, *, strict: bool=False) -> Callable[[], None]
L318 def export_sharness_policy_json(profile: LandlockProfile, policy_path: Path) -> tuple[Path, str]
L334 def files_office_policy(docs_dir: Path | None=None) -> LandlockProfile
L426 def build_kernel_sandbox_cmd(cmd: list[str], profile: LandlockProfile, *, policy_dir: Path | None=None, sharness_bin: str | None=None, ai_jail_bin: str | None=None, bwrap_bin: str | None=None, workdir: Path | None=None, protect_dot_git: bool=True, strict: bool=False) -> tuple[list[str], str, str]
L539 def run_under_landlock(cmd: list[str], profile: LandlockProfile, *, strict: bool=False, timeout: float=15.0, cwd: Path | None=None) -> dict[str, Any]
```

### `config.py`

```python
L22 def set_process_name(name: str) -> None
L53 class PersistenceError(OSError)
L57 def ensure_private_dir(path: Path) -> Path
L67 def ensure_private_file(path: Path) -> Path
L77 def atomic_write_private_text(target: Path, content: str) -> None
L256 def load_neural_schema(schema_path: Path=SCHEMA_FILE) -> dict[str, Any]
L299 def load_env(env_path: Path=ENV_FILE) -> dict[str, str]
L370 def config_load_warnings() -> list[str]
L399 def is_web_comms_enabled() -> bool
L406 def get_web_comms_allowed_hosts() -> list[str]
L411 def get_libreoffice_uno_port() -> int
L421 def is_autonomy_observe_only() -> bool
L505 def get_smolLM_status() -> dict[str, Any]
L515 def get_active_local_inference_route(prefer_npu: bool=True) -> dict[str, Any]
L537 def is_lite_mode() -> bool
L576 def is_hard_blocked_path(path: Path | str) -> bool
L676 def get_assistant_name() -> str
L681 def get_assistant_nickname() -> str
L686 def get_assistant_display_label() -> str
L695 def format_persona_instructions() -> str
L713 def set_assistant_identity(name: str, nickname: str | None=None, schema_path: Path | None=None, env_path: Path | None=None) -> tuple[str, str]
L825 def get_voice_for_lang(lang_code: str) -> str
L835 def get_espeak_for_lang(lang_code: str) -> str
L947 def detect_system_ram_gb() -> int
L960 def detect_available_ram_mb() -> int
L973 def lookup_catalog_model(tag: str) -> dict[str, Any] | None
L981 def format_model_catalog(ram_gb: int | None=None) -> str
L998 def set_local_model(model_tag: str, env_path: Path | None=None, ram_gb: int | None=None) -> tuple[str, str | None]
L1048 def set_env_pairs(pairs: dict[str, str], env_path: Path | None=None) -> None
L1107 def get_recommended_whisper_model(prefer_language: str | None=None, prefer_accuracy: str | None=None) -> dict[str, Any]
L1122 def get_recommended_ollama_model(prefer_vision: bool=False, prefer_heavy: bool=False) -> dict[str, Any]
L1137 def get_recommended_edge0_model() -> dict[str, Any]
L1146 def get_full_model_decision(goal: str | None=None, configured_providers: list[str] | None=None, prefer_language: str | None=None, prefer_vision: bool=False, prefer_heavy: bool=False, prefer_parallel: bool=False) -> model_selector.FullModelDecision
L1169 def format_model_decision_summary(decision: model_selector.FullModelDecision | None=None) -> str
L1181 def apply_recommended_models(*, apply_whisper: bool=False, apply_ollama: bool=False, env_path: Path | None=None) -> dict[str, str]
```

### `connectors/base.py`

```python
L78 class ConnectorStatus(str, Enum)
L87 class ConnectorConfig()
L105 class ConnectorAction()
L115 class ActionResult()
L127 class AllowDenyGate()
  L134 def __init__(self, approval_cb: Callable[[str, str], bool] | None=None)
  L138 def requires_approval(self, risk_level: RiskLevel) -> bool
  L144 def request_approval(self, action_name: str, connector_name: str, risk_level: RiskLevel, params: dict[str, Any]) -> bool
  L174 def register_pending(self, action_id: str, action: dict[str, Any]) -> None
  L181 def resolve_pending(self, action_id: str, approved: bool) -> dict[str, Any] | None
L189 class BaseConnector()
  L199 def __init__(self, config: ConnectorConfig, approval_cb: Callable[[str, str], bool] | None=None)
  L210 def name(self) -> str
  L214 def display_name(self) -> str
  L218 def status(self) -> ConnectorStatus
  L222 def last_error(self) -> str | None
  L290 def is_configured(self) -> bool
  L294 def list_actions(self) -> list[ConnectorAction]
  L298 def execute_action(self, action_name: str, params: dict[str, Any]) -> ActionResult
  L302 def configure(self, credentials: dict[str, str]) -> bool
  L306 def deconfigure(self) -> bool
  L313 def status_row(self) -> dict[str, Any]
```

## Integration signatures and control flow

- `router.py`: `ask` (L2516) normalizes input, sets tier/dry-run controls and routes through tier handlers; `_tier0_dispatch` (L1900) handles deterministic dispatch. `route_for_autonomy` (L521) tries deterministic math, selects candidate tools, forms a decision, and applies offline/UNO policy before producing a structured result. Provider and LLM helper signatures below are static, not a claim that cloud access is enabled.
- `TurnTrace` is imported by `router.py` from `router_receipts.py:97`; it records per-turn routing receipt data.
- `capabilities.py:137` checks manifest declarations and privilege boundaries; `capabilities.py:170` checks manifest integrity. `cli_bridge.py:57` is the typed CLI gateway, with subcommand allowlists and approval for mutations.
- `learning/self_improve.py:62` collects failures, constructs and validates a proposal, runs a sandbox test, and records pending/rejected results; it does not automatically activate proposals.

### `router.py` signatures

```python
L126 def apply_offline_and_uno_policy(decision: RouteDecision, *, goal: str='', online_probe: Callable[[], bool] | None=None, uno_probe: Callable[[], bool] | None=None) -> RouteDecision | OfflineFallback
L237 def _sanitize_tool_prompt_content(content: str, *, cap: int=_TOOL_PROMPT_CAP) -> str
L258 class CelinaRouter()
  L259 def __init__(self, db: memory.MemoryDB | None=None, response_cache: cache.SmartCache | None=None, os_world_model: OSWorldModel | None=None)
  L277 def _finalize_reply(self, reply: str, source: str, trace: TurnTrace | None=None, callbacks: _bh_mod.BrainCallbacks | None=None) -> tuple[str, str]
  L307 def _register_connectors(self) -> None
  L330 def get_supervisor(self) -> Any
  L346 def _is_factual(query: str) -> bool
  L354 def _is_associative(query: str) -> bool
  L361 def _is_relational(query: str) -> bool
  L370 def _retrieval_strategy(self, query: str) -> str
  L383 def retrieve_context(self, query: str, *, k: int=8, trace: TurnTrace | None=None) -> list[dict[str, Any]]
  L399 def route_with_quiet_brain(self, query: str, *, confidence_threshold: float=0.82, trace: TurnTrace | None=None) -> dict[str, Any]
  L419 def extract_mean_logprob(response_payload: dict[str, Any]) -> float
  L441 def call_with_second_thought(self, goal: str, *, no_think_fn: Callable[[str], dict[str, Any]], think_fn: Callable[[str], dict[str, Any]], threshold: float=0.82, trace: TurnTrace | None=None) -> dict[str, Any]
  L473 def _get_compute_why(self) -> str
  L483 def warm_local_model_async(self, *, total_ram_gb: int | None=None, available_ram_mb: int | None=None) -> bool
  L515 def get_last_receipt(self) -> str
  L521 def route_for_autonomy(self, goal: str, context: dict[str, Any] | None=None) -> dict[str, Any]
  L739 def _build_messages(self, user_prompt: str, gear: str, language: str='auto', for_cloud: bool=False, active_window: str='', det_lang: str='en', lang_style: str='native', trace: TurnTrace | None=None) -> list[dict[str, Any]]
  L913 def _sanitize_messages_for_cloud(messages: list[dict[str, Any]], vault: privacy.RedactionVault) -> list[dict[str, Any]]
  L927 def _post_with_retry(self, url: str, payload: dict[str, Any], timeout: int, headers: dict[str, str] | None=None, retries: int=3, stream: bool=False) -> requests.Response
  L954 def _call_provider_chat(self, provider: Any, api_key: str, model: str, prompt: str, timeout: int=12) -> dict[str, Any]
  L997 def _run_ensemble(self, messages: list[dict[str, Any]], gear: str, vault: privacy.RedactionVault | None=None) -> dict[str, Any]
  L1062 def _execute_gear05_intent(self, micro: Any, query: str, dry_run: bool=False) -> str | None
  L1164 def _post_openrouter(self, messages: list[dict[str, Any]], gear: str, use_tools: bool=True, active_tools: list[dict[str, Any]] | None=None, stream_cb: StreamCallback | None=None, vault: privacy.RedactionVault | None=None) -> dict[str, Any]
  L1376 def _simulate_tier2_tool(fn_name: str, fn_args: dict[str, Any]) -> tuple[str, str]
  L1386 def _run_cloud_agent(self, messages: list[dict[str, Any]], gear: str, vault: privacy.RedactionVault, active_tools: list[dict[str, Any]] | None=None, trace: TurnTrace | None=None, status_cb: StatusCallback | None=None, approval_cb: ApprovalCallback | None=None, consent_cb: PrivacyConsentCallback | None=None, task_glow_cb: TaskGlowCallback | None=None, stream_cb: StreamCallback | None=None, dry_run: bool=False) -> tuple[str | None, str | None, bool]
  L1547 def _read_psi(self, psi_path: Path=Path('/proc/pressure/memory')) -> float
  L1554 def _apply_psi_kv_trim(self, ollama_opts: dict[str, Any], *, psi_path: Path=Path('/proc/pressure/memory'), threshold: float=10.0) -> dict[str, Any]
  L1579 def _post_ollama(self, messages: list[dict[str, Any]], gear: str, use_tools: bool=True, active_tools: list[dict[str, Any]] | None=None, stream_cb: StreamCallback | None=None, trace: TurnTrace | None=None) -> dict[str, Any]
  L1717 def _run_local_agent(self, messages: list[dict[str, Any]], gear: str, active_tools: list[dict[str, Any]] | None=None, trace: TurnTrace | None=None, status_cb: StatusCallback | None=None, approval_cb: ApprovalCallback | None=None, consent_cb: PrivacyConsentCallback | None=None, task_glow_cb: TaskGlowCallback | None=None, stream_cb: StreamCallback | None=None, dry_run: bool=False) -> tuple[str | None, str | None]
  L1900 def _tier0_dispatch(self, clean: str, effective_dry_run: bool, approval_cb: ApprovalCallback | None, status_cb: StatusCallback | None, consent_cb: PrivacyConsentCallback | None, task_glow_cb: TaskGlowCallback | None) -> tuple[str | None, str | None]
  L2264 def _tier0_5_micro_classify(self, clean: str, effective_dry_run: bool, approval_cb: ApprovalCallback | None, status_cb: StatusCallback | None, consent_cb: PrivacyConsentCallback | None, task_glow_cb: TaskGlowCallback | None) -> tuple[str | None, str | None, str | None]
  L2321 def _tier0_5_smollm_generate(self, clean: str, effective_dry_run: bool) -> tuple[str | None, str | None, str | None]
  L2376 def _tier1_available(self) -> bool
  L2390 def _tier1_local_fast(self, clean: str, effective_dry_run: bool, approval_cb: ApprovalCallback | None, status_cb: StatusCallback | None, consent_cb: PrivacyConsentCallback | None, task_glow_cb: TaskGlowCallback | None, stream_cb: StreamCallback | None) -> tuple[str | None, str | None, str | None]
  L2430 def _tier2_local_heavy(self, clean: str, effective_dry_run: bool, approval_cb: ApprovalCallback | None, status_cb: StatusCallback | None, consent_cb: PrivacyConsentCallback | None, task_glow_cb: TaskGlowCallback | None, stream_cb: StreamCallback | None) -> tuple[str | None, str | None, str | None]
  L2470 def _tier3_cloud(self, clean: str, effective_dry_run: bool, approval_cb: ApprovalCallback | None, status_cb: StatusCallback | None, consent_cb: PrivacyConsentCallback | None, task_glow_cb: TaskGlowCallback | None, stream_cb: StreamCallback | None) -> tuple[str | None, str | None, str | None]
  L2516 def ask(self, text: str, mode: str='auto', language: str | None=None, status_cb: StatusCallback | None=None, approval_cb: ApprovalCallback | None=None, consent_cb: PrivacyConsentCallback | None=None, task_glow_cb: TaskGlowCallback | None=None, stream_cb: StreamCallback | None=None, dry_run: bool=False, tier: int | None=None, channel: str='text', callbacks: _bh_mod.BrainCallbacks | None=None) -> tuple[str, str]
L3037 def route_query_with_meta(text: str, mode: str='auto', language: str | None=None, status_cb: StatusCallback | None=None, approval_cb: ApprovalCallback | None=None, consent_cb: PrivacyConsentCallback | None=None) -> tuple[str, str]
L3055 def process_query(text: str, mode: str='auto', language: str | None=None) -> str
L3060 def _has_gui_display() -> bool
L3064 def _terminal_approval(title: str, detail: str) -> bool
L3092 def _terminal_privacy_consent(summary: str, preview: str) -> str
L3132 def main() -> None
```

### `router_receipts.py` signatures

```python
L73 class OfflineFallback()
L85 class RouteDecision()
L97 class TurnTrace()
  L151 def format_receipt(self) -> str
L251 def _get_base_system_prompt() -> str
L255 def _has_cloud_key() -> bool
L259 def _parse_tool_args(raw: Any) -> dict[str, Any]
L276 def classify_cognitive_gear_with_reason(prompt: str) -> tuple[str, str, str]
L319 def classify_cognitive_gear(prompt: str) -> tuple[str, str]
L324 def select_swarm_role(prompt: str, gear: str) -> tuple[str, str]
L356 def _gear_settings(gear: str) -> dict[str, Any]
L396 def _is_tool_error(output: str) -> bool
L407 def _build_self_correction_hint(fn_name: str, output: str, dup_failures: int) -> str
L446 def _detect_implicit_correction(prompt: str) -> tuple[str, str] | None
L496 def is_informational_only_prompt(prompt: str) -> bool
L510 def apply_action_gate(prompt: str, active_tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]
L533 def _strip_analysis_tags(text: str) -> str
L538 def _format_stream_preview(raw_text: str, vault: privacy.RedactionVault | None=None) -> str
```

### `capabilities.py` signatures

```python
L100 def locate_manifest_path() -> Path | None
L113 def load_manifest(path: Path | None=None, *, force_reload: bool=False) -> dict[str, Any]
L130 def get_tool_capability(tool_name: str, manifest: dict[str, Any] | None=None) -> dict[str, Any] | None
L137 def check_tool_execution_boundary(tool_name: str, *, is_tier2: bool, manifest: dict[str, Any] | None=None) -> tuple[bool, str]
L170 def verify_manifest_integrity(manifest: dict[str, Any]) -> list[str]
L204 def _has_whitespace(value: Any) -> bool
L217 def validate_manifest_invariants(manifest: dict[str, Any], *, expected_tier1: set[str], expected_tier2: set[str]) -> list[str]
L333 def build_bwrap_command(tool_name: str, inner_cmd: list[str], *, workdir: Path, manifest: dict[str, Any] | None=None, allow_workdir_write: bool=False) -> list[str]
L402 def format_capabilities_summary(manifest: dict[str, Any] | None=None) -> str
```

### `cli_bridge.py` signatures

```python
L45 def _normalize_tool_name(tool: str) -> str
L50 def _strip_leading_dash_dash(args: list[str]) -> list[str]
L57 def run_typed_cli(tool: str, subcommand: str, extra_args: list[str] | None=None, *, cwd: str | None=None, approval_cb: ApprovalCallback | None=None, timeout: float=15.0) -> str
```

### `learning/self_improve.py` signatures

```python
L45 class _DefaultBwrapSandboxRunner()
  L48 def run_python(self, code: str, timeout: int=30) -> bool
L56 class SelfImprover()
  L57 def __init__(self, graph: TaskGraph, llm: Any, sandbox_runner: Any | None=None)
  L62 def run_nightly(self) -> dict[str, Any] | None
  L89 def _recent_failures(self) -> list[dict[str, Any]]
  L101 def _propose(self, failures: list[dict[str, Any]]) -> dict[str, Any] | None
  L133 def _validate_safety(self, proposal: dict[str, Any]) -> tuple[bool, str]
  L143 def _test_in_sandbox(self, proposal: dict[str, Any]) -> str
  L155 def _store_proposal(self, proposal: dict[str, Any], *, source_task_ids: list[str], test_result: str, status: str='pending') -> str
  L183 def list_proposals(self, *, status: str | None=None) -> list[dict[str, Any]]
  L193 def set_proposal_status(self, proposal_id: str, new_status: str) -> bool
  L206 def run_mars_reflection(self, failures: list[dict[str, Any]] | None=None, *, memory_engine: Any | None=None) -> dict[str, Any]
  L300 def compile_recurring_queries(self, memory_engine: Any, queries: list[str], *, big_model_fn: Callable[[str], str], source_model: str='big-offline', confidence: float=0.95) -> list[str]
L329 def _on_ac_power() -> bool
L346 def _idle_minutes() -> float
L358 def should_run_sleep_consolidation(*, min_idle_min: float=15.0) -> bool
L364 class SkilspectorScanResult()
L374 def run_sleep_consolidation(*, min_idle_min: float=15.0, memory_engine: Any | None=None, big_model_fn: Callable[[str], str] | None=None) -> dict[str, Any]
L404 def nl_to_skill_compile(*, nl_description: str, skill_name: str | None=None, sandbox_runner: Any | None=None, quarantine_dir: str | None=None) -> SkilspectorScanResult
L460 def main() -> int
```

### Tracked learning files

- `features/celina/celina_pkg/learning/__init__.py`
- `features/celina/celina_pkg/learning/self_improve.py`

### `TurnTrace` dataclass fields (constructor inputs)

```python
L100 prompt_preview: str = ''
L101 gear: str = 'fast'
L102 gear_reason: str = ''
L103 swarm_role: str = 'Executive Orchestrator'
L104 detected_lang: str = 'en'
L105 lang_style: str = 'native'
L106 active_window: str = ''
L107 tool_domains: list[str] = dataclasses.field(default_factory=list)
L108 tools_attached: int = 0
L109 tools_total: int = 0
L110 tools_called: list[str] = dataclasses.field(default_factory=list)
L111 circuit_breaker_events: list[str] = dataclasses.field(default_factory=list)
L112 capability_rejections: list[str] = dataclasses.field(default_factory=list)
L113 action_gate_suppressed: int = 0
L114 recalled_facts: list[str] = dataclasses.field(default_factory=list)
L115 recalled_episodes: int = 0
L116 active_skill_md: str = ''
L117 privacy_summary: str = 'clean'
L118 cache_status: str = 'miss'
L119 self_corrections: int = 0
L120 cot_used: bool = False
L121 dry_run: bool = False
L122 simulated_actions: list[str] = dataclasses.field(default_factory=list)
L123 learned_notes: list[str] = dataclasses.field(default_factory=list)
L124 compute_backend: str = ''
L125 mcp_server: str = ''
L126 mcp_tool: str = ''
L127 mcp_arguments_hash: str = ''
L128 mcp_transport: str = ''
L129 memweave_strategy: str = ''
L130 vision_backend: str = ''
L131 vision_latency_ms: int = 0
L132 vision_escalated: bool = False
L133 compression_backend: str = ''
L134 compression_tier: str = ''
L135 compression_saved_pct: float = 0.0
L136 compression_ccr_ids: tuple[str, ...] = ()
L137 quiet_brain_hit: bool = False
L138 second_thought_escalated: bool = False
L139 second_thought_confidence: float = 1.0
L140 autotune_ctx: int = 0
L141 autotune_keep: int = 0
L142 latency_ms: int = 0
L143 source: str = ''
L144 cloud_provider: str = ''
L145 cloud_model: str = ''
L146 static_prefix_hash: str = ''
L147 prompt_hash: str = ''
L148 completion_verified: bool = False
L149 history_compacted: bool = False
```

## Full tracked files at extraction baseline

### `features/celina/config/neural-schema.json`

```json
{
  "schema_version": "4.4",
  "persona": {
    "name": "Celina",
    "nickname": "Celly",
    "role": "Executive OS Orchestrator, Project Astra Computer-Use Agent & Multilingual Chief-of-Staff",
    "auto_match_user_language": true,
    "default_language": "auto",
    "supported_languages": [
      "en",
      "te",
      "hi"
    ],
    "system_instructions": "You are {name} (also affectionately called {nickname} by the user), an ultra-intelligent, decisive personal OS assistant running on Ubuntu 26.04 Wayland. Act like a sharp real-person chief of staff: lead with your clearest verdict or action first, avoid robotic filler, and proactively offer at most ONE high-leverage suggestion or risk warning when genuinely useful. Match the language of the user's latest message strictly (reply in English when the user writes in English; reply in Telugu or Hindi only when the user writes in Telugu/Tenglish or Hindi/Hinglish). When the user asks to rename you or change your nickname, call set_assistant_identity immediately. When the user refers to highlighted/selected text ('summarize this', 'translate this', 'debug this'), call get_highlighted_text first. Use the active desktop window context when interpreting actions ('export this to PDF'). You can perform ANYTHING a human does on a computer: open ANY website, search URL, media page, login page, or desktop app via open_application (e.g. open_application(app_name='https://open.spotify.com/search/lofi%20beats')), control playback via control_media, manage files/LibreOffice docs/MCP/goals, and for any multi-step GUI/browser click/type workflow invoke autonomous_gui_task or click_on_text/type_text/press_hotkey (never read or enter user passwords \u2014 open the login page and let the user sign in). NEVER falsely claim you cannot open websites, play media, or control apps. You can also learn, record, save, and replay skills. Never invent facts or claim an action succeeded unless a tool returned success."
  },
  "swarm_roles": {
    "architect": {
      "name": "The Architect",
      "triggers": [
        "plan",
        "architect",
        "architecture",
        "design",
        "roadmap",
        "break down",
        "project",
        "migrate",
        "goal",
        "milestone"
      ],
      "directive": "SWARM ROLE \u2014 THE ARCHITECT: Decompose the objective into a numbered DAG of concrete sub-tasks, identify dependencies and failure modes, and log or update multi-step objectives via manage_goal when appropriate."
    },
    "engineer": {
      "name": "The Coder / Engineer",
      "triggers": [
        "code",
        "debug",
        "traceback",
        "script",
        "python",
        "bash",
        "fix",
        "refactor",
        "compile",
        "git",
        "sql",
        "bug",
        "test"
      ],
      "directive": "SWARM ROLE \u2014 THE ENGINEER: Inspect actual files/logs first, isolate the single root cause, write clean modular code, and prefer sandboxed verification before declaring success."
    },
    "researcher": {
      "name": "The Researcher",
      "triggers": [
        "research",
        "search",
        "latest",
        "compare",
        "news",
        "paper",
        "specs",
        "fact check",
        "summarize",
        "benchmark"
      ],
      "directive": "SWARM ROLE \u2014 THE RESEARCHER: Cross-verify live sources via web_search/fetch_webpage or local documents, synthesize a verdict-first brief, and cite concrete dates or numbers."
    },
    "critic": {
      "name": "The Critic / Verifier",
      "directive": "SWARM ROLE \u2014 THE CRITIC: Audit proposed Tier-2 actions and outputs for security risks, data loss, privacy leaks, or hallucinated paths before final execution."
    }
  },
  "cognition": {
    "adaptive_thinking": true,
    "second_thought_confidence_gate": {
      "enabled": true,
      "min_mean_logprob": 0.82,
      "runtime_small_model": "qwen3:1.7b"
    },
    "tool_presentation": {
      "mode": "hybrid_tiered",
      "detailed_top_k": 8,
      "include_name_only_catalog": true
    },
    "tree_of_thought_on_deep": true,
    "critic_audit_on_tier2": true,
    "gears": {
      "fast": {
        "reasoning_effort": "none",
        "max_tokens": 220,
        "temperature": 0.35,
        "max_steps": 2,
        "instruction": "GEAR 1 (FAST INSTINCT): This is a quick/simple request. Do NOT overthink or write long intros. Reply immediately in 1\u20132 crisp, helpful sentences."
      },
      "tactical": {
        "reasoning_effort": "low",
        "max_tokens": 600,
        "temperature": 0.45,
        "max_steps": 5,
        "instruction": "GEAR 2 (TACTICAL ADVISOR): Think concisely and use tools directly when needed. Lead with ONE decisive recommendation or action result, state the core reason briefly, and optionally add ONE smart next-step suggestion."
      },
      "deep": {
        "reasoning_effort": "high",
        "max_tokens": 1500,
        "temperature": 0.3,
        "max_steps": 6,
        "instruction": "GEAR 3 (DEEP STRATEGIST): This is an important or complex problem. Think rigorously: verify facts/files/screen via tools, examine edge cases, trade-offs, and hidden risks, then commit to a clear verdict and concrete action plan."
      }
    },
    "fast_overrides": [
      "quick:",
      "fast:",
      "brief:",
      "short:",
      "tldr"
    ],
    "deep_overrides": [
      "deep:",
      "think deeply",
      "think hard",
      "analyze carefully",
      "step by step"
    ],
    "deep_triggers": [
      "architect",
      "architecture",
      "debug",
      "compare",
      "tradeoff",
      "trade-off",
      "strategy",
      "financial",
      "invest",
      "career",
      "legal",
      "medical",
      "optimize",
      "refactor",
      "migrate",
      "prove",
      "why does",
      "root cause",
      "plan my",
      "pros and cons",
      "security audit",
      "design a",
      "system design",
      "automate complex",
      "multi-step workflow"
    ],
    "tactical_triggers": [
      "slow",
      "lag",
      "fix",
      "error",
      "recommend",
      "suggest",
      "which should i",
      "best way",
      "how to",
      "summarize",
      "edit",
      "update",
      "create",
      "document",
      "libreoffice",
      "search",
      "find",
      "send",
      "organize",
      "automate",
      "script",
      "install",
      "download",
      "battery",
      "ram",
      "click",
      "screen",
      "ocr",
      "learn",
      "skill",
      "record",
      "workflow",
      "macro",
      "type",
      "press",
      "mcp",
      "github",
      "linear",
      "notion",
      "git",
      "cache",
      "astra",
      "scroll",
      "drag",
      "fill form",
      "youtube",
      "spotify",
      "play",
      "song",
      "songs",
      "music",
      "video",
      "watch",
      "listen",
      "login",
      "sign in",
      "open",
      "launch",
      "website",
      "netflix",
      "amazon",
      "flipkart",
      "browser"
    ]
  },
  "edge_lighting": {
    "enabled": true,
    "fullscreen_on_task": true,
    "island_on_response": true
  },
  "cache": {
    "enabled": true,
    "ram_hot_max_items": 32,
    "ram_idle_offload_sec": 300,
    "disk_offload_interval_sec": 600,
    "max_entries": 300,
    "max_size_mb": 10,
    "ttl_seconds": {
      "llm_fast": 21600,
      "llm_tactical": 7200,
      "llm_deep": 3600,
      "web_search": 900,
      "fetch_webpage": 1800,
      "document": 43200
    }
  },
  "automation": {
    "enabled": true,
    "ocr_languages": "eng+tel+hin",
    "default_step_delay_sec": 0.4,
    "max_workflow_steps": 25,
    "astra_max_steps": 10,
    "require_approval_for_gui_actions": true,
    "require_approval_for_skill_replay": true
  },
  "privacy": {
    "strict_mode": true,
    "auto_redact_pii": true,
    "require_consent_for_cloud_local_data": true,
    "require_consent_for_cloud_pii": true,
    "prefer_local_for_private_files": true,
    "hard_blocked_paths": [
      ".ssh",
      ".gnupg",
      ".aws",
      ".kube",
      ".netrc",
      ".git-credentials",
      "keyrings",
      "Login Data",
      "cookies.sqlite"
    ],
    "local_sensitive_tools": [
      "read_file",
      "read_document",
      "search_in_files",
      "get_clipboard",
      "get_highlighted_text",
      "run_shell_command",
      "read_screen",
      "find_text_on_screen",
      "inspect_ui_elements",
      "mcp_call_tool",
      "get_upcoming_events",
      "search_contacts",
      "search_browser_bookmarks",
      "prepare_meeting_context",
      "transcribe_system_audio",
      "get_secret_vault_status",
      "mount_ios_device",
      "respond_ios_notification",
      "writing_tool_transform",
      "live_text_ocr",
      "audit_app_permissions"
    ]
  },
  "routing": {
    "default_mode": "auto",
    "max_agent_steps": 5,
    "cloud_model": "openrouter/free",
    "vision_model": "openrouter/free",
    "local_model": "qwen3:1.7b",
    "ollama_num_ctx": 4096,
    "request_timeout_sec": 20,
    "local_timeout_sec": 180
  },
  "languages": {
    "en": {
      "name": "English",
      "native_name": "English",
      "badge": "EN",
      "piper_voice": "en_US-amy-medium",
      "piper_repo_path": "en/en_US/amy/medium/en_US-amy-medium",
      "espeak_voice": "en-us+f3"
    },
    "te": {
      "name": "Telugu",
      "native_name": "\u0c24\u0c46\u0c32\u0c41\u0c17\u0c41",
      "badge": "\u0c24\u0c46",
      "unicode_range": [
        "0C00",
        "0C7F"
      ],
      "piper_voice": "te_IN-maya-medium",
      "piper_repo_path": "te/te_IN/maya/medium/te_IN-maya-medium",
      "espeak_voice": "te+f3"
    },
    "hi": {
      "name": "Hindi",
      "native_name": "\u0939\u093f\u0928\u094d\u0926\u0940",
      "badge": "\u0939\u093f\u0902",
      "unicode_range": [
        "0900",
        "097F"
      ],
      "piper_voice": "hi_IN-priyamvada-medium",
      "piper_repo_path": "hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium",
      "espeak_voice": "hi+f3"
    }
  },
  "stt": {
    "whisper_model": "base",
    "whisper_idle_offload_seconds": 180,
    "auto_detect_language": true,
    "vad_engine": "auto",
    "silero_vad_threshold": 0.5,
    "audio_ducking": true,
    "duck_volume_pct": 25,
    "tts_engine": "auto",
    "kokoro_voice_en": "af_bella",
    "kokoro_voice_hi": "hf_alpha",
    "silence_stop_seconds": 1.2,
    "start_timeout_seconds": 5.0,
    "max_duration_seconds": 15.0
  },
  "security_policy": {
    "max_file_backups": 20,
    "tier1_auto_tools": [
      "get_system_stats",
      "set_volume",
      "set_brightness",
      "control_media",
      "control_connectivity",
      "open_application",
      "toggle_app_nap",
      "open_document",
      "list_running_apps",
      "search_files",
      "search_in_files",
      "read_file",
      "read_function_signatures",
      "read_lines",
      "read_document",
      "get_clipboard",
      "get_highlighted_text",
      "set_clipboard",
      "set_timer",
      "web_search",
      "fetch_webpage",
      "fetch_url_text",
      "get_weather",
      "grep_code",
      "get_news_headlines",
      "get_current_time_zone",
      "remember_fact",
      "search_memory",
      "link_knowledge",
      "list_files",
      "manage_goal",
      "save_workspace_snapshot",
      "set_assistant_identity",
      "get_hardware_capabilities",
      "set_focus_mode",
      "diagnose_systemd_service",
      "get_secret_vault_status",
      "spotlight_query",
      "route_app_audio",
      "get_continuity_status",
      "audit_app_permissions",
      "search_active_app_menus",
      "writing_tool_transform",
      "live_text_ocr",
      "get_upcoming_events",
      "search_contacts",
      "search_browser_bookmarks",
      "prepare_meeting_context",
      "read_screen",
      "find_text_on_screen",
      "inspect_ui_elements",
      "learn_skill",
      "save_last_actions_as_skill",
      "list_skills",
      "list_quarantined_skills",
      "reject_quarantined_skill",
      "update_skill",
      "forget_skill",
      "start_recording",
      "stop_recording",
      "mcp_list_servers",
      "mcp_list_tools",
      "cache_status",
      "clear_cache"
    ],
    "tier2_approval_tools": [
      "write_file",
      "edit_file",
      "edit_document",
      "create_document",
      "convert_document",
      "libreoffice_edit",
      "move_file",
      "trash_file",
      "close_application",
      "compose_message",
      "run_shell_command",
      "run_python_sandbox",
      "code_sandbox",
      "transcribe_system_audio",
      "manage_default_apps",
      "click_on_text",
      "click_position",
      "scroll_screen",
      "drag_and_drop",
      "type_text",
      "press_hotkey",
      "run_skill",
      "autonomous_gui_task",
      "mcp_call_tool",
      "install_community_skill",
      "approve_quarantined_skill",
      "trigger_iot_webhook",
      "launch_android_mirror",
      "control_airplay_mirror",
      "mount_ios_device",
      "start_ios_continuity_camera",
      "start_ipad_sidecar_weylus",
      "respond_ios_notification",
      "create_calendar_event",
      "submit_goal",
      "computer_click",
      "computer_type",
      "vision_ocr",
      "vision_analyze",
      "ghostdesk_click",
      "ponytail_evaluate",
      "skilspector_scan"
    ],
    "protected_paths": [
      ".ssh",
      ".gnupg",
      ".aws",
      ".kube",
      ".netrc",
      ".git-credentials"
    ]
  },
  "tool_domains": {
    "os_hardware": {
      "always_active": true,
      "core_fast_tools": [
        "get_system_stats",
        "set_volume",
        "set_brightness",
        "control_media",
        "control_connectivity",
        "open_application",
        "toggle_app_nap",
        "set_timer",
        "get_highlighted_text",
        "get_clipboard"
      ],
      "tools": [
        "get_system_stats",
        "set_volume",
        "set_brightness",
        "control_media",
        "control_connectivity",
        "open_application",
        "toggle_app_nap",
        "close_application",
        "list_running_apps",
        "set_timer",
        "get_clipboard",
        "get_highlighted_text",
        "set_clipboard",
        "diagnose_systemd_service",
        "manage_default_apps"
      ]
    },
    "files_office": {
      "triggers": [
        "file",
        "folder",
        "dir",
        "directory",
        "path",
        "doc",
        "docx",
        "odt",
        "pdf",
        "xlsx",
        "xls",
        "ods",
        "pptx",
        "csv",
        "sheet",
        "spreadsheet",
        "slide",
        "libreoffice",
        "writer",
        "calc",
        "impress",
        "read",
        "write",
        "edit",
        "create",
        "trash",
        "delete",
        "move",
        "rename",
        "backup",
        "resume",
        "report",
        "notes",
        "convert",
        "export",
        "find",
        "search",
        "grep",
        "code"
      ],
      "window_hints": [
        "libreoffice",
        "writer",
        "calc",
        "impress",
        "nautilus",
        "files",
        "code",
        "vim",
        "gedit",
        "evince"
      ],
      "tools": [
        "search_files",
        "search_in_files",
        "read_file",
        "write_file",
        "edit_file",
        "move_file",
        "trash_file",
        "open_document",
        "read_document",
        "create_document",
        "edit_document",
        "convert_document",
        "libreoffice_edit"
      ]
    },
    "web_comms": {
      "triggers": [
        "web",
        "google",
        "search",
        "news",
        "headlines",
        "latest",
        "price",
        "weather",
        "forecast",
        "time zone",
        "timezone",
        "http",
        "url",
        "website",
        "page",
        "article",
        "fetch",
        "summarize url",
        "lookup",
        "whatsapp",
        "telegram",
        "email",
        "mail",
        "message",
        "send",
        "text",
        "reply"
      ],
      "window_hints": [
        "firefox",
        "chrome",
        "chromium",
        "brave",
        "thunderbird",
        "telegram",
        "whatsapp"
      ],
      "tools": [
        "web_search",
        "fetch_webpage",
        "fetch_url_text",
        "get_weather",
        "get_news_headlines",
        "get_current_time_zone",
        "compose_message"
      ]
    },
    "wayland_gui": {
      "triggers": [
        "screen",
        "click",
        "double click",
        "right click",
        "type",
        "scroll",
        "drag",
        "drop",
        "hotkey",
        "shortcut",
        "press",
        "button",
        "window",
        "ocr",
        "astra",
        "fill",
        "form",
        "ui",
        "element",
        "cursor",
        "mouse",
        "automate",
        "on screen",
        "youtube",
        "spotify",
        "play",
        "song",
        "songs",
        "music",
        "video",
        "watch",
        "login",
        "sign in",
        "website",
        "webpage",
        "tab",
        "netflix",
        "amazon",
        "flipkart",
        "browser"
      ],
      "tools": [
        "read_screen",
        "find_text_on_screen",
        "inspect_ui_elements",
        "click_on_text",
        "click_position",
        "scroll_screen",
        "drag_and_drop",
        "type_text",
        "press_hotkey",
        "autonomous_gui_task"
      ]
    },
    "apple_pim_writing": {
      "triggers": [
        "calendar",
        "meeting",
        "meetings",
        "brief",
        "prepare",
        "prep",
        "schedule",
        "appointment",
        "event",
        "events",
        "upcoming",
        "tomorrow",
        "contact",
        "contacts",
        "phone number",
        "email address",
        "bookmark",
        "bookmarks",
        "proofread",
        "rewrite",
        "rephrase",
        "tone",
        "grammar",
        "polish",
        "key points",
        "bullet points",
        "writing tool",
        "live text",
        "snip",
        "extract text",
        "spotlight",
        "convert units",
        "menu",
        "menus",
        "command palette",
        "\u0c15\u0c4d\u0c2f\u0c3e\u0c32\u0c46\u0c02\u0c21\u0c30\u0c4d",
        "\u0c2e\u0c40\u0c1f\u0c3f\u0c02\u0c17\u0c4d",
        "\u0c15\u0c3e\u0c02\u0c1f\u0c3e\u0c15\u0c4d\u0c1f\u0c4d",
        "\u0915\u0948\u0932\u0947\u0902\u0921\u0930",
        "\u092e\u0940\u091f\u093f\u0902\u0917",
        "\u0915\u0949\u0928\u094d\u091f\u0948\u0915\u094d\u091f"
      ],
      "window_hints": [
        "calendar",
        "contacts",
        "thunderbird",
        "firefox",
        "libreoffice",
        "gedit"
      ],
      "tools": [
        "writing_tool_transform",
        "live_text_ocr",
        "get_upcoming_events",
        "create_calendar_event",
        "search_contacts",
        "search_browser_bookmarks",
        "prepare_meeting_context",
        "spotlight_query",
        "search_active_app_menus"
      ]
    },
    "continuity_audio_privacy": {
      "triggers": [
        "continuity",
        "iphone",
        "ipad",
        "airpods",
        "airplay",
        "uxplay",
        "shairport",
        "ifuse",
        "mount iphone",
        "sidecar",
        "weylus",
        "apple pencil",
        "continuity camera",
        "ancs",
        "answer call",
        "decline call",
        "dismiss notification",
        "transcribe",
        "meeting audio",
        "loopback",
        "summarize meeting",
        "what was just said",
        "keyring",
        "keepass",
        "keepassxc",
        "vault",
        "secret service",
        "unlock vault",
        "android",
        "scrcpy",
        "mirror",
        "localsend",
        "airdrop",
        "deskflow",
        "syncthing",
        "focus",
        "focus mode",
        "dnd",
        "do not disturb",
        "night light",
        "sleep mode",
        "coding mode",
        "work mode",
        "presentation mode",
        "route audio",
        "audio sink",
        "headphone",
        "headphones",
        "speaker",
        "speakers",
        "bluetooth",
        "pipewire",
        "output device",
        "mic",
        "microphone",
        "camera",
        "webcam",
        "permission",
        "permissions",
        "privacy",
        "audit",
        "flatseal",
        "tcc",
        "hardware",
        "biometric",
        "biometrics",
        "fingerprint",
        "touch id",
        "face id",
        "charge limit",
        "\u0c2b\u0c4b\u0c15\u0c38\u0c4d",
        "\u0c2c\u0c4d\u0c2f\u0c3e\u0c1f\u0c30\u0c40",
        "\u0c39\u0c46\u0c21\u0c4d\u200c\u0c2b\u0c4b\u0c28\u0c4d",
        "\u092b\u094b\u0915\u0938",
        "\u092c\u0948\u091f\u0930\u0940",
        "\u0939\u0947\u0921\u092b\u094b\u0928"
      ],
      "window_hints": [
        "scrcpy",
        "uxplay",
        "localsend",
        "flatseal",
        "helvum",
        "settings",
        "keepassxc",
        "zoom"
      ],
      "tools": [
        "get_continuity_status",
        "set_focus_mode",
        "route_app_audio",
        "audit_app_permissions",
        "get_hardware_capabilities",
        "get_secret_vault_status",
        "transcribe_system_audio",
        "launch_android_mirror",
        "control_airplay_mirror",
        "mount_ios_device",
        "start_ios_continuity_camera",
        "start_ipad_sidecar_weylus",
        "respond_ios_notification"
      ]
    },
    "skills_memory": {
      "triggers": [
        "remember",
        "memory",
        "fact",
        "forgot",
        "recall",
        "history",
        "skill",
        "routine",
        "workflow",
        "macro",
        "learn",
        "record",
        "recording",
        "replay",
        "save last",
        "heal",
        "update skill",
        "forget skill",
        "install skill",
        "quarantine",
        "goal",
        "goals",
        "project",
        "roadmap",
        "deadline",
        "milestone",
        "subtask",
        "knowledge",
        "graph",
        "link",
        "relationship",
        "plan",
        "workspace",
        "snapshot",
        "session",
        "name",
        "nickname",
        "rename",
        "call yourself",
        "identity",
        "\u0c17\u0c41\u0c30\u0c4d\u0c24\u0c41\u0c02\u0c1a\u0c41\u0c15\u0c4b",
        "\u0c2f\u0c3e\u0c26\u0c4d",
        "\u092f\u093e\u0926 \u0930\u0916\u094b"
      ],
      "tools": [
        "remember_fact",
        "search_memory",
        "link_knowledge",
        "manage_goal",
        "save_workspace_snapshot",
        "set_assistant_identity",
        "learn_skill",
        "save_last_actions_as_skill",
        "start_recording",
        "stop_recording",
        "list_skills",
        "list_quarantined_skills",
        "approve_quarantined_skill",
        "reject_quarantined_skill",
        "run_skill",
        "update_skill",
        "forget_skill",
        "install_community_skill"
      ]
    },
    "mcp_system": {
      "triggers": [
        "mcp",
        "connector",
        "github",
        "linear",
        "notion",
        "git",
        "repo",
        "commit",
        "branch",
        "sqlite",
        "database",
        "playwright",
        "context7",
        "shell",
        "bash",
        "terminal",
        "command",
        "python",
        "sandbox",
        "execute",
        "run code",
        "verify code",
        "systemctl",
        "journalctl",
        "service",
        "default app",
        "mime",
        "apt",
        "cache",
        "offload",
        "ram",
        "iot",
        "smart home",
        "home assistant",
        "webhook",
        "mqtt",
        "lights",
        "switch"
      ],
      "window_hints": [
        "terminal",
        "ptyxis",
        "console",
        "kitty",
        "alacritty",
        "code"
      ],
      "tools": [
        "mcp_list_servers",
        "mcp_list_tools",
        "mcp_call_tool",
        "run_shell_command",
        "run_python_sandbox",
        "trigger_iot_webhook",
        "cache_status",
        "clear_cache",
        "submit_goal"
      ]
    },
    "mcp_perception_actuation": {
      "tier": 2,
      "servers": [
        "computer-use-linux",
        "shadow-vision",
        "ghostdesk"
      ],
      "triggers": [
        "computer-use",
        "shadow-vision",
        "ghostdesk",
        "eye2byte",
        "uacc",
        "deskwright",
        "semantic click",
        "vision ocr",
        "vision analyze",
        "virtual desktop",
        "isolated desktop",
        "ui map",
        "mcp click",
        "mcp type"
      ],
      "tools": [
        "computer_click",
        "computer_type",
        "vision_ocr",
        "vision_analyze",
        "ghostdesk_click"
      ]
    },
    "code_exec": {
      "triggers": [
        "code",
        "script",
        "python",
        "bash",
        "shell script",
        "run code",
        "execute python",
        "run bash",
        "calculate",
        "compute",
        "sandbox",
        "verify code",
        "grep",
        "signature",
        "function",
        "class definition",
        "debug",
        "compile",
        "interpreter",
        "spreadsheet calc",
        "data analysis",
        "automate task",
        "python sandbox",
        "bash sandbox"
      ],
      "window_hints": [
        "terminal",
        "ptyxis",
        "console",
        "code",
        "vim",
        "gedit",
        "code"
      ],
      "tools": [
        "code_sandbox",
        "run_python_sandbox",
        "run_shell_command",
        "grep_code",
        "list_files",
        "read_function_signatures",
        "read_lines"
      ]
    },
    "skill_security": {
      "triggers": [
        "skill",
        "scan",
        "security",
        "vulnerability",
        "mcp",
        "install skill",
        "skilspector",
        "ponytail",
        "risk",
        "audit",
        "supply chain",
        "prompt injection",
        "data exfiltration",
        "skill inspector"
      ],
      "window_hints": [
        "terminal",
        "code",
        "console",
        "editor"
      ],
      "tools": [
        "ponytail_evaluate",
        "skilspector_scan"
      ]
    }
  },
  "world_model": {
    "track_active_window": true,
    "proactive_triggers": {
      "ram_pressure_pct": 88,
      "battery_low_pct": 18
    }
  },
  "confidence_policy": {
    "act_immediately_min_confidence": 0.75,
    "enable_action_gate": true,
    "clarify_if_ambiguous_destructive": true,
    "clarification_instruction": "If a destructive or irreversible request (delete, overwrite, send external message) is ambiguous about the target file or recipient, ask ONE brief clarifying question before invoking tools."
  },
  "reflection_policy": {
    "enable_preflight_micro_plan": true,
    "enable_error_self_correction": true,
    "max_identical_tool_failures": 2,
    "verify_astra_done_state": true,
    "enable_implicit_correction_learning": true,
    "enable_habit_mining": true,
    "habit_suggestion_threshold": 3
  },
  "embeddings": {
    "engine": "auto",
    "model": "embeddinggemma",
    "model2vec_model": "minishlab/potion-multilingual-128M",
    "dim": 256,
    "keep_alive": "30s",
    "rrf_k": 60,
    "semantic_cache_enabled": true,
    "semantic_cache_threshold": 0.92,
    "tool_router_min_sim": 0.38
  },
  "web_comms": {
    "enabled": false,
    "allowed_hosts": []
  },
  "libreoffice": {
    "uno_port": 2002
  },
  "autonomy": {
    "observe_only": true
  }
}
```

### `tools/validate_configs.py`

```python
#!/usr/bin/env python3
"""
macubuntu / Celina Configuration Integrity Validator (P1)

Validates all JSON configuration files for:
  1. Whitespace corruption — no key or structural string value may differ
     from its .strip(). E.g. "tools " is rejected, "tools" is accepted.
  2. JSON Schema conformance with additionalProperties:false — unknown keys
     are rejected, not silently ignored.
  3. Fail-closed: any parse error, whitespace violation, or schema violation
     aborts with a named error and a non-zero exit code. Never degrades
     silently.

Usage:
    python tools/validate_configs.py               # validate all known configs
    python tools/validate_configs.py --file X.json # validate one file
    python tools/validate_configs.py --schema      # also emit compiled schemas

Exit codes:
    0 = all configs valid
    1 = validation failure (whitespace, schema, or parse error)
    2 = usage error
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import jsonschema
except ImportError:
    jsonschema = None

REPO_ROOT = Path(__file__).resolve().parent.parent

CONFIG_FILES: List[Tuple[Path, Optional[Dict[str, Any]], Optional[set]]] = [
    (
        REPO_ROOT / "system" / "capabilities" / "celina-capabilities.json",
        "celina-capabilities",
        {"tier", "fs_scope", "network_egress", "kernel_boundary", "credential_access", "auth_operation", "domain", "_audit_note"},
    ),
    (
        REPO_ROOT / "features" / "celina" / "config" / "celina-capabilities.json",
        "celina-capabilities",
        {"tier", "fs_scope", "network_egress", "kernel_boundary", "credential_access", "auth_operation", "domain", "_audit_note"},
    ),
    (
        REPO_ROOT / "features" / "celina" / "config" / "neural-schema.json",
        "neural-schema",
        set(),
    ),
    (
        REPO_ROOT / "features" / "celina" / "config" / "mcp-servers.json",
        "mcp-servers",
        set(),
    ),
]

CELINA_CAPABILITIES_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Celina OS Capability Manifest",
    "type": "object",
    "additionalProperties": False,
    "required": ["manifest_version", "celina_version", "enforcement_model", "never_exposed_paths", "tools", "integrity"],
    "properties": {
        "manifest_version": {"type": "string", "pattern": r"^\d+\.\d+$"},
        "celina_version": {"type": "string", "pattern": r"^\d+\.\d+$"},
        "enforcement_model": {"type": "string", "minLength": 1},
        "never_exposed_paths": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "tools": {
            "type": "object",
            "additionalProperties": False,
            "patternProperties": {
                "^[a-z][a-z0-9_]*$": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["domain", "tier", "fs_scope", "network_egress", "credential_access", "pipewire", "auth_operation"],
                    "properties": {
                        "domain": {"type": "string", "minLength": 1},
                        "tier": {"type": "string", "enum": ["tier0", "tier0_5", "tier1", "tier2", "tier3"]},
                        "fs_scope": {
                            "type": "string",
                            "enum": ["none", "celina_state_only", "home_ro", "home_rw_approval_undo", "bwrap_workdir"],
                        },
                        "network_egress": {
                            "type": "string",
                            "enum": ["none", "loopback_ollama", "https_public_only", "lan_mdns", "iot_allowlist", "mcp_declared"],
                        },
                        "dbus_buses": {
                            "type": "array",
                            "items": {
                                "oneOf": [
                                    {"type": "string", "minLength": 1},
                                    {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["bus", "service", "path", "interface", "methods"],
                                        "properties": {
                                            "bus": {"type": "string", "minLength": 1},
                                            "service": {"type": "string", "minLength": 1},
                                            "path": {"type": "string", "minLength": 1},
                                            "interface": {"type": "string", "minLength": 1},
                                            "methods": {
                                                "type": "array",
                                                "items": {"type": "string", "minLength": 1},
                                            },
                                        },
                                    },
                                ],
                            },
                        },
                        "pipewire": {
                            "oneOf": [
                                {"type": "boolean"},
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["direction", "portal_mediated", "node_classes", "capture"],
                                    "properties": {
                                        "direction": {"type": "string", "minLength": 1},
                                        "portal_mediated": {"type": "boolean"},
                                        "node_classes": {
                                            "type": "array",
                                            "items": {"type": "string", "minLength": 1},
                                        },
                                        "capture": {"type": "boolean"},
                                    },
                                },
                            ],
                        },
                        "kernel_boundary": {"type": "string", "minLength": 1},
                        "credential_access": {"type": "string", "enum": ["none", "secret_material"]},
                        "auth_operation": {"type": "string", "minLength": 1},
                        "_audit_note": {"type": "string", "minLength": 1},
                        "mcp_server": {"type": "string", "minLength": 1},
                        "mcp_transport": {"type": "string", "minLength": 1},
                        "opt_in": {"type": "boolean"},
                    },
                }
            },
        },
        "integrity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["sha256", "signed_by", "schema_version", "default_policy"],
            "properties": {
                "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
                "signed_by": {"type": "string", "minLength": 1},
                "schema_version": {"type": "string", "minLength": 1},
                "default_policy": {"type": "string", "enum": ["deny"]},
            },
        },
    },
}

NEURAL_SCHEMA_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Celina Neural Schema (runtime config)",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "security_policy"],
    "properties": {
        "schema_version": {"type": "string", "pattern": r"^\d+\.\d+$"},
        "persona": {"type": "object"},
        "swarm_roles": {"type": "object"},
        "cognition": {"type": "object"},
        "edge_lighting": {"type": "object"},
        "cache": {"type": "object"},
        "automation": {"type": "object"},
        "privacy": {"type": "object"},
        "routing": {"type": "object"},
        "languages": {"type": "object"},
        "stt": {"type": "object"},
        "security_policy": {
            "type": "object",
            "additionalProperties": False,
            "required": ["max_file_backups", "tier1_auto_tools", "tier2_approval_tools", "protected_paths"],
            "properties": {
                "max_file_backups": {"type": "integer", "minimum": 0},
                "tier1_auto_tools": {"type": "array", "items": {"type": "string"}},
                "tier2_approval_tools": {"type": "array", "items": {"type": "string"}},
                "protected_paths": {"type": "array", "items": {"type": "string"}},
            },
        },
        "tool_domains": {"type": "object"},
        "world_model": {"type": "object"},
        "confidence_policy": {"type": "object"},
        "reflection_policy": {"type": "object"},
        "embeddings": {"type": "object"},
        "web_comms": {"type": "object"},
        "libreoffice": {"type": "object"},
        "autonomy": {"type": "object"},
    },
}

MCP_SERVERS_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "MCP Server Configuration",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "idle_offload_seconds": {"type": "integer", "minimum": 0},
        "mcpServers": {"type": "object"},
        "servers": {"type": "object"},
    },
}

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "celina-capabilities": CELINA_CAPABILITIES_SCHEMA,
    "neural-schema": NEURAL_SCHEMA_SCHEMA,
    "mcp-servers": MCP_SERVERS_SCHEMA,
}


class ConfigValidationError(Exception):
    pass


def check_whitespace(
    obj: Any,
    path: str = "$",
    errors: Optional[List[str]] = None,
    exempt_keys: Optional[set] = None,
) -> List[str]:
    if errors is None:
        errors = []
    if exempt_keys is None:
        exempt_keys = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key != key.strip():
                errors.append(f"{path}: key '{key}' has leading/trailing whitespace")
            # Exempt designated structural keys (e.g. _audit_note) from whitespace checks
            if key in exempt_keys:
                continue
            check_whitespace(value, f"{path}.{key}", errors, exempt_keys)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            check_whitespace(item, f"{path}[{i}]", errors, exempt_keys)
    elif isinstance(obj, str):
        if obj != obj.strip():
            errors.append(f"{path}: string value {obj!r} has leading/trailing whitespace")
    return errors


def validate_schema(obj: Any, schema: Dict[str, Any], path: str, errors: List[str]) -> None:
    if jsonschema is None:
        errors.append(f"{path}: jsonschema not installed — cannot perform schema validation")
        return
    try:
        jsonschema.validate(obj, schema)
    except jsonschema.ValidationError as exc:
        err_path = "/".join(str(p) for p in exc.absolute_path)
        errors.append(f"{path}: schema violation — {exc.message} (at {err_path or 'root'})")
    except jsonschema.SchemaError as exc:
        errors.append(f"{path}: invalid schema — {exc.message}")


def validate_file(path: Path, schema_name: Optional[str], structural_keys: Optional[set]) -> List[str]:
    errors: List[str] = []
    if not path.is_file():
        return [f"{path}: file not found"]
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: cannot read — {exc}"]
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON — {exc.msg} at line {exc.lineno}, col {exc.colno}"]
    ws_errors = check_whitespace(obj, str(path), exempt_keys=structural_keys)
    errors.extend(ws_errors)
    schema = SCHEMAS.get(schema_name) if schema_name else None
    if schema is not None:
        validate_schema(obj, schema, str(path), errors)
    elif schema_name is not None:
        errors.append(f"{path}: no schema registered for '{schema_name}'")
    return errors


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    single_file: Optional[str] = None
    emit_schemas = False
    i = 0
    while i < len(argv):
        if argv[i] == "--file" and i + 1 < len(argv):
            single_file = argv[i + 1]
            i += 2
        elif argv[i] == "--schema":
            emit_schemas = True
            i += 1
        elif argv[i] in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print(f"Unknown argument: {argv[i]}", file=sys.stderr)
            return 2
    if emit_schemas:
        for name, schema in SCHEMAS.items():
            print(f"# Schema: {name}")
            print(json.dumps(schema, indent=2))
            print()
        return 0
    all_errors: List[str] = []
    targets: List[Tuple[Path, Optional[str], Optional[set]]]
    if single_file:
        sf = Path(single_file)
        match = None
        for path, sname, skeys in CONFIG_FILES:
            if path == sf or path.name == sf.name:
                match = (sf, sname, skeys)  # PRESERVE sf, never replace with hardcoded repo path
                break
        if match is None:
            match = (sf, None, None)
        targets = [match]
    else:
        targets = CONFIG_FILES
    for path, schema_name, structural_keys in targets:
        file_errors = validate_file(path, schema_name, structural_keys)
        if file_errors:
            all_errors.extend(file_errors)

    # Cross-file parity check (full validation only, no --file)
    if not single_file:
        cap_system = REPO_ROOT / "system" / "capabilities" / "celina-capabilities.json"
        cap_feature = REPO_ROOT / "features" / "celina" / "config" / "celina-capabilities.json"
        neural = REPO_ROOT / "features" / "celina" / "config" / "neural-schema.json"

        # 1. The two celina-capabilities.json copies must be identical
        if cap_system.is_file() and cap_feature.is_file():
            try:
                sys_obj = json.loads(cap_system.read_text(encoding="utf-8"))
                feat_obj = json.loads(cap_feature.read_text(encoding="utf-8"))
                if sys_obj != feat_obj:
                    all_errors.append(
                        "parity: system/capabilities/celina-capabilities.json and "
                        "features/celina/config/celina-capabilities.json differ"
                    )
            except json.JSONDecodeError:
                pass  # already reported by validate_file above

        # 2. tier1_auto_tools | tier2_approval_tools == manifest tools keys; tier1 & tier2 == empty
        if neural.is_file():
            try:
                neural_obj = json.loads(neural.read_text(encoding="utf-8"))
                sec = neural_obj.get("security_policy", {})
                tier1 = set(sec.get("tier1_auto_tools", []))
                tier2 = set(sec.get("tier2_approval_tools", []))
                for cap_path in (cap_system, cap_feature):
                    if cap_path.is_file():
                        try:
                            cap_obj = json.loads(cap_path.read_text(encoding="utf-8"))
                            manifest_tools = set(cap_obj.get("tools", {}).keys())
                            union = tier1 | tier2
                            if union != manifest_tools:
                                missing = manifest_tools - union
                                extra = union - manifest_tools
                                all_errors.append(
                                    f"parity: tier1|tier2 != manifest tools in {cap_path.name} "
                                    f"(missing={missing}, extra={extra})"
                                )
                            overlap = tier1 & tier2
                            if overlap:
                                all_errors.append(
                                    f"parity: tier1 & tier2 not disjoint: {overlap}"
                                )
                        except json.JSONDecodeError:
                            pass
            except json.JSONDecodeError:
                pass

    if all_errors:
        print(f"\nCONFIG INTEGRITY VALIDATION FAILED ({len(all_errors)} error(s)):", file=sys.stderr)
        for err in all_errors:
            print(f"   • {err}", file=sys.stderr)
        print("\n   Fail-closed: refusing to continue. Fix the config(s) above.", file=sys.stderr)
        return 1
    print(f"All {len(targets)} config file(s) passed integrity validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### `version.toml`

```toml
# macubuntu / Celina — Single Version Source (P1)
# All version fields across the repository are derived from this file.
# The installer, manifest, neural-schema, and ARCHITECTURE.md must agree.

[celina]
# Deployed Celina AI OS version. Read by install.sh, embedded in
# celina-capabilities.json (celina_version) and neural-schema.json (schema_version).
version = "4.4"

[manifest]
# OS capability manifest schema version. Embedded in
# celina-capabilities.json (manifest_version).
version = "1.0"

[schema]
# Neural schema version. Embedded in neural-schema.json (schema_version).
version = "4.4"

[doc]
# ARCHITECTURE.md document version. Informational only; not code-enforced.
version = "4.5"

[release]
# Packaging/release semver. Single source of truth shared by build-deb.sh
# and build-release.sh. Must match VERSION and debian/changelog.
semver = "4.4.0"
```

### `pyproject.toml`

```toml
[project]
name = "macubuntu-celina"
version = "4.4.0"
description = "Celina AI assistant - security-focused local-first agent framework"
requires-python = ">=3.12"

[tool.ruff]
# Default: exclude obvious false-positive zones from strict rules
line-length = 100
src = ["features/celina"]

[tool.ruff.lint]
# Select all rules by default, then override per-module below
select = ["ALL"]
ignore = [
    # UP045: type: ignore without code — handled by mypy instead
    "UP045",
    # BLE001: blind except — many are intentional for optional deps
    "BLE001",
    # S110: try-except-pass — many are intentional for optional imports
    "S110",
    # F401: unused imports — may be re-exported
    "F401",
]

[tool.ruff.lint.per-file-ignores]
# Security-critical modules: enforce S, B, ASYNC rules strictly
"features/celina/celina_pkg/approval.py" = []
"features/celina/celina_pkg/evidence.py" = []
"features/celina/celina_pkg/config.py" = []
"features/celina/celina_pkg/connectors/base.py" = []
"features/celina/celina_pkg/net_policy.py" = []
"features/celina/celina_pkg/tools.py" = []
"features/celina/celina_pkg/cli_bridge.py" = []

# Test files: relax some rules
"tests/**" = [
    "S101",  # assert in tests
    "BLE001",  # blind except in test mocks
    "S110",  # try-except-pass in test fixtures
]

# Hardware/platform-specific code: relax import rules
"features/celina/celina_hardware/**" = [
    "F401",  # optional hardware imports
    "BLE001",  # blind except for optional hardware
    "S110",  # try-except-pass for optional hardware
]

# Optional dependency modules
"features/celina/celina_pkg/*_optional.py" = [
    "F401",
    "BLE001",
    "S110",
]

[tool.ruff.lint.isort]
known-first-party = ["celina_pkg", "config", "tools"]
```

### `CODEOWNERS`

```text
# CODEOWNERS — Celina Protected Core (Phase 9 B2)
#
# Files listed here require explicit review from the designated owner
# before any change can be merged. This protects the security-critical
# core from silent weakening.
#
# Pattern syntax matches GitHub CODEOWNERS: https://docs.github.com/en/repositories/managing-your-repository/about-code-owners

# ======================================================================
# Protected core security modules — require security team review
# ======================================================================
/features/celina/celina_pkg/approval.py        @security-core
/features/celina/celina_pkg/evidence.py        @security-core
/features/celina/celina_pkg/config.py          @security-core
/features/celina/celina_pkg/connectors/base.py @security-core
/features/celina/celina_pkg/net_policy.py      @security-core
/features/celina/celina_pkg/tools.py           @security-core
/features/celina/celina_pkg/cli_bridge.py      @security-core

# ======================================================================
# Protected core manifest — 106-tool capability manifest (B1)
# ======================================================================
/system/capabilities/celina-capabilities.json   @security-core @audit
/features/celina/config/celina-capabilities.json @security-core @audit

# ======================================================================
# Verification gate and integrity tooling — changes require review
# ======================================================================
/scripts/verify_all.sh                          @security-core
/tools/manifest_integrity.py                    @security-core
/tools/validate_configs.py                      @security-core

# ======================================================================
# Security tooling configuration
# ======================================================================
/pyproject.toml                                 @security-core
/.secrets.baseline                              @security-core
/.pre-commit-config.yaml                        @security-core

# ======================================================================
# CI workflows — changes require review
# ======================================================================
/.github/workflows/                             @security-core
/.github/dependabot.yml                         @security-core

# Pre-self-improvement freeze: protected baseline and guardrails
/features/celina/celina_pkg/landlock_policy.py  @security-core
/selfimprove/protected_hashes.json               @security-core
/tests/selfimprove/test_protected_core.py        @security-core
/tests/selfimprove/test_layering.py              @security-core
/CODEOWNERS                                     @security-core
```

## Environment and hardware snapshot

- Python: `3.13.18 (main, Nov  5 2026, 09:37:13) [GCC 14.2.0]`
- pytest: `9.0.3`
- ruff: `NOT INSTALLED / UNVERIFIED`
- mypy: `NOT INSTALLED / UNVERIFIED`
- bandit: `1.9.4`

### Optional package installation and license metadata

| Distribution | Installed version | Installed metadata license |
|---|---|---|
| `deepeval` | NOT INSTALLED | UNVERIFIED (not installed) |
| `lancedb` | NOT INSTALLED | UNVERIFIED (not installed) |
| `dspy` | NOT INSTALLED | UNVERIFIED (not installed) |
| `opik` | NOT INSTALLED | UNVERIFIED (not installed) |
| `inspect-ai` | NOT INSTALLED | UNVERIFIED (not installed) |
| `lm-eval` | NOT INSTALLED | UNVERIFIED (not installed) |

### Recent commits

```
e5924ce docs(audit): update FINAL.md with 85.20% config coverage, worktree proof, and secret triage
ae32b49 docs(audit): triage tracked secret scan candidates and correct final report
54b7a0e fix(tests): resolve repo root dynamically instead of hardcoding checkout path
a0cdb31 fix(coverage): cover final missing branch in config.py to reach >=85% [audit-7d]
d6004a5 chore(repo): track core runtime modules, security tests, and policy assets
cd0959a chore(repo): gitignore local audit/coverage scratch artifacts
6b7c065 docs(audit): update progress tracker with FINAL.md accuracy corrections [audit-7d]
1675b3e docs(audit): tighten FINAL.md accuracy — unfiltered bandit counts + exact coverage table [audit-7d]
```

### Hardware summary

- `/etc/macubuntu/hardware.json`: absent
- `MemTotal:        2030416 kB`
- GPU: UNVERIFIED: lspci unavailable or GPU not listed

## Authorized post-freeze EvidenceLedger anchor correction

The user authorized a narrowly scoped missing-anchor correction after the frozen `pre-selfimprove-freeze` tag. Frozen-tag evidence.py SHA-256 remains `b1fe25dd173f0789db358e012a19785739554bf1fde8ba4ec922a1a7a8baefab`; the updated protected manifest pins `11e9747977349e31cbad219eadad10a558de147c05b273eaa4c297d847900a66`. The tag is unchanged and continues to identify the historical baseline. `verify_chain()` retains its tuple contract; `verify()` raises on failures; open and append reject missing anchors for non-empty ledgers. Confirmed reset does not implicitly re-trust non-empty unanchored bytes.
