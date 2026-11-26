"""
Celina AI — Tamper-Evident Hash-Chained Agent Evidence Ledger (v4.4)

Separate append-only audit trail (`~/.local/share/celina/evidence/ledger.jsonl`,
mode `0600` inside `0700` dir — independent of SQLite memory/cache DBs).
Every Tier-2 tool execution appends a SHA-256 hash-chained record linking:
  timestamp_utc, tool, arguments_hash, undo_action_id, outcome, redacted result,
  /why reasoning summary, OS capability profile, prev_hash -> entry_hash.

Verify integrity anytime:   celina evidence verify
Export verifiable audit:    celina evidence export [output.jsonl]
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import approval, capabilities, config, privacy

GENESIS_HASH = "0" * 64
_UNDO_ID_RE = re.compile(r"\[undo:\s*(act_[0-9_a-f]+)\]")

_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # ignore/disregard/forget/override instructions (incl. 4-word variant)
    (
        "ignore_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass|cancel|stop)\s+(all\s+)?(?:the\s+)?(?:previous|prior)?\s*(instructions|rules|guidelines|system|prompt|safety)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ignore_your_training",
        re.compile(r"\bignore\s+your\s+(safety|training|guidelines)\b", re.IGNORECASE),
    ),
    (
        "forget_safety",
        re.compile(r"\bforget\s+(all\s+)?(safety|training)\b", re.IGNORECASE),
    ),
    # Persona / role change
    (
        "persona_change",
        re.compile(
            r"\b(you\s+are\s+now|you\s+must|from\s+now\s+on|act\s+as"
            r"|pretend\s+to\s+be|roleplay\s+as)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "unrestricted_mode",
        re.compile(r"\b(unrestricted|root|developer)\s+mode\b", re.IGNORECASE),
    ),
    (
        "dan_jailbreak",
        re.compile(r"\bDAN\b|\bdo\s+anything\s+now\b", re.IGNORECASE),
    ),
    # Role prefix / system label
    (
        "role_prefix",
        re.compile(r"\b(system|developer|assistant|user)\s*:\s*", re.IGNORECASE),
    ),
    (
        "bracket_system",
        re.compile(r"\[(SYSTEM|system|CELINA|celina)\s*\]", re.IGNORECASE),
    ),
    # Jailbreak / safety override
    (
        "jailbreak_term",
        re.compile(
            r"\b(jailbreak|prompt\s+injection|override\s+safety|disable\s+safety|disable_?safety|disable_?approval)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "bypass_approval",
        re.compile(
            r"\b(override|bypass)\s+(all\s+)?"
            r"(approval|safety|gate|guardrail|confirmation|required)\b",
            re.IGNORECASE,
        ),
    ),
    # Exfiltration / repetition
    (
        "exfiltration",
        re.compile(
            r"\b(repeat\s+above|repeat\s+the\s+above|verbatim\s+above|print\s+above|print\s+your\s+system\s+prompt)\b",
            re.IGNORECASE,
        ),
    ),
    # CELINA_ directives
    (
        "celina_directive",
        re.compile(r"\bCELINA_[A-Z_]+\b", re.IGNORECASE),
    ),
    # Tool names in hostile context
    (
        "tool_call_in_text",
        re.compile(
            r"\b(write_file|edit_file|move_file|trash_file|run_shell_command|code_sandbox|run_python_sandbox)\s*\(",
            re.IGNORECASE,
        ),
    ),
    # HTML/XML comments with directives
    (
        "html_xml_directive",
        re.compile(
            r"<!--\s*(CELINA|system|tool_override|admin_override|celina_directive)|<\s*/?(celina_directive|system|tool_override|admin_override)\s*>",
            re.IGNORECASE,
        ),
    ),
    (
        "short_directive",
        re.compile(r"\b(ignore_safety|disable_approval|approve_all|bypass_guard)\b", re.IGNORECASE),
    ),
    # NEW INSTRUCTION / prefix patterns
    (
        "new_instruction",
        re.compile(r"\bnew\s+instruction\s*:?\s", re.IGNORECASE),
    ),
    (
        "celina_override_dot",
        re.compile(r"\b(window\.)?celina[Oo]verride\b", re.IGNORECASE),
    ),
    (
        "xss_pattern",
        re.compile(
            r"\bdata:text/html\b|\bjavascript:\s*(void\s*\()?|meta\s+http-equiv\s*=",
            re.IGNORECASE,
        ),
    ),
    (
        "file_op_prefix",
        re.compile(
            r"\b(EDIT_FILE|RENAME_FILE|TRASH_FILE|RUN|CHMOD|CHOWN|OVERRIDE|NEW_INSTRUCTION|MODE|RESET)\s*:",
            re.IGNORECASE,
        ),
    ),
    (
        "json_action_field",
        re.compile(
            r'"action"\s*:\s*"(write_file|edit_file|move_file|trash_file|run_shell_command)"',
            re.IGNORECASE,
        ),
    ),
    (
        "credential_exposure",
        re.compile(r"\blogin\s*=\s*\w+\s+password\s*=", re.IGNORECASE),
    ),
    (
        "celina_colon",
        re.compile(r"\bCELINA:\s+", re.IGNORECASE),
    ),
)


def detect_prompt_injection(text: str) -> list[str]:
    """Detects prompt-injection patterns so they are logged as security events rather than normal retrievals."""
    raw = text or ""
    return [label for label, pat in _PROMPT_INJECTION_PATTERNS if pat.search(raw)]


def _compute_entry_hash(record_without_hash: dict[str, Any]) -> str:
    canonical = json.dumps(record_without_hash, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class EvidenceIntegrityError(RuntimeError):
    """Fail-closed exception raised when an anchored evidence ledger is deleted, truncated, or tampered."""


def _default_anchor_for(ledger_path: Path) -> Path:
    """
    Stores the fail-closed anchor outside the ledger's directory:
      - Production ledger (under `config.BASE_DIR`): `~/.config/celina/evidence_anchors/<sha>.anchor`
      - Custom/test ledger outside `config.BASE_DIR`: `<ledger.parent.parent>/.celina_evidence_anchors/<sha>.anchor`
    In both cases the anchor lives in a separate directory from `ledger.jsonl`, so deleting
    the ledger (or its parent dir) still triggers fail-closed `EvidenceIntegrityError` while
    isolated pytest runs never leak `.anchor` files into the host's real `~/.config`.
    """
    resolved_p = ledger_path.resolve()
    key = hashlib.sha256(str(resolved_p).encode("utf-8")).hexdigest()[:16]
    try:
        resolved_p.relative_to(config.BASE_DIR.resolve())
        in_prod_base = True
    except ValueError:
        in_prod_base = False
    if in_prod_base:
        return config.CONFIG_DIR / "evidence_anchors" / f"{key}.anchor"
    return resolved_p.parent.parent / ".celina_evidence_anchors" / f"{key}.anchor"


class EvidenceLedger:
    """Thread-safe, append-only, SHA-256 hash-chained Tier-2 execution ledger with fail-closed anchor."""

    def __init__(
        self,
        ledger_path: Optional[Path] = None,
        *,
        durable_store: Any = None,
        anchor_path: Optional[Path] = None,
        verify_anchor_on_open: bool = True,
    ):
        self.ledger_path = (
            Path(ledger_path) if ledger_path is not None else (config.BASE_DIR / "evidence" / "ledger.jsonl")
        )
        self.anchor_path = (
            Path(anchor_path) if anchor_path is not None else _default_anchor_for(self.ledger_path)
        )
        self._durable_store = durable_store
        self._lock = threading.RLock()
        if verify_anchor_on_open:
            self._verify_anchor_on_open()

    def _read_anchor(self) -> Optional[dict[str, Any]]:
        if not self.anchor_path.is_file():
            return None
        try:
            return json.loads(self.anchor_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]  # json.loads returns Any; anchor schema validated on read
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceIntegrityError(f"EVIDENCE ANCHOR CORRUPT ({self.anchor_path}): {exc}") from exc

    def _write_anchor(self, count: int, tip_hash: str) -> None:
        config.ensure_private_dir(self.anchor_path.parent)
        payload = json.dumps(
            {
                "count": int(count),
                "tip_hash": str(tip_hash),
                "ledger_path": str(self.ledger_path.resolve()),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            sort_keys=True,
        )
        tmp_p = self.anchor_path.with_suffix(".anchor.tmp")
        fd = os.open(str(tmp_p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
        finally:
            try:
                os.chmod(tmp_p, 0o600)
            except OSError:
                pass
        os.replace(tmp_p, self.anchor_path)

    def _verify_anchor_on_open(self) -> None:
        anchor = self._read_anchor()
        if anchor is None:
            if self.ledger_path.is_file():
                try:
                    with self.ledger_path.open("r", encoding="utf-8") as fh:
                        has_records = any(line.strip() for line in fh)
                except OSError as exc:
                    message = f"EVIDENCE LEDGER UNREADABLE: {exc}"
                    raise EvidenceIntegrityError(message) from exc
                if has_records:
                    message = "EVIDENCE ANCHOR MISSING for non-empty ledger"
                    raise EvidenceIntegrityError(message)
            return
        expected_count = int(anchor.get("count") or 0)
        expected_tip = str(anchor.get("tip_hash") or GENESIS_HASH)
        if expected_count <= 0:
            return
        if not self.ledger_path.is_file():
            raise EvidenceIntegrityError(
                f"EVIDENCE LEDGER DELETED ({self.ledger_path}). Anchor shows {expected_count} entries "
                f"(tip={expected_tip[:16]}...). Refusing to start. Restore via celina-import or explicitly "
                f"run `celina evidence reset`."
            )
        actual_seq, actual_tip = self._read_last_link()
        if actual_seq < expected_count:
            raise EvidenceIntegrityError(
                f"EVIDENCE LEDGER TRUNCATED. Anchor count={expected_count}, actual={actual_seq}. Refusing to start."
            )
        if actual_seq == expected_count and actual_tip != expected_tip:
            raise EvidenceIntegrityError(
                f"EVIDENCE LEDGER TAMPERED. Tip hash mismatch (expected {expected_tip[:16]}..., got {actual_tip[:16]}...)."
            )

    def reset_anchor(self, *, confirm: bool = False) -> None:
        """Explicit operator escape hatch (`celina evidence reset`) to clear the fail-closed anchor."""
        if not confirm:
            raise ValueError("Refusing to reset evidence anchor without confirm=True.")
        with self._lock:
            try:
                self.anchor_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _read_last_link(self) -> tuple[int, str]:
        if not self.ledger_path.is_file():
            return 0, GENESIS_HASH
        last_line = ""
        with self.ledger_path.open("r", encoding="utf-8") as fh:
            for ln in fh:
                s = ln.strip()
                if s:
                    last_line = s
        if not last_line:
            return 0, GENESIS_HASH
        obj = json.loads(last_line)
        return int(obj.get("seq", 0)), str(obj.get("entry_hash", GENESIS_HASH))

    def record_execution(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        why_reasoning: str = "",
        undo_action_id: Optional[str] = None,
        tier: str = "tier2",
        root_goal_id: str = "",
        task_id: str = "",
        parent_task_id: str = "",
        trace_id: str = "",
        span_id: Optional[str] = None,
        actor_identity: str = "",
        policy_decision: str = "allow",
        event_type: str = "tool_execution",
        durable_store: Any = None,
    ) -> dict[str, Any]:
        """Appends one hash-chained, intent-linked evidence record with redacted result/reasoning."""
        vault = privacy.RedactionVault()
        redacted_res, _ = vault.redact(str(result_text or "")[:240])
        redacted_why, _ = vault.redact(str(why_reasoning or "")[:320])

        if undo_action_id is None:
            m = _UNDO_ID_RE.search(str(result_text or ""))
            if m:
                undo_action_id = m.group(1)

        low_res = str(result_text or "")
        if low_res.startswith("Denied:") or low_res.startswith("Cancelled"):
            outcome = "denied"
        elif low_res.startswith("Blocked"):
            outcome = "blocked"
        elif low_res.startswith(("Error:", "Tool ", "Failed")):
            outcome = "error"
        else:
            outcome = "ok"

        cap = capabilities.get_tool_capability(tool_name) or {}
        cap_summary = {
            "fs_scope": cap.get("fs_scope", "unknown"),
            "network_egress": cap.get("network_egress", "unknown"),
            "kernel_boundary": cap.get("kernel_boundary", "unknown"),
        }

        with self._lock:
            self._verify_anchor_on_open()
            config.ensure_private_dir(self.ledger_path.parent)
            prev_seq, prev_hash = self._read_last_link()
            body: dict[str, Any] = {
                "seq": prev_seq + 1,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "tool": tool_name,
                "tier": tier,
                "arguments_hash": approval.compute_arguments_hash(tool_name, arguments),
                "undo_action_id": undo_action_id,
                "outcome": outcome,
                "result_preview": redacted_res,
                "why_reasoning": redacted_why,
                "capability_profile": cap_summary,
                "root_goal_id": root_goal_id,
                "task_id": task_id,
                "parent_task_id": parent_task_id,
                "trace_id": trace_id,
                "span_id": span_id or "",
                "actor_identity": actor_identity or f"uid:{os.getuid()}",
                "policy_decision": policy_decision,
                "prev_hash": prev_hash,
            }
            entry_hash = _compute_entry_hash(body)
            record = dict(body)
            record["entry_hash"] = entry_hash

            line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            fd = os.open(str(self.ledger_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
            finally:
                try:
                    os.chmod(self.ledger_path, 0o600)
                except OSError:
                    pass

            self._write_anchor(int(record["seq"]), entry_hash)

            target_store = durable_store if durable_store is not None else self._durable_store
            if target_store is not None and hasattr(target_store, "mirror_evidence_record"):
                try:
                    target_store.mirror_evidence_record(record)
                except Exception:
                    pass
            return record

    # ------------------------------------------------------------------
    # Full observability lifecycle (P2 workstream):
    # predict → approve → execute → verify → calibrate
    # ------------------------------------------------------------------

    def record_prediction(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        predicted_success: float,
        root_goal_id: str = "",
        task_id: str = "",
        trace_id: str = "",
        actor_identity: str = "",
        sim_digest: str = "",
    ) -> dict[str, Any]:
        """Record a tool-outcome prediction at dispatch time (audit item 8).

        The outcome is filled in later by :meth:`record_verification` using
        independent read-after-write verification — never the tool's own
        exit code.
        """
        return self.record_execution(
            tool_name=tool_name,
            arguments=arguments,
            result_text=f"predicted_success={predicted_success}",
            why_reasoning=f"sim_digest={sim_digest[:16]}..." if sim_digest else "",
            tier="tier2",
            root_goal_id=root_goal_id,
            task_id=task_id,
            trace_id=trace_id,
            actor_identity=actor_identity or f"uid:{os.getuid()}",
            policy_decision="predict",
            event_type="prediction",
        )

    def record_approval(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        sim_digest: str,
        decision: str = "allow",
        root_goal_id: str = "",
        task_id: str = "",
        trace_id: str = "",
        actor_identity: str = "",
    ) -> dict[str, Any]:
        """Record an approval decision bound to a sim digest (audit item 2)."""
        return self.record_execution(
            tool_name=tool_name,
            arguments=arguments,
            result_text=f"approval_decision={decision}",
            why_reasoning=f"sim_digest={sim_digest[:16]}...",
            tier="tier2",
            root_goal_id=root_goal_id,
            task_id=task_id,
            trace_id=trace_id,
            actor_identity=actor_identity or f"uid:{os.getuid()}",
            policy_decision=decision,
            event_type="approval",
        )

    def record_verification(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        verified_success: bool,
        verification_method: str = "independent_read_after_write",
        root_goal_id: str = "",
        task_id: str = "",
        trace_id: str = "",
        actor_identity: str = "",
    ) -> dict[str, Any]:
        """Record a verified outcome from independent verification (audit item 8).

        `verified_success` must come from independent read-after-write
        verification (re-stat/hash, systemctl is-active), never from the
        tool's own exit code.
        """
        return self.record_execution(
            tool_name=tool_name,
            arguments=arguments,
            result_text=f"verified_success={verified_success} method={verification_method}",
            why_reasoning="",
            tier="tier2",
            root_goal_id=root_goal_id,
            task_id=task_id,
            trace_id=trace_id,
            actor_identity=actor_identity or f"uid:{os.getuid()}",
            policy_decision="verified",
            event_type="verification",
        )

    def record_calibration(
        self,
        *,
        tool_name: str,
        context_key: str,
        signed_bias: float,
        wilson_lower: float,
        is_unproven: bool,
        root_goal_id: str = "",
        task_id: str = "",
        trace_id: str = "",
        actor_identity: str = "",
    ) -> dict[str, Any]:
        """Record a calibration observation (audit items 6, 7).

        Captures signed bias, Wilson lower bound, and unproven status for
        a tool in a given context (tool × online/offline × gear).
        """
        return self.record_execution(
            tool_name=tool_name,
            arguments={
                "context_key": context_key,
                "signed_bias": signed_bias,
                "wilson_lower": wilson_lower,
                "is_unproven": is_unproven,
            },
            result_text=f"calibration bias={signed_bias} wilson={wilson_lower}",
            why_reasoning=f"unproven={is_unproven}",
            tier="tier2",
            root_goal_id=root_goal_id,
            task_id=task_id,
            trace_id=trace_id,
            actor_identity=actor_identity or f"uid:{os.getuid()}",
            policy_decision="calibrate",
            event_type="calibration",
        )

    def record_security_event(
        self,
        *,
        event_type: str,
        tool_name: str = "security",
        detail: str = "",
        summary: str = "",
        root_goal_id: str = "",
        task_id: str = "",
        parent_task_id: str = "",
        trace_id: str = "",
        span_id: str = "",
        actor_identity: str = "",
        policy_decision: str = "",
        durable_store: Any = None,
    ) -> dict[str, Any]:
        """Captures prompt-injection or boundary violations as explicit security events in the hash chain."""
        msg = detail or summary or event_type
        return self.record_execution(
            tool_name=tool_name,
            arguments={"security_event": event_type},
            result_text=f"Blocked: {msg}",
            why_reasoning=msg,
            tier="security",
            root_goal_id=root_goal_id,
            task_id=task_id,
            parent_task_id=parent_task_id,
            trace_id=trace_id,
            span_id=span_id,
            actor_identity=actor_identity or f"uid:{os.getuid()}",
            policy_decision=policy_decision or f"deny:{event_type}",
            event_type=event_type,
            durable_store=durable_store,
        )

    def list_entries(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            if not self.ledger_path.is_file():
                return []
            out: list[dict[str, Any]] = []
            with self.ledger_path.open("r", encoding="utf-8") as fh:
                for ln in fh:
                    s = ln.strip()
                    if s:
                        out.append(json.loads(s))
            return out[-max(1, int(limit)) :]

    def verify_chain(self) -> tuple[bool, str, int]:  # noqa: PLR0912  # Check missing anchors without weakening chain validation.
        """Verifies sequence continuity, prev_hash linkage, entry_hash, and external fail-closed anchor."""
        with self._lock:
            anchor = None
            try:
                anchor = self._read_anchor()
            except EvidenceIntegrityError as exc:
                return False, str(exc), 0
            if not self.ledger_path.is_file():
                if anchor and int(anchor.get("count") or 0) > 0:
                    return (
                        False,
                        f"Evidence ledger missing/deleted (anchor expects {anchor['count']} records, tip={str(anchor.get('tip_hash',''))[:16]}...).",
                        0,
                    )
                return True, "Evidence ledger is empty (0 records).", 0
            expected_prev = GENESIS_HASH
            expected_seq = 1
            count = 0
            with self.ledger_path.open("r", encoding="utf-8") as fh:
                for lineno, raw in enumerate(fh, 1):
                    s = raw.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                    except json.JSONDecodeError as exc:
                        return False, f"Corrupt JSON at line {lineno}: {exc}", count
                    if obj.get("seq") != expected_seq:
                        return False, f"Sequence gap/reorder at line {lineno}: expected seq={expected_seq}, got {obj.get('seq')}", count
                    if obj.get("prev_hash") != expected_prev:
                        return False, f"Broken hash chain at seq={expected_seq} (prev_hash mismatch)", count
                    claimed = obj.get("entry_hash")
                    body = {k: v for k, v in obj.items() if k != "entry_hash"}
                    recalc = _compute_entry_hash(body)
                    if claimed != recalc:
                        return False, f"Tampered record detected at seq={expected_seq} (entry_hash mismatch)", count
                    expected_prev = recalc
                    expected_seq += 1
                    count += 1
            if anchor is None and count:
                return False, "EVIDENCE ANCHOR MISSING for non-empty ledger", count
            if anchor is not None:
                anc_cnt = int(anchor.get("count") or 0)
                anc_tip = str(anchor.get("tip_hash") or GENESIS_HASH)
                if count < anc_cnt:
                    return False, f"Evidence ledger truncated: anchor expects {anc_cnt} records, found {count}.", count
                if count == anc_cnt and anc_cnt > 0 and expected_prev != anc_tip:
                    return False, f"Evidence ledger tip mismatch against anchor (expected {anc_tip[:16]}..., got {expected_prev[:16]}...).", count
            return True, f"✅ Evidence chain verified ({count} tamper-evident records, tip={expected_prev[:16]}...).", count

    def verify(self) -> tuple[bool, str, int]:
        """Verify the chain and raise on any integrity or anchor failure."""
        ok, message, count = self.verify_chain()
        if not ok:
            raise EvidenceIntegrityError(message)
        return ok, message, count

    def export_evidence(self, output_path: Optional[Path | str] = None) -> dict[str, Any]:
        """Verifies chain integrity first, then exports a self-verifying .jsonl + .sha256 bundle."""
        ok, msg, count = self.verify_chain()
        if not ok:
            raise ValueError(f"Refusing to export corrupted evidence ledger: {msg}")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_p = (
            Path(output_path).expanduser().resolve()
            if output_path is not None
            else (config.BACKUPS_DIR / f"celina-evidence-{stamp}.jsonl").resolve()
        )
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self.ledger_path.is_file():
                shutil.copy2(self.ledger_path, out_p)
            else:
                out_p.write_text("", encoding="utf-8")
            os.chmod(out_p, 0o600)
        digest = _sha256_file(out_p)
        sha_p = Path(f"{out_p}.sha256")
        sha_p.write_text(f"{digest}  {out_p.name}\n", encoding="utf-8")
        os.chmod(sha_p, 0o600)
        return {
            "export_path": str(out_p),
            "sha256_file": str(sha_p),
            "sha256": digest,
            "record_count": count,
            "verification": msg,
        }


_DEFAULT_LEDGER: Optional[EvidenceLedger] = None
_ACTIVE_WHY_CONTEXT = threading.local()


def set_current_why_context(summary: str) -> None:
    _ACTIVE_WHY_CONTEXT.summary = summary


def get_current_why_context() -> str:
    return str(getattr(_ACTIVE_WHY_CONTEXT, "summary", "") or "")


def get_evidence_ledger() -> EvidenceLedger:
    global _DEFAULT_LEDGER
    if _DEFAULT_LEDGER is None:
        _DEFAULT_LEDGER = EvidenceLedger()
    return _DEFAULT_LEDGER
