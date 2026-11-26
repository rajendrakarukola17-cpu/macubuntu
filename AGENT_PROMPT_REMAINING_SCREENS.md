# macubuntu Center — Master Agent Prompt: Remaining 8 Screens

## Context

This prompt instructs an agent to wire the remaining 8 screens of the macubuntu Center GTK4/libadwaita application, connect backend sockets, and add animations. The base installer and 25 install-wizard features are complete (committed). The following screens already exist and are wired:

- `overview` — `_page_overview()`
- `updates` — `_page_updates()`
- `install` — `_page_install()` (with 25 features: offline mode, stage selection, LUKS, user account, ZRAM, snapshots, App Nap, hardware profile, network, storage, software, timezone, locale, accessibility, power, security, backup, updates, services, model hub, API keys, access)
- `finish` — `_page_finish()` (install summary)
- `models` — `_page_models()` (local model hub)
- `apikeys` — `_page_apikeys()` (API keys & cloud)
- `access` — `_page_access()` (access & permissions + GPG keys)
- `reset` — `_page_reset()` (system wipe)

## Remaining 8 Screens to Wire

| Screen ID | Title | Icon | Section | Status |
|-----------|-------|------|---------|--------|
| `performance` | Performance & App Nap | speedometer-symbolic | SYSTEM | NOT WIRED |
| `appearance` | Appearance & Input | preferences-desktop-appearance-symbolic | SYSTEM | NOT WIRED |
| `continuity` | Continuity & Power | battery-good-charging-symbolic | SYSTEM | NOT WIRED |
| `recovery` | Snapshots & Recovery | document-revert-symbolic | SYSTEM | NOT WIRED |
| `knowledge` | Knowledge & Memory | accessories-dictionary-symbolic | CELINA AI & MODELS | NOT WIRED |
| `voice` | Voice & Live Playground | audio-input-microphone-symbolic | CELINA AI & MODELS | NOT WIRED |
| `agents` | Agents & Shortcuts | system-run-symbolic | ACCESS & TRUST | NOT WIRED |
| `security` | 106 Tools & MCP03 | channel-secure-symbolic | ACCESS & TRUST | NOT WIRED |

## Required Changes

### 1. Add `_page_*()` methods for each screen (in `main.py`)

Each page method must:
- Return a `Gtk.ScrolledWindow` wrapping `_wrap_page(title, subtitle, body)`
- Use `Adw.PreferencesGroup` for setting groups
- Follow the existing CSS class conventions (`metric-card`, `pill`, `pill-green`, `pill-amber`, `pill-red`, `dim-label`, `mono`)
- Use `Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)` as the page body
- Include at least one interactive control (switch, entry, button, etc.)

### 2. Register each page in `_populate_pages()`

Add to the existing `_populate_pages()` method:
```python
self.stack.add_named(self._page_performance(), "performance")
self.stack.add_named(self._page_appearance(), "appearance")
self.stack.add_named(self._page_continuity(), "continuity")
self.stack.add_named(self._page_recovery(), "recovery")
self.stack.add_named(self._page_knowledge(), "knowledge")
self.stack.add_named(self._page_voice(), "voice")
self.stack.add_named(self._page_agents(), "agents")
self.stack.add_named(self._page_security(), "security")
```

### 3. Add nav items to `NAV_SECTIONS`

The nav items already exist in `NAV_SECTIONS` — no change needed there. But verify each `(page_id, title, icon_name, color_cls)` tuple matches.

### 4. Connect backend sockets

Each screen must connect to its corresponding Celina backend via Unix domain socket:

| Screen | Backend | Socket Path | Protocol |
|--------|---------|-------------|----------|
| performance | HardwareGovernor / AppNap | `/run/user/$UID/celina/app_nap.sock` | JSON over UDS |
| appearance | (UI only, no backend) | — | — |
| continuity | cgroup v2 App Nap | `/sys/fs/cgroup/` | file reads |
| recovery | Snapper / BTRFS | `/run/user/$UID/celina/snapshot.sock` | JSON over UDS |
| knowledge | Celina memory DB | `$HOME/.local/share/celina/db/memory.db` | SQLite |
| voice | celina-voice.service | `/run/user/$UID/celina/voice.sock` | JSON over UDS |
| agents | Celina agent dispatcher | `/run/user/$UID/celina/agents.sock` | JSON over UDS |
| security | MCP03 / 106 tools | `/run/user/$UID/celina/mcp.sock` | JSON over UDS |

Socket connection pattern:
```python
import socket, json

def _socket_request(socket_path: str, request: dict) -> dict | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(socket_path)
            s.sendall(json.dumps(request).encode())
            data = s.recv(65536)
            return json.loads(data.decode())
    except (ConnectionRefusedError, FileNotFoundError, json.JSONDecodeError):
        return None
```

### 5. Add animations

Use `Adw.SpringAnimation` for page reveals (see existing `_spring_reveal()` helper):
```python
def _spring_reveal(self, widget: Gtk.Widget) -> None:
    widget.set_opacity(0.0)
    target = Adw.CallbackAnimationTarget.new(
        lambda v: widget.set_opacity(max(0.0, min(1.0, v)))
    )
    anim = Adw.SpringAnimation.new(
        widget, 0.0, 1.0, Adw.SpringParams.new(0.88, 1.0, 380.0), target
    )
    anim.play()
```

Call `_spring_reveal(page_widget)` when a page is first shown.

### 6. State tracking

For each screen, add `self._<screen>_<setting>` instance variables in `__init__` following the existing pattern (e.g., `self._install_tier`, `self._install_luks_enabled`). All new method params must be optional kwargs with safe defaults.

### 7. Standing Constraints (DO NOT VIOLATE)

- DO NOT change `BusRequest.uid_provenance` default "untrusted"
- DO NOT enable 10 opt-in MCP servers
- DO NOT autostart GTK3 island_ui.py
- DO NOT bulk-delete system/ files without confirming zero references
- All stage file writes via P5 atomic DRY_RUN-safe wrappers
- `_applyMCPAction` in extension.js
- denylist before bwrap
- `PYTHONPATH` parent dir `features/celina`
- `fresh_candidates` init
- `_unescape_ics` backslash escaping
- exit code propagation
- no dummy apt install keyd echo
- Preserve P5 rollback exit contract (exit 3)
- `uid_provenance="untrusted"` fail-closed default
- all Celina QoS/security exemptions preserved
- `DRY_RUN=1` zero-HOME-mutation guarantee
- never overwrite existing user secrets
- every root file mutation flows through `P5 atomic write_root_file()/copy_root_file()`
- Memory v2: zero changes to `install.sh/core/stages/system/configs/manifests`
- 5-file max allowlist
- no new `TOOL_SCHEMAS` tools
- stdlib-only (`sqlite3`, `datetime`, `re`, `json`)
- all new method params optional kwargs with safe defaults
- idempotent in-place migration matching `cache.py` pattern
- fail-closed invalid `source_trust` defaults to `"untrusted_web"`
- Roadmap methodology: implement roadmap items systematically one by one; do not batch multiple subsystems in a single change
- Respect `/etc/macubuntu/hardware.json` (tier, has_battery, gpu_vendor)
- preserve Stage 61 exit-code-3 rollback contract
- never pass raw untrusted external text into LLM session with Tier-2 OS/filesystem mutation tools
- enforce live MCP tools/list SHA-256 pinning and never_exposed_paths
- zero-cloud privacy — no plaintext secrets or raw telemetry leave machine

### 8. Verification

After implementing each screen:
1. Run `python3 -c "import ast; ast.parse(open('apps/macubuntu-center/main.py').read())"` — must pass
2. Run `python3 -m pytest -q` — must be green (526 passed, 5 skipped, 0 failed)
3. Run `bash -n stages/stage-99-verify.sh` — must pass
4. Verify each nav item in `NAV_SECTIONS` has a corresponding `_page_*()` method and `_populate_pages()` registration

## Implementation Order

Implement one screen at a time, in this order:
1. `performance` — Performance & App Nap
2. `appearance` — Appearance & Input
3. `continuity` — Continuity & Power
4. `recovery` — Snapshots & Recovery
5. `knowledge` — Knowledge & Memory
6. `voice` — Voice & Live Playground
7. `agents` — Agents & Shortcuts
8. `security` — 106 Tools & MCP03

Commit each screen separately with a descriptive message.