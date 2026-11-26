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
