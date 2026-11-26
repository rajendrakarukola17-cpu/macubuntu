"""Celina AI (Celly) - Configuration & Neural Schema Loader (v4.2)
Precedence: process environment > ~/.config/celina/env > ~/.config/celina/neural-schema.json > defaults.
Assistant display name & nickname default to Celina / Celly and can be updated anytime via
/name, set_assistant_identity, ~/.config/celina/env, or neural-schema.json.
"""

import ctypes
import fcntl
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import model_selector


def set_process_name(name: str) -> None:
    """Set the visible process name via prctl(PR_SET_NAME) on Linux.

    Used so ananicy-cpp rules (which match on comm name like `celina-daemon`,
    `celina-search`) actually match Celina processes — otherwise they run
    as `python3` and the ananicy rules never fire.
    Zero new dependencies: uses stdlib `ctypes` only.
    """
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_NAME = 15
        buf = ctypes.create_string_buffer(name.encode("utf-8")[:15])
        result = libc.prctl(PR_SET_NAME, ctypes.byref(buf), 0, 0, 0)
        if result != 0:
            import errno
            err = ctypes.get_errno()
            if err != errno.EPERM:
                raise OSError(err, f"prctl(PR_SET_NAME={name!r}) failed")
    except (OSError, AttributeError):
        # Fails open — process naming is a nice-to-have for ananicy matching.
        # EPERM is expected if we lack permission; AttributeError if ctypes lacks prctl.
        pass

try:
    os.umask(0o077)
except OSError:
    pass


class PersistenceError(OSError):
    """Raised when atomic persistence of configuration/identity fails on disk."""


def ensure_private_dir(path: Path) -> Path:
    """Creates directory with mode 0700 and hardens existing directory permissions."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def ensure_private_file(path: Path) -> Path:
    """Ensures a private file exists (if created) and has mode 0600."""
    ensure_private_dir(path.parent)
    if not path.exists():
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    os.chmod(path, 0o600)
    return path


def atomic_write_private_text(target: Path, content: str) -> None:
    """Atomically writes text to target with fsync + mode 0600.
    Raises OSError on any failure without corrupting the existing target file.
    """
    ensure_private_dir(target.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
        os.chmod(target, 0o600)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# --- Core Paths ---
# P2-04: Resolve target user identity (not root when running under sudo).
# Use TARGET_HOME instead of Path.home() so all Celina paths point to the
# actual desktop user's home, even if the process is running as root.
def _resolve_target_home() -> Path:
    """Resolve the actual target user's home directory.

    Reads TARGET_HOME from env (set by core/common.sh when sourced), falls back
    to SUDO_USER home, then to current user's home. Never returns /root when
    running under sudo.
    """
    import pwd
    target_home = os.environ.get("TARGET_HOME", "").strip()
    if target_home:
        return Path(target_home)
    sudo_user = os.environ.get("SUDO_USER", "").strip()
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path.home()


HOME = _resolve_target_home()
BASE_DIR = HOME / ".local" / "share" / "celina"
# P0-09: Canonical storage root — same as BASE_DIR (~/.local/share/celina/),
# matching ARCHITECTURE.md and mcp-servers.json. Not ~/.celina/storage/shared/.
SHARED_STORAGE_DIR = BASE_DIR
CONFIG_DIR = HOME / ".config" / "celina"
VOICES_DIR = BASE_DIR / "voices"
WAKEWORD_DIR = BASE_DIR / "wakeword"
BACKUPS_DIR = BASE_DIR / "backups"
UNDO_DIR = BASE_DIR / "undo"
CHECKPOINTS_DIR = BASE_DIR / "checkpoints"
SKILLS_DIR = BASE_DIR / "skills"
SKILLS_TRUSTED_DIR = SKILLS_DIR / "trusted"
SKILLS_QUARANTINE_DIR = SKILLS_DIR / "quarantine"
LOGS_DIR = BASE_DIR / "logs"
ENV_FILE = CONFIG_DIR / "env"
SCHEMA_FILE = CONFIG_DIR / "neural-schema.json"
MCP_CONFIG_FILE = CONFIG_DIR / "mcp-servers.json"
IOT_ALLOWLIST_FILE = CONFIG_DIR / "iot-allowlist.json"
DB_PATH = SHARED_STORAGE_DIR / "memory.db"
CACHE_DB_PATH = SHARED_STORAGE_DIR / "cache.db"
NAPPED_PIDS_FILE = BASE_DIR / "napped_pids.json"
LEGACY_GRAPH_FILE = BASE_DIR / "semantic_memory.json"
STOP_AUTOMATION_FILE = BASE_DIR / "STOP_AUTOMATION"

# User-scoped IPC & Wayland ydotool socket paths
# P0-08: Never fall back to /tmp for IPC sockets. Use a private 0700 dir
# under the user's home when XDG_RUNTIME_DIR is unavailable (ssh, cron, early boot).
_RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or "")
if not _RUNTIME_DIR.is_dir() and os.environ.get("DRY_RUN") != "1":
    _RUNTIME_DIR = HOME / ".local" / "run" / "celina"
    _RUNTIME_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
elif not _RUNTIME_DIR.is_dir():
    _RUNTIME_DIR = HOME / ".local" / "run" / "celina"
SOCKET_PATH = str(_RUNTIME_DIR / f"celina_island_{os.getuid()}.sock")
DEFAULT_YDOTOOL_SOCKET = str(_RUNTIME_DIR / ".ydotool_socket")

# Ensure private runtime directories exist with 0700 permissions
# Skip during DRY_RUN to keep HOME side-effect-free (P0-11)
if os.environ.get("DRY_RUN") != "1":
    for _d in (
        BASE_DIR,
        CONFIG_DIR,
        VOICES_DIR,
        WAKEWORD_DIR,
        BACKUPS_DIR,
        UNDO_DIR,
        CHECKPOINTS_DIR,
        SKILLS_DIR,
        SKILLS_TRUSTED_DIR,
        SKILLS_QUARANTINE_DIR,
        LOGS_DIR,
    ):
        ensure_private_dir(_d)

# Harden existing config files to 0600 when present
for _f in (ENV_FILE, SCHEMA_FILE, MCP_CONFIG_FILE, IOT_ALLOWLIST_FILE):
    if _f.exists():
        try:
            os.chmod(_f, 0o600)
        except OSError:
            pass

def _load_default_neural_schema() -> dict[str, Any]:
    """Load DEFAULT_NEURAL_SCHEMA from the shipped JSON template (single source of truth).

    Reads features/celina/config/neural-schema.json from the repo. Falls back to
    a minimal built-in dict if the file is missing (defensive only).
    """
    import json as _json

    _repo_cfg = Path(__file__).resolve().parents[3] / "features" / "celina" / "config" / "neural-schema.json"
    if _repo_cfg.is_file():
        try:
            return _json.loads(_repo_cfg.read_text(encoding="utf-8"))  # type: ignore[no-any-return]  # json.loads returns Any; schema validated by caller
        except (OSError, _json.JSONDecodeError):
            pass
    # Minimal defensive fallback (never used in practice)
    return {
        "schema_version": "4.4",
        "persona": {
            "name": "Celina",
            "nickname": "Celly",
            "auto_match_user_language": True,
            "default_language": "auto",
            "supported_languages": ["en", "te", "hi"],
            "system_instructions": (
                "You are {name} (also affectionately called {nickname} by the user), an ultra-intelligent, decisive personal OS assistant running on Ubuntu 26.04 Wayland. "
                "Lead with your clearest verdict or action, avoid filler, and offer at most ONE high-leverage proactive suggestion. "
                "Match the language of the user's latest message strictly (reply in English when the user writes in English; reply in Telugu or Hindi only when the user writes in Telugu/Tenglish or Hindi/Hinglish). "
                "When the user asks to rename you or change your nickname, call set_assistant_identity immediately. "
                "You can read the screen via offline OCR, click/type/press hotkeys on Wayland, manage knowledge graphs & goals, and learn/record/replay multi-step skills."
            ),
        },
    }


DEFAULT_NEURAL_SCHEMA: dict[str, Any] = _load_default_neural_schema()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged




def _parse_schema_ver(ver: Any) -> tuple[int, int]:
    import re as _re
    m = _re.match(r"^\s*(\d+)(?:\.(\d+))?", str(ver or "0.0"))
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2) or 0))


def _union_list(base_list: Any, user_list: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for src in (base_list, user_list):
        if isinstance(src, list):
            for item in src:
                s = str(item)
                if s not in seen:
                    seen.add(s)
                    out.append(s)
    return out

def load_neural_schema(schema_path: Path = SCHEMA_FILE) -> dict[str, Any]:
    if schema_path.exists():
        try:
            loaded = json.loads(schema_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                merged = _deep_merge(DEFAULT_NEURAL_SCHEMA, loaded)
                cur_ver = _parse_schema_ver(DEFAULT_NEURAL_SCHEMA.get("schema_version", "4.3"))
                disk_ver = _parse_schema_ver(loaded.get("schema_version", "0.0"))
                if disk_ver < cur_ver:
                    # Seamless upgrade migration: preserve user persona/routing/settings while
                    # ensuring newly added tools, privacy guards & 8-domain routing stay active!
                    base_sec = DEFAULT_NEURAL_SCHEMA.get("security_policy", {})
                    merged_sec = dict(merged.get("security_policy") or {})
                    for key in ("tier1_auto_tools", "tier2_approval_tools", "protected_paths"):
                        merged_sec[key] = _union_list(base_sec.get(key), merged_sec.get(key))
                    # Fail-closed precedence: tools promoted to Tier 2 MUST be removed from Tier 1
                    t2_set = set(merged_sec.get("tier2_approval_tools") or [])
                    merged_sec["tier1_auto_tools"] = [
                        t for t in (merged_sec.get("tier1_auto_tools") or []) if t not in t2_set
                    ]
                    merged["security_policy"] = merged_sec

                    base_priv = DEFAULT_NEURAL_SCHEMA.get("privacy", {})
                    merged_priv = dict(merged.get("privacy") or {})
                    for key in ("local_sensitive_tools", "hard_blocked_paths"):
                        merged_priv[key] = _union_list(base_priv.get(key), merged_priv.get(key))
                    merged["privacy"] = merged_priv

                    # Refresh tool_domains to clean v4.3 8-domain partition (keeping any user custom domains)
                    base_doms = DEFAULT_NEURAL_SCHEMA.get("tool_domains", {})
                    upgraded_doms = {k: dict(v) for k, v in base_doms.items()}
                    for k, v in (loaded.get("tool_domains") or {}).items():
                        if k not in upgraded_doms and isinstance(v, dict):
                            upgraded_doms[k] = v
                    merged["tool_domains"] = upgraded_doms
                    merged["schema_version"] = str(DEFAULT_NEURAL_SCHEMA["schema_version"])
                return merged
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            import sys
            print(f"[celina.config] schema load fallback ({schema_path}): {exc}", file=sys.stderr)
    return dict(DEFAULT_NEURAL_SCHEMA)


def load_env(env_path: Path = ENV_FILE) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            if key:
                env_vars[key] = val
    return env_vars


def _get_setting(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    if val is not None and val != "":
        return val.strip()
    return ENV.get(key, default).strip()


# --- File locking for env file (P0-07: prevent concurrent write races) ---
@contextmanager
def _env_file_lock(env_path: Path) -> Iterator[None]:
    """Exclusive flock on the env file so concurrent writers don't clobber each other."""
    lock_path = env_path.parent / (env_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


# --- Safe coercion helpers (P0-06: never crash at import on bad config) ---
_CONFIG_LOAD_WARNINGS: list[str] = []


def _safe_int(value: Any, default: int, key: str = "") -> int:
    """Coerce to int, falling back to default with a warning on failure."""
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        msg = f"config: invalid int for {key or value!r}: {exc}; using default {default}"
        if msg not in _CONFIG_LOAD_WARNINGS:
            _CONFIG_LOAD_WARNINGS.append(msg)
        return default


def _safe_float(value: Any, default: float, key: str = "") -> float:
    """Coerce to float, falling back to default with a warning on failure."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        msg = f"config: invalid float for {key or value!r}: {exc}; using default {default}"
        if msg not in _CONFIG_LOAD_WARNINGS:
            _CONFIG_LOAD_WARNINGS.append(msg)
        return default


def config_load_warnings() -> list[str]:
    """Return all warnings collected during config load (non-fatal degradations)."""
    return list(_CONFIG_LOAD_WARNINGS)


# --- Load Schema & Environment ---
NEURAL_SCHEMA = load_neural_schema()
ENV = load_env()

_ROUTING = NEURAL_SCHEMA.get("routing", {})
_STT = NEURAL_SCHEMA.get("stt", {})
_PERSONA = NEURAL_SCHEMA.get("persona", {})
_SECURITY = NEURAL_SCHEMA.get("security_policy", {})
COGNITION_CFG: dict[str, Any] = NEURAL_SCHEMA.get("cognition", {})
SWARM_ROLES: dict[str, dict[str, Any]] = NEURAL_SCHEMA.get("swarm_roles", {})
WORLD_MODEL_CFG: dict[str, Any] = NEURAL_SCHEMA.get("world_model", {})
TOOL_DOMAINS: dict[str, dict[str, Any]] = NEURAL_SCHEMA.get("tool_domains", {})
CONFIDENCE_POLICY: dict[str, Any] = NEURAL_SCHEMA.get("confidence_policy", {})
REFLECTION_POLICY: dict[str, Any] = NEURAL_SCHEMA.get("reflection_policy", {})
EDGE_LIGHTING_CFG: dict[str, Any] = NEURAL_SCHEMA.get("edge_lighting", {})
CACHE_CFG: dict[str, Any] = NEURAL_SCHEMA.get("cache", {})
AUTOMATION_CFG: dict[str, Any] = NEURAL_SCHEMA.get("automation", {})
PRIVACY_CFG: dict[str, Any] = NEURAL_SCHEMA.get("privacy", {})
LANGUAGES: dict[str, dict[str, Any]] = NEURAL_SCHEMA.get("languages", {})
WEB_COMMS_CFG: dict[str, Any] = NEURAL_SCHEMA.get("web_comms", {"enabled": False, "allowed_hosts": []})
LIBREOFFICE_CFG: dict[str, Any] = NEURAL_SCHEMA.get("libreoffice", {"uno_port": 2002})
AUTONOMY_CFG: dict[str, Any] = NEURAL_SCHEMA.get("autonomy", {"observe_only": True})


def is_web_comms_enabled() -> bool:
    env_override = os.environ.get("CELINA_WEB_COMMS_ENABLED")
    if env_override is not None and env_override.strip() != "":
        return env_override.strip().lower() in {"1", "true", "yes", "on"}
    return bool(WEB_COMMS_CFG.get("enabled", False))


def get_web_comms_allowed_hosts() -> list[str]:
    hosts = WEB_COMMS_CFG.get("allowed_hosts") or []
    return [str(h).strip().lower().rstrip(".") for h in hosts if str(h).strip()]


def get_libreoffice_uno_port() -> int:
    env_port = os.environ.get("CELINA_LIBREOFFICE_UNO_PORT")
    if env_port and env_port.strip().isdigit():
        return int(env_port.strip())
    try:
        return int(LIBREOFFICE_CFG.get("uno_port", 2002))
    except (TypeError, ValueError):
        return 2002


def is_autonomy_observe_only() -> bool:
    env_override = os.environ.get("CELINA_AUTONOMY_OBSERVE_ONLY")
    if env_override is not None and env_override.strip() != "":
        return env_override.strip().lower() not in {"0", "false", "no", "off"}
    return bool(AUTONOMY_CFG.get("observe_only", True))

# Assistant Identity (default: Celina / nickname: Celly; updatable live anytime!)
ASSISTANT_NAME = _get_setting(
    "CELINA_NAME",
    _get_setting("ASSISTANT_NAME", str(_PERSONA.get("name", "Celina"))),
) or "Celina"
ASSISTANT_NICKNAME = _get_setting(
    "CELINA_NICKNAME",
    _get_setting("ASSISTANT_NICKNAME", str(_PERSONA.get("nickname", "Celly"))),
)

# AI Endpoints & Models (all cloud keys are optional; never print their values)
OPENROUTER_API_KEY = _get_setting("OPENROUTER_API_KEY", "")
GROQ_API_KEY = _get_setting("GROQ_API_KEY", "")
GEMINI_API_KEY = _get_setting("GEMINI_API_KEY", "")
NVIDIA_API_KEY = _get_setting("NVIDIA_API_KEY", "")
MISTRAL_API_KEY = _get_setting("MISTRAL_API_KEY", "")
CEREBRAS_API_KEY = _get_setting("CEREBRAS_API_KEY", "")
XAI_API_KEY = _get_setting("XAI_API_KEY", "")
ZHIPUAI_API_KEY = _get_setting("ZHIPUAI_API_KEY", "")
DEEPSEEK_API_KEY = _get_setting("DEEPSEEK_API_KEY", "")
TOGETHER_API_KEY = _get_setting("TOGETHER_API_KEY", "")
PERPLEXITY_API_KEY = _get_setting("PERPLEXITY_API_KEY", "")
OLLAMA_API_BASE = _get_setting("OLLAMA_API_BASE", "http://localhost:11434").rstrip("/")
NPU_API_BASE = _get_setting("CELINA_NPU_ENDPOINT", "http://127.0.0.1:11435").rstrip("/")
LOCAL_MODEL = _get_setting("CELINA_LOCAL_MODEL", str(_ROUTING.get("local_model", "qwen3:1.7b")))

# ---------------------------------------------------------------------------
# Gear 0.5: smolLM Quantized (Update 26)
# Local sub-second LLM tier between Gear 0 (0 tokens) and Gear 1 (Ollama)
# ---------------------------------------------------------------------------
SMOLLM_MODEL = _get_setting(
    "CELINA_SMOLLM_MODEL",
    str(_ROUTING.get("smolllm_model", "smollm-117m")),
)
SMOLLM_BIN = _get_setting(
    "CELINA_SMOLLM_BIN",
    str(HOME / ".celina" / "models" / "smolLM" / "llama.cpp" / "llama-cli"),
)
SMOLLM_MODEL_PATH = _get_setting(
    "CELINA_SMOLLM_MODEL_PATH",
    str(HOME / ".celina" / "models" / "smolLM" / f"{SMOLLM_MODEL}.gguf"),
)
SMOLLM_NUM_THREADS = _safe_int(
    _get_setting("CELINA_SMOLLM_NUM_THREADS", str(_ROUTING.get("smolllm_num_threads", 2))),
    2, "CELINA_SMOLLM_NUM_THREADS",
)
SMOLLM_NUM_CTX = _safe_int(
    _get_setting("CELINA_SMOLLM_NUM_CTX", str(_ROUTING.get("smolllm_num_ctx", 512))),
    512, "CELINA_SMOLLM_NUM_CTX",
)
SMOLLM_MAX_TOKENS = _safe_int(
    _get_setting("CELINA_SMOLLM_MAX_TOKENS", str(_ROUTING.get("smolllm_max_tokens", 128))),
    128, "CELINA_SMOLLM_MAX_TOKENS",
)
SMOLLM_TEMPERATURE = _safe_float(
    _get_setting("CELINA_SMOLLM_TEMPERATURE", str(_ROUTING.get("smolllm_temperature", 0.3))),
    0.3, "CELINA_SMOLLM_TEMPERATURE",
)


# ---------------------------------------------------------------------------
# Gear 0.5 micro-tier (intent + slot router) config
# ---------------------------------------------------------------------------
GEAR0_5_BACKEND = _get_setting(
    "CELINA_GEAR0_5_BACKEND",
    str(_ROUTING.get("gear0_5_backend", "auto")),
).lower()
# auto → try fasttext first, then smollm, then fall back to smolLM direct generation
GEAR0_5_MIN_CONFIDENCE = _safe_float(
    _get_setting("CELINA_GEAR0_5_MIN_CONFIDENCE", str(_ROUTING.get("gear0_5_min_confidence", 0.75))),
    0.75, "CELINA_GEAR0_5_MIN_CONFIDENCE",
)
GEAR0_5_MODEL_PATH = _get_setting(
    "CELINA_GEAR0_5_MODEL_PATH",
    str(_ROUTING.get("gear0_5_model_path", "")),
)


def get_smolLM_status() -> dict[str, Any]:
    """Return smolLM tier status (Update 26)."""
    try:
        from celina_pkg.gear0_5_smolLM import gear0_5_smolLM_status

        return gear0_5_smolLM_status()  # type: ignore[no-any-return]  # external function returns Any; validated at call site
    except Exception as exc:  # pragma: no cover - import guard
        return {"available": False, "error": str(exc)}


def get_active_local_inference_route(prefer_npu: bool = True) -> dict[str, Any]:
    """Resolve active local inference backend (decoupled NPU 11435 -> Ollama 11434 fallback)."""
    try:
        from celina_hardware.compute import (
            detect_compute_topology,
            resolve_inference_endpoint,
        )

        topo = detect_compute_topology(npu_endpoint=NPU_API_BASE, ollama_endpoint=OLLAMA_API_BASE)
        return resolve_inference_endpoint(prefer_npu=prefer_npu, topology=topo)  # type: ignore[no-any-return]  # hardware function returns Any; schema validated at call site
    except Exception:
        return {
            "endpoint": OLLAMA_API_BASE,
            "backend": "ollama",
            "fallback_used": False,
            "service": "ollama.service",
        }
OLLAMA_NUM_CTX = _safe_int(_get_setting("CELINA_OLLAMA_NUM_CTX", str(_ROUTING.get("ollama_num_ctx", 4096))), 4096, "CELINA_OLLAMA_NUM_CTX")
OLLAMA_KEEP_ALIVE = _get_setting("CELINA_OLLAMA_KEEP_ALIVE", str(_ROUTING.get("ollama_keep_alive", "45s")))
LITE_MODE = _get_setting("CELINA_LITE_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}


def is_lite_mode() -> bool:
    """Returns True when Zero-Lag 8 GB Turbo/Lite Mode is enabled (env or persisted config)."""
    env_val = os.environ.get("CELINA_LITE_MODE")
    if env_val is not None:
        return env_val.strip().lower() in {"1", "true", "yes", "on"}
    if "CELINA_LITE_MODE" in ENV:
        return str(ENV["CELINA_LITE_MODE"]).strip().lower() in {"1", "true", "yes", "on"}
    return bool(LITE_MODE)
OLLAMA_NUM_THREADS = _safe_int(_get_setting("CELINA_OLLAMA_NUM_THREADS", str(_ROUTING.get("ollama_num_threads", 0))), 0, "CELINA_OLLAMA_NUM_THREADS")
CLOUD_MODEL = _get_setting("CELINA_CLOUD_MODEL", str(_ROUTING.get("cloud_model", "openrouter/free")))
VISION_MODEL = _get_setting("CELINA_VISION_MODEL", str(_ROUTING.get("vision_model", CLOUD_MODEL)))

# Apple Intelligence Edge Lighting
EDGE_LIGHTING_ENABLED = _get_setting(
    "CELINA_EDGE_LIGHTING", "1" if EDGE_LIGHTING_CFG.get("enabled", True) else "0",
).lower() in {"1", "true", "yes", "on"}
FULLSCREEN_GLOW_ON_TASK = EDGE_LIGHTING_ENABLED and bool(EDGE_LIGHTING_CFG.get("fullscreen_on_task", True))
ISLAND_GLOW_ON_RESPONSE = EDGE_LIGHTING_ENABLED and bool(EDGE_LIGHTING_CFG.get("island_on_response", True))

# Smart Cache Settings
CACHE_ENABLED = _get_setting(
    "CELINA_CACHE_ENABLED", "1" if CACHE_CFG.get("enabled", True) else "0",
).lower() in {"1", "true", "yes", "on"}
CACHE_OFFLOAD_INTERVAL_SEC = _safe_int(CACHE_CFG.get("disk_offload_interval_sec", 600), 600, "cache.disk_offload_interval_sec")

# Automation & OCR Settings
OCR_LANGUAGES = _get_setting("CELINA_OCR_LANGUAGES", str(AUTOMATION_CFG.get("ocr_languages", "eng+tel+hin")))
YDOTOOL_SOCKET = _get_setting("YDOTOOL_SOCKET", DEFAULT_YDOTOOL_SOCKET)
MAX_WORKFLOW_STEPS = _safe_int(AUTOMATION_CFG.get("max_workflow_steps", 25), 25, "automation.max_workflow_steps")
DEFAULT_STEP_DELAY = _safe_float(AUTOMATION_CFG.get("default_step_delay_sec", 0.4), 0.4, "automation.default_step_delay_sec")

# Privacy Firewall Settings
STRICT_PRIVACY = _get_setting(
    "CELINA_STRICT_PRIVACY", "1" if PRIVACY_CFG.get("strict_mode", True) else "0",
).lower() in {"1", "true", "yes", "on"}
HARD_BLOCKED_PATHS: set[str] = set(PRIVACY_CFG.get("hard_blocked_paths", []))
LOCAL_SENSITIVE_TOOLS: set[str] = set(PRIVACY_CFG.get("local_sensitive_tools", []))


def is_hard_blocked_path(path: Path | str) -> bool:
    """Returns True if `path` touches any secret/credential path in HARD_BLOCKED_PATHS."""
    try:
        p = Path(path).expanduser()
        parts = set(p.parts) | set(p.resolve().parts)
    except OSError:
        p = Path(str(path))
        parts = set(p.parts)
    p_str = str(p)
    for blocked in HARD_BLOCKED_PATHS:
        b = str(blocked).strip()
        if not b:
            continue
        if b in parts or p.name == b:
            return True
        if "/" in b:
            try:
                bp = str(Path(b).expanduser().resolve())
                if p_str == bp or p_str.startswith(bp + os.sep):
                    return True
            except OSError:
                pass
    return False

# Multilingual & Voice Settings
DEFAULT_LANGUAGE = _get_setting(
    "CELINA_LANGUAGE", str(_PERSONA.get("default_language", "auto")),
).lower()
SUPPORTED_LANGUAGES: list[str] = list(_PERSONA.get("supported_languages", ["en", "te", "hi"]))

SPEAK_ENABLED = _get_setting("CELINA_SPEAK", "1").lower() in {"1", "true", "yes", "on"}
VOICE_MODEL = _get_setting(
    "CELINA_VOICE_EN",
    _get_setting(
        "CELINA_VOICE_MODEL",
        str(LANGUAGES.get("en", {}).get("piper_voice", "en_US-amy-medium")),
    ),
)
WHISPER_MODEL = _get_setting("CELINA_WHISPER_MODEL", str(_STT.get("whisper_model", "base")))
WHISPER_IDLE_OFFLOAD_SEC = _safe_float(
    _get_setting("CELINA_WHISPER_IDLE_OFFLOAD", str(_STT.get("whisper_idle_offload_seconds", 180))),
    180.0, "CELINA_WHISPER_IDLE_OFFLOAD",
)
WAKE_WORD_ENABLED = _get_setting("CELINA_WAKE_WORD", "0").lower() in {"1", "true", "yes", "on"}
WAKE_WORD_MODEL = _get_setting("CELINA_WAKE_MODEL", "")

# STT Timing & Voice Pipeline (Silero VAD v5 ONNX, PipeWire Audio Ducking, Optional Kokoro-82M)
STT_SILENCE_SECONDS = _safe_float(_STT.get("silence_stop_seconds", 1.2), 1.2, "stt.silence_stop_seconds")
STT_START_TIMEOUT = _safe_float(_STT.get("start_timeout_seconds", 5.0), 5.0, "stt.start_timeout_seconds")
STT_MAX_DURATION = _safe_float(_STT.get("max_duration_seconds", 15.0), 15.0, "stt.max_duration_seconds")
VAD_ENGINE = _get_setting("CELINA_VAD_ENGINE", str(_STT.get("vad_engine", "auto"))).lower()
SILERO_VAD_MODEL = _get_setting("CELINA_SILERO_VAD_MODEL", str(VOICES_DIR / "silero_vad.onnx"))
SILERO_VAD_THRESHOLD = _safe_float(_get_setting("CELINA_SILERO_VAD_THRESHOLD", str(_STT.get("silero_vad_threshold", 0.50))), 0.50, "CELINA_SILERO_VAD_THRESHOLD")
AUDIO_DUCKING_ENABLED = _get_setting(
    "CELINA_AUDIO_DUCKING", "1" if _STT.get("audio_ducking", True) else "0",
).lower() in {"1", "true", "yes", "on"}
AUDIO_DUCK_PCT = max(5, min(90, _safe_int(_get_setting("CELINA_AUDIO_DUCK_PCT", str(_STT.get("duck_volume_pct", 25))), 25, "CELINA_AUDIO_DUCK_PCT")))
TTS_ENGINE = _get_setting("CELINA_TTS_ENGINE", str(_STT.get("tts_engine", "auto"))).lower()
KOKORO_VOICE_EN = _get_setting("CELINA_KOKORO_VOICE_EN", str(_STT.get("kokoro_voice_en", "af_bella")))
KOKORO_VOICE_HI = _get_setting("CELINA_KOKORO_VOICE_HI", str(_STT.get("kokoro_voice_hi", "hf_alpha")))
ACTION_GATE_ENABLED = _get_setting(
    "CELINA_ACTION_GATE", "1" if CONFIDENCE_POLICY.get("enable_action_gate", True) else "0",
).lower() in {"1", "true", "yes", "on"}

# Hybrid Semantic & Embedding Engine (zero-idle-RAM: Ollama embeddinggemma / Model2Vec + SQLite RRF)
EMBEDDINGS_CFG: dict[str, Any] = NEURAL_SCHEMA.get("embeddings", {})
EMBED_ENGINE = _get_setting("CELINA_EMBED_ENGINE", str(EMBEDDINGS_CFG.get("engine", "auto"))).lower()
EMBED_MODEL = _get_setting("CELINA_EMBED_MODEL", str(EMBEDDINGS_CFG.get("model", "embeddinggemma")))
EMBED_MODEL2VEC_MODEL = _get_setting(
    "CELINA_EMBED_MODEL2VEC", str(EMBEDDINGS_CFG.get("model2vec_model", "minishlab/potion-multilingual-128M")),
)
EMBED_DIM = max(32, _safe_int(_get_setting("CELINA_EMBED_DIM", str(EMBEDDINGS_CFG.get("dim", 256))), 256, "CELINA_EMBED_DIM"))
EMBED_KEEP_ALIVE = _get_setting("CELINA_EMBED_KEEP_ALIVE", str(EMBEDDINGS_CFG.get("keep_alive", "30s")))
EMBED_RRF_K = max(1, _safe_int(_get_setting("CELINA_EMBED_RRF_K", str(EMBEDDINGS_CFG.get("rrf_k", 60))), 60, "CELINA_EMBED_RRF_K"))
SEMANTIC_CACHE_ENABLED = _get_setting(
    "CELINA_SEMANTIC_CACHE", "1" if EMBEDDINGS_CFG.get("semantic_cache_enabled", True) else "0",
).lower() in {"1", "true", "yes", "on"}
SEMANTIC_CACHE_THRESHOLD = _safe_float(
    _get_setting("CELINA_SEMANTIC_CACHE_THRESHOLD", str(EMBEDDINGS_CFG.get("semantic_cache_threshold", 0.92))),
    0.92, "CELINA_SEMANTIC_CACHE_THRESHOLD",
)
TOOL_ROUTER_SEMANTIC_MIN_SIM = _safe_float(
    _get_setting("CELINA_TOOL_SEMANTIC_MIN_SIM", str(EMBEDDINGS_CFG.get("tool_router_min_sim", 0.38))),
    0.38, "CELINA_TOOL_SEMANTIC_MIN_SIM",
)

# Security Policy from Neural Schema
MAX_FILE_BACKUPS = _safe_int(_SECURITY.get("max_file_backups", 20), 20, "security.max_file_backups")
TIER1_AUTO_TOOLS: set[str] = set(_SECURITY.get("tier1_auto_tools", []))
TIER2_APPROVAL_TOOLS: set[str] = set(_SECURITY.get("tier2_approval_tools", []))
PROTECTED_PATHS: set[str] = set(_SECURITY.get("protected_paths", [])) | HARD_BLOCKED_PATHS

# Performance & Agent Limits
MEMORY_HISTORY_LIMIT = 10
MAX_AGENT_STEPS = _safe_int(_ROUTING.get("max_agent_steps", 5), 5, "routing.max_agent_steps")
REQUEST_TIMEOUT = _safe_int(_get_setting("CELINA_REQUEST_TIMEOUT", str(_ROUTING.get("request_timeout_sec", 20))), 20, "CELINA_REQUEST_TIMEOUT")
LOCAL_TIMEOUT = _safe_int(_get_setting("CELINA_LOCAL_TIMEOUT", str(_ROUTING.get("local_timeout_sec", 180))), 180, "CELINA_LOCAL_TIMEOUT")
LOCAL_THINK_ENABLED = _get_setting("CELINA_LOCAL_THINK", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_assistant_name() -> str:
    """Returns the live configured assistant primary name (default: 'Celina')."""
    return (ASSISTANT_NAME or "Celina").strip()


def get_assistant_nickname() -> str:
    """Returns the live configured assistant nickname (default: 'Celly')."""
    return (ASSISTANT_NICKNAME or "").strip()


def get_assistant_display_label() -> str:
    """Returns 'Celina (Celly)' when nickname differs from name, else just the primary name."""
    name = get_assistant_name()
    nick = get_assistant_nickname()
    if nick and nick.lower() != name.lower():
        return f"{name} ({nick})"
    return name


def format_persona_instructions() -> str:
    """Renders persona.system_instructions with live {name} and {nickname} placeholders."""
    name = get_assistant_name()
    nick = get_assistant_nickname() or name
    raw = str(
        NEURAL_SCHEMA.get("persona", {}).get(
            "system_instructions",
            DEFAULT_NEURAL_SCHEMA["persona"]["system_instructions"],
        ),
    )
    return raw.replace("{name}", name).replace("{nickname}", nick)


def _sanitize_identity_token(val: str, max_len: int = 32) -> str:
    cleaned = re.sub(r"[\r\n\t\"'`;$\\]+", "", (val or "").strip())
    return cleaned[:max_len].strip()


def set_assistant_identity(
    name: str,
    nickname: str | None = None,
    schema_path: Path | None = None,
    env_path: Path | None = None,
) -> tuple[str, str]:
    """Atomically updates assistant primary name & nickname with fsync + 0600 permissions.
    On any OSError during persistence, restores previous in-memory values and raises
    PersistenceError so callers NEVER falsely claim 'Saved persistently'.
    """
    global ASSISTANT_NAME, ASSISTANT_NICKNAME

    clean_name = _sanitize_identity_token(name)
    if not clean_name:
        raise ValueError("Assistant name cannot be empty.")

    if nickname is None:
        clean_nick = get_assistant_nickname()
    else:
        clean_nick = _sanitize_identity_token(nickname)

    # Snapshot prior in-memory state for strict rollback on persistence failure
    prev_name = ASSISTANT_NAME
    prev_nick = ASSISTANT_NICKNAME
    prev_schema_name = NEURAL_SCHEMA.get("persona", {}).get("name")
    prev_schema_nick = NEURAL_SCHEMA.get("persona", {}).get("nickname")
    prev_env_name = ENV.get("CELINA_NAME")
    prev_env_nick = ENV.get("CELINA_NICKNAME")
    prev_os_env = {k: os.environ.get(k) for k in ("CELINA_NAME", "ASSISTANT_NAME", "CELINA_NICKNAME", "ASSISTANT_NICKNAME")}

    def _restore_memory() -> None:
        global ASSISTANT_NAME, ASSISTANT_NICKNAME
        ASSISTANT_NAME = prev_name
        ASSISTANT_NICKNAME = prev_nick
        NEURAL_SCHEMA.setdefault("persona", {})["name"] = prev_schema_name
        NEURAL_SCHEMA["persona"]["nickname"] = prev_schema_nick
        if prev_env_name is None:
            ENV.pop("CELINA_NAME", None)
        else:
            ENV["CELINA_NAME"] = prev_env_name
        if prev_env_nick is None:
            ENV.pop("CELINA_NICKNAME", None)
        else:
            ENV["CELINA_NICKNAME"] = prev_env_nick
        for k, v in prev_os_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # 1. Atomically persist to neural-schema.json (fsync + 0600)
    s_path = schema_path or SCHEMA_FILE
    try:
        existing: dict[str, Any] = {}
        if s_path.exists():
            parsed = json.loads(s_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                existing = parsed
        existing.setdefault("persona", {})
        existing["persona"]["name"] = clean_name
        existing["persona"]["nickname"] = clean_nick
        atomic_write_private_text(s_path, json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
    except (OSError, ValueError) as exc:
        _restore_memory()
        raise PersistenceError(f"Failed to persist identity to {s_path}: {exc}") from exc

    # 2. Keep ~/.config/celina/env synchronized atomically when present
    e_path = env_path or ENV_FILE
    if e_path.exists():
        try:
            lines = e_path.read_text(encoding="utf-8").splitlines()
            new_lines: list[str] = []
            saw_name = False
            saw_nick = False
            for ln in lines:
                stripped = ln.strip()
                if stripped.startswith("CELINA_NAME="):
                    new_lines.append(f'CELINA_NAME="{clean_name}"')
                    saw_name = True
                elif stripped.startswith("CELINA_NICKNAME="):
                    new_lines.append(f'CELINA_NICKNAME="{clean_nick}"')
                    saw_nick = True
                else:
                    new_lines.append(ln)
            if not saw_name:
                new_lines.append(f'CELINA_NAME="{clean_name}"')
            if not saw_nick:
                new_lines.append(f'CELINA_NICKNAME="{clean_nick}"')
            atomic_write_private_text(e_path, "\n".join(new_lines) + "\n")
        except OSError as exc:
            _restore_memory()
            raise PersistenceError(f"Failed to persist identity to {e_path}: {exc}") from exc

    # 3. Commit in-memory state ONLY after durable disk writes succeeded
    ASSISTANT_NAME = clean_name
    ASSISTANT_NICKNAME = clean_nick
    NEURAL_SCHEMA.setdefault("persona", {})["name"] = clean_name
    NEURAL_SCHEMA["persona"]["nickname"] = clean_nick
    ENV["CELINA_NAME"] = clean_name
    ENV["CELINA_NICKNAME"] = clean_nick
    if "CELINA_NAME" in os.environ:
        os.environ["CELINA_NAME"] = clean_name
    if "ASSISTANT_NAME" in os.environ:
        os.environ["ASSISTANT_NAME"] = clean_name
    if "CELINA_NICKNAME" in os.environ:
        os.environ["CELINA_NICKNAME"] = clean_nick
    if "ASSISTANT_NICKNAME" in os.environ:
        os.environ["ASSISTANT_NICKNAME"] = clean_nick

    return clean_name, clean_nick


def get_voice_for_lang(lang_code: str) -> str:
    code = (lang_code or "en").lower()
    env_key = f"CELINA_VOICE_{code.upper()}"
    override = _get_setting(env_key, "")
    if override:
        return override
    lang_cfg = LANGUAGES.get(code) or LANGUAGES.get("en") or {}
    return str(lang_cfg.get("piper_voice", VOICE_MODEL))


def get_espeak_for_lang(lang_code: str) -> str:
    code = (lang_code or "en").lower()
    lang_cfg = LANGUAGES.get(code) or LANGUAGES.get("en") or {}
    return str(lang_cfg.get("espeak_voice", "en-us+f3"))


# --- Hardware-Aware Dense & Sparse MoE Local Model Catalog (Ollama / llama.cpp C engine) ---
LOCAL_MODEL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "tag": "qwen3:1.7b",
        "arch": "Dense",
        "total_params": "1.7B",
        "active_params": "1.7B",
        "size_gb": 1.4,
        "min_ram_gb": 6,
        "note": "Best 8GB RAM zero-lag default (~1.8GB live RAM; leaves 3+ GB for browser/apps)",
    },
    {
        "tag": "qwen2.5:3b",
        "arch": "Dense",
        "total_params": "3B",
        "active_params": "3B",
        "size_gb": 1.9,
        "min_ram_gb": 8,
        "note": "Smarter 8GB RAM everyday tool-calling brain (~2.2GB live RAM; fast & reliable offline)",
    },
    {
        "tag": "granite3.1-moe:1b",
        "arch": "Sparse MoE",
        "total_params": "1B",
        "active_params": "0.4B",
        "size_gb": 1.4,
        "min_ram_gb": 6,
        "note": "Featherweight Sparse MoE (only 400M params fire/token — lowest CPU & RAM)",
    },
    {
        "tag": "granite3.1-moe:3b",
        "arch": "Sparse MoE",
        "total_params": "3B",
        "active_params": "0.8B",
        "size_gb": 2.0,
        "min_ram_gb": 8,
        "note": "Fast 8GB Sparse MoE (only 800M params fire/token; ~2.4GB live RAM)",
    },
    {
        "tag": "qwen3:4b",
        "arch": "Dense",
        "total_params": "4B",
        "active_params": "4B",
        "size_gb": 2.5,
        "min_ram_gb": 11,
        "note": "Stronger 4B (~3.4GB live RAM; best on 12GB+ RAM or 8GB with browser closed)",
    },
    {
        "tag": "qwen3.5:4b",
        "arch": "Dense Vision",
        "total_params": "4B",
        "active_params": "4B",
        "size_gb": 3.5,
        "min_ram_gb": 8,
        "note": "Multimodal text + image vision on 8GB RAM",
    },
    {
        "tag": "qwen3.5:9b",
        "arch": "Dense Vision",
        "total_params": "9B",
        "active_params": "9B",
        "size_gb": 6.6,
        "min_ram_gb": 14,
        "note": "Strong 16GB RAM daily driver (text + vision)",
    },
    {
        "tag": "gpt-oss:20b",
        "arch": "Sparse MoE",
        "total_params": "21B",
        "active_params": "3.6B",
        "size_gb": 14.0,
        "min_ram_gb": 18,
        "note": "OpenAI open-weight MoE (21B total, ~3.6B fire/token; tight on 16GB+ZRAM)",
    },
    {
        "tag": "gemma4:26b",
        "arch": "Sparse MoE Vision",
        "total_params": "25.2B",
        "active_params": "3.8B",
        "size_gb": 17.0,
        "min_ram_gb": 22,
        "note": "8 of 128 experts fire/token (3.8B speed, 26B brains + vision)",
    },
    {
        "tag": "qwen3:30b-a3b",
        "arch": "Sparse MoE",
        "total_params": "30.5B",
        "active_params": "3.3B",
        "size_gb": 19.0,
        "min_ram_gb": 24,
        "note": "30B MoE at 3.3B CPU speed (fast in 24-32GB RAM; NVMe mmap swap if less)",
    },
    {
        "tag": "qwen3.5:35b-a3b",
        "arch": "Sparse MoE Vision",
        "total_params": "34.7B",
        "active_params": "3B",
        "size_gb": 22.0,
        "min_ram_gb": 28,
        "note": "35B multimodal MoE firing ~3B/token (32GB+ RAM recommended)",
    },
)

_MODEL_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}$")


def detect_system_ram_gb() -> int:
    """Returns approximate physical RAM in GiB from /proc/meminfo."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return max(1, (kb + 524288) // 1048576)
    except (OSError, ValueError):
        return 8
    return 8


def detect_available_ram_mb() -> int:
    """Returns live MemAvailable in MiB from /proc/meminfo for dynamic RAM-pressure guards."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return max(0, kb // 1024)
    except (OSError, ValueError):
        return 2048
    return 2048


def lookup_catalog_model(tag: str) -> dict[str, Any] | None:
    low = (tag or "").strip().lower()
    for item in LOCAL_MODEL_CATALOG:
        if item["tag"].lower() == low:
            return item
    return None


def format_model_catalog(ram_gb: int | None = None) -> str:
    """Formats RAM-matched Dense & Sparse MoE model guidance for `/model`."""
    ram = ram_gb if ram_gb is not None else detect_system_ram_gb()
    lines = [
        f"Active local Ollama model: {LOCAL_MODEL} (detected RAM: ~{ram} GB)",
        "Curated Dense & Sparse MoE models (Ollama runs llama.cpp C/C++ with NVMe mmap):",
    ]
    for m in LOCAL_MODEL_CATALOG:
        fit = "IN-RAM FAST" if ram >= int(m["min_ram_gb"]) else "NVMe mmap (slower swap)"
        lines.append(
            f"  • {m['tag']:<19} | {m['arch']:<17} | {m['total_params']} ({m['active_params']} active) "
            f"| ~{m['size_gb']:.1f}GB | [{fit}] — {m['note']}",
        )
    lines.append("Switch anytime: /model <tag>   (then download once: ollama pull <tag>)")
    return "\n".join(lines)


def set_local_model(
    model_tag: str,
    env_path: Path | None = None,
    ram_gb: int | None = None,
) -> tuple[str, str | None]:
    """Validates and atomically persists CELINA_LOCAL_MODEL (0600) while updating live config.LOCAL_MODEL.
    Returns (clean_tag, optional_ram_warning).
    """
    global LOCAL_MODEL

    clean = (model_tag or "").strip()
    if not _MODEL_TAG_RE.fullmatch(clean):
        raise ValueError(f"Invalid Ollama model tag: '{model_tag}'")

    e_path = env_path or ENV_FILE
    # P0-07: exclusive lock around read-modify-write to prevent lost updates
    with _env_file_lock(e_path):
        if e_path.exists():
            lines = e_path.read_text(encoding="utf-8", errors="replace").splitlines()
            out: list[str] = []
            found = False
            for ln in lines:
                if ln.strip().startswith("CELINA_LOCAL_MODEL="):
                    out.append(f'CELINA_LOCAL_MODEL="{clean}"')
                    found = True
                else:
                    out.append(ln)
            if not found:
                out.append(f'CELINA_LOCAL_MODEL="{clean}"')
            atomic_write_private_text(e_path, "\n".join(out) + "\n")
        elif env_path is not None:
            atomic_write_private_text(e_path, f'CELINA_LOCAL_MODEL="{clean}"\n')

    LOCAL_MODEL = clean
    ENV["CELINA_LOCAL_MODEL"] = clean
    if "CELINA_LOCAL_MODEL" in os.environ:
        os.environ["CELINA_LOCAL_MODEL"] = clean

    warn: str | None = None
    ram = ram_gb if ram_gb is not None else detect_system_ram_gb()
    meta = lookup_catalog_model(clean)
    if meta is not None and ram < int(meta["min_ram_gb"]):
        warn = (
            f"Note: {clean} needs ~{meta['size_gb']:.1f} GB (~{meta['min_ram_gb']} GB RAM recommended) "
            f"and this PC has ~{ram} GB RAM. Ollama/llama.cpp will stream experts via NVMe mmap "
            "(expect slower ~1-4 tok/s instead of in-RAM speed)."
        )
    return clean, warn


def set_env_pairs(
    pairs: dict[str, str],
    env_path: Path | None = None,
) -> None:
    """Atomically persists key=value pairs into ~/.config/celina/env (mode 0600)
    and synchronizes live in-memory config flags (LITE_MODE, OLLAMA_KEEP_ALIVE).
    """
    global LITE_MODE, OLLAMA_KEEP_ALIVE

    clean_pairs: dict[str, str] = {}
    for k, v in pairs.items():
        ck = re.sub(r"[^A-Za-z0-9_]", "", str(k or "").strip())
        if not ck:
            continue
        cv = re.sub(r"[\r\n\"'`;$]", "", str(v if v is not None else "").strip())
        clean_pairs[ck] = cv

    if not clean_pairs:
        return

    e_path = env_path or ENV_FILE
    # P0-07: exclusive lock around read-modify-write to prevent lost updates
    with _env_file_lock(e_path):
        lines = e_path.read_text(encoding="utf-8", errors="replace").splitlines() if e_path.exists() else []
        seen: set[str] = set()
        out: list[str] = []
        for ln in lines:
            stripped = ln.strip()
            replaced = False
            for ck, cv in clean_pairs.items():
                if stripped.startswith(f"{ck}="):
                    out.append(f'{ck}="{cv}"')
                    seen.add(ck)
                    replaced = True
                    break
            if not replaced:
                out.append(ln)
        for ck, cv in clean_pairs.items():
            if ck not in seen:
                out.append(f'{ck}="{cv}"')

        e_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_private_text(e_path, "\n".join(out) + "\n")

    for ck, cv in clean_pairs.items():
        ENV[ck] = cv
        if ck in os.environ:
            os.environ[ck] = cv
    if "CELINA_LITE_MODE" in clean_pairs:
        LITE_MODE = clean_pairs["CELINA_LITE_MODE"].lower() in {"1", "true", "yes", "on"}
    if "CELINA_OLLAMA_KEEP_ALIVE" in clean_pairs:
        OLLAMA_KEEP_ALIVE = clean_pairs["CELINA_OLLAMA_KEEP_ALIVE"]


# ---------------------------------------------------------------------------
# Unified Model Selection Integration (Update 27)
# ---------------------------------------------------------------------------


def get_recommended_whisper_model(
    prefer_language: str | None = None,
    prefer_accuracy: str | None = None,
) -> dict[str, Any]:
    """Return the recommended Whisper STT model for this system.

    Uses model_selector.select_whisper_model() with detected RAM.
    Env var CELINA_WHISPER_MODEL takes priority if set.
    """
    return model_selector.select_whisper_model(  # type: ignore[no-any-return]  # model_selector returns Any; schema validated at call site
        prefer_language=prefer_language,
        prefer_accuracy=prefer_accuracy,
    )


def get_recommended_ollama_model(
    prefer_vision: bool = False,
    prefer_heavy: bool = False,
) -> dict[str, Any]:
    """Return the recommended Ollama LLM model for this system.

    Uses model_selector.select_ollama_model() with detected RAM, GPU, NPU.
    Env var CELINA_LOCAL_MODEL takes priority if set.
    """
    return model_selector.select_ollama_model(  # type: ignore[no-any-return]  # model_selector returns Any; schema validated at call site
        prefer_vision=prefer_vision,
        prefer_heavy=prefer_heavy,
    )


def get_recommended_edge0_model() -> dict[str, Any]:
    """Return the recommended Edge0 GGUF model for this system.

    Uses model_selector.select_edge0_model() with detected RAM and disk.
    Env var CELINA_EDGE0_MODEL takes priority if set.
    """
    return model_selector.select_edge0_model()  # type: ignore[no-any-return]  # model_selector returns Any; schema validated at call site


def get_full_model_decision(
    goal: str | None = None,
    configured_providers: list[str] | None = None,
    prefer_language: str | None = None,
    prefer_vision: bool = False,
    prefer_heavy: bool = False,
    prefer_parallel: bool = False,
) -> model_selector.FullModelDecision:
    """Return the complete model decision for all tiers.

    This is the single entry point for all model selection decisions:
    Whisper, Ollama, Edge0, Gear 0.5 smolLM, and cloud routing.
    """
    return model_selector.make_full_model_decision(
        goal=goal,
        configured_providers=configured_providers,
        prefer_language=prefer_language,
        prefer_vision=prefer_vision,
        prefer_heavy=prefer_heavy,
        prefer_parallel=prefer_parallel,
    )


def format_model_decision_summary(
    decision: model_selector.FullModelDecision | None = None,
) -> str:
    """Return a human-readable summary of the full model decision.

    If no decision is passed, computes one from current system resources.
    """
    if decision is None:
        decision = model_selector.make_full_model_decision()
    return model_selector.format_decision_summary(decision)  # type: ignore[no-any-return]  # model_selector returns Any; schema validated at call site


def apply_recommended_models(
    *,
    apply_whisper: bool = False,
    apply_ollama: bool = False,
    env_path: Path | None = None,
) -> dict[str, str]:
    """Apply recommended models by writing them to ~/.config/celina/env.

    Does NOT download models — only persists the model choice so that
    the installer / stages can perform the actual `ollama pull` /
    `pip install faster-whisper` / Edge0 download.

    Returns dict of what was applied.
    """
    applied: dict[str, str] = {}
    e_path = env_path or ENV_FILE

    if apply_whisper:
        rec = get_recommended_whisper_model()
        model_name = rec["model"]
        set_env_pairs({"CELINA_WHISPER_MODEL": model_name}, env_path=e_path)
        applied["whisper"] = model_name

    if apply_ollama:
        rec = get_recommended_ollama_model()
        model_tag = rec["model"]
        set_local_model(model_tag, env_path=e_path)
        applied["ollama"] = model_tag

    return applied

