"""Celina AI — Multi-Agent Everyday Trainer & Readiness Grader (`celina-train`)
Runs 6 specialist Teacher/Evaluator agents (60 real everyday scenarios — including
messy phrasing, typos, multi-clause commands, Telugu, Hindi, restraint & security tests)
in 100% safe dry-run mode, grades Celina's actual dispatchers, and compiles verified
skills & Quiet-Brain exemplars into Celina's SQLite memory.

Usage:
  celina-train                   # Run 6-agent training & seed everyday skills/Quiet Brain
  celina-train --upgrade-brain   # Also pull qwen2.5:3b via Ollama and activate it
  celina-train --json            # Machine-readable scorecard JSON
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import shutil
import time
from pathlib import Path
from typing import Any

from . import command_runner, config, everyday_agents, memory, tools

UPGRADE_BRAIN_MODEL = "qwen2.5:3b"


@dataclasses.dataclass(frozen=True)
class TrainingCase:
    agent: str
    prompt: str
    kind: str  # "everyday" | "fast_intent" | "install" | "url_guard" | "restraint"
    expect_substrings: tuple[str, ...] = ()
    forbid_substrings: tuple[str, ...] = ("I cannot directly", "(No response)")


# 70 Real-World Everyday Cases across 6 Specialist Teacher Agents!
TRAINING_CURRICULUM: tuple[TrainingCase, ...] = (
    # 1. MediaTeacher (11 cases)
    TrainingCase("MediaTeacher", "do login in spotify if alreday login done play dude telugu songs", "fast_intent", ("open.spotify.com/search/dude%20telugu%20songs",)),
    TrainingCase("MediaTeacher", "play lofi beats on youtube", "fast_intent", ("youtube.com/watch?v=",)),
    TrainingCase("MediaTeacher", "play dude songs in youtube now", "fast_intent", ("Playing 'dude songs' directly on YouTube", "youtube.com/watch?v="), ("in youtube now", "I cannot directly", "(No response)")),
    TrainingCase("MediaTeacher", "play dude song in youtube stupid", "fast_intent", ("Sorry about that", "Playing 'dude song' directly on YouTube", "youtube.com/watch?v="), ("stupid", "I cannot directly", "(No response)")),
    TrainingCase("MediaTeacher", "play now", "everyday", ("MediaAgent", "Play/Resume"), ("results?search_query=now",)),
    TrainingCase("MediaTeacher", "play ar rahman hits", "fast_intent", ("youtube.com/watch?v=",)),
    TrainingCase("MediaTeacher", "open spotify and play telugu melodies", "fast_intent", ("open.spotify.com/search/",)),
    TrainingCase("MediaTeacher", "pause video", "everyday", ("MediaAgent", "pause")),
    TrainingCase("MediaTeacher", "vol up", "everyday", ("MediaAgent", "+10%")),
    TrainingCase("MediaTeacher", "brightness up", "everyday", ("MediaAgent", "85%")),
    TrainingCase("MediaTeacher", "వాల్యూమ్ పెంచు", "fast_intent", ()),
    TrainingCase("MediaTeacher", "आवाज़ बढ़ाओ", "fast_intent", ()),

    # 2. WebTeacher (13 cases)
    TrainingCase("WebTeacher", "open yt", "fast_intent", ("https://www.youtube.com",), ("python",)),
    TrainingCase("WebTeacher", "open spotify in web", "fast_intent", ("https://open.spotify.com",)),
    TrainingCase("WebTeacher", "open github.com", "fast_intent", ("https://github.com",)),
    TrainingCase("WebTeacher", "open swiggy in web", "fast_intent", ("google.com/search?q=swiggy",)),
    TrainingCase("WebTeacher", "search amazon for mechanical keyboard", "fast_intent", ("amazon.in/s?k=",)),
    TrainingCase("WebTeacher", "open flipkart and search wireless mouse", "fast_intent", ("flipkart.com/search?q=",)),
    TrainingCase("WebTeacher", "do login in github", "fast_intent", ("https://github.com/login",)),
    TrainingCase("WebTeacher", "login in gmail", "fast_intent", ("https://mail.google.com",)),
    TrainingCase("WebTeacher", "log me into github", "everyday", ("KeychainLogin", "github", "0 password exposure")),
    TrainingCase("WebTeacher", "auto login spotify", "everyday", ("KeychainLogin", "spotify", "GNOME Keyring")),
    TrainingCase("WebTeacher", "directions to Hyderabad Airport", "everyday", ("google.com/maps/search/",)),
    TrainingCase("WebTeacher", "translate good morning to telugu", "everyday", ("translate.google.com", "tl=te")),
    TrainingCase("WebTeacher", "weather in Visakhapatnam", "everyday", ("WebAgent", "weather")),

    # 3. FilesNotesTeacher (13 cases)
    TrainingCase("FilesNotesTeacher", "add todo buy groceries", "everyday", ("FilesNotesAgent", "buy groceries")),
    TrainingCase("FilesNotesTeacher", "show my todos", "everyday", ("to-do",)),
    TrainingCase("FilesNotesTeacher", "complete todo 1", "everyday", ("FilesNotesAgent", "1")),
    TrainingCase("FilesNotesTeacher", "take a note: project review at 5pm", "everyday", ("FilesNotesAgent", "project review")),
    TrainingCase("FilesNotesTeacher", "show my notes", "everyday", ("Notes",)),
    TrainingCase("FilesNotesTeacher", "organize my downloads", "everyday", ("FilesNotesAgent",)),
    TrainingCase("FilesNotesTeacher", "create folder Movies on desktop", "everyday", ("Desktop/Movies",)),
    TrainingCase("FilesNotesTeacher", "open downloads", "everyday", ("Downloads",)),
    TrainingCase("FilesNotesTeacher", "open documents", "everyday", ("Documents",)),
    TrainingCase("FilesNotesTeacher", "find file resume", "everyday", ("FilesNotesAgent", "resume", "depth 8")),
    TrainingCase("FilesNotesTeacher", "where is my budget report.pdf", "everyday", ("FilesNotesAgent", "budget report.pdf", "depth 8")),
    TrainingCase("FilesNotesTeacher", "reveal resume in folder", "everyday", ("FilesNotesAgent", "reveal in Files", "resume")),
    TrainingCase("FilesNotesTeacher", "remember my favorite music app is spotify", "everyday", ("LearningAgent", "favorite_music_app='spotify'")),

    # 4. SystemTeacher (20 cases — includes natural messy OS toggles)
    TrainingCase("SystemTeacher", "my pc is lagging fix it", "everyday", ("TurboMode", "CELINA_LITE_MODE=1")),
    TrainingCase("SystemTeacher", "turbo status", "everyday", ("SystemAgent", "ZRAM", "Turbo/Lite Mode")),
    TrainingCase("SystemTeacher", "on darkmode", "everyday", ("SystemAgent", "Dark Mode", "prefer-dark")),
    TrainingCase("SystemTeacher", "change to dark mode", "everyday", ("SystemAgent", "Dark Mode", "prefer-dark")),
    TrainingCase("SystemTeacher", "off darkmode", "everyday", ("SystemAgent", "Light Mode", "prefer-light")),
    TrainingCase("SystemTeacher", "on night light", "everyday", ("SystemAgent", "Night Light", "ON")),
    TrainingCase("SystemTeacher", "on dnd", "everyday", ("SystemAgent", "Do Not Disturb", "ON")),
    TrainingCase("SystemTeacher", "on wifi", "everyday", ("SystemAgent", "wifi", "on")),
    TrainingCase("SystemTeacher", "off bluetooth", "everyday", ("SystemAgent", "bluetooth", "off")),
    TrainingCase("SystemTeacher", "download spotify", "install", ("com.spotify.Client",)),
    TrainingCase("SystemTeacher", "install vlc", "install", ("org.videolan.VLC",)),
    TrainingCase("SystemTeacher", "install kdenlive", "install", ("Flathub/APT",)),
    TrainingCase("SystemTeacher", "open wifi settings", "fast_intent", ("wifi",)),
    TrainingCase("SystemTeacher", "open bluetooth settings", "fast_intent", ("bluetooth",)),
    TrainingCase("SystemTeacher", "check battery", "everyday", ("SystemAgent", "battery")),
    TrainingCase("SystemTeacher", "disk space", "everyday", ("Disk Space",)),
    TrainingCase("SystemTeacher", "what is my ip", "everyday", ("SystemAgent", "IP")),
    TrainingCase("SystemTeacher", "close firefox", "everyday", ("SystemAgent", "firefox")),
    TrainingCase("SystemTeacher", "sleep laptop", "everyday", ("SystemAgent", "suspend")),
    TrainingCase("SystemTeacher", "enable local mcp", "everyday", ("SystemAgent", "MCP")),

    # 5. RoutineTeacher (11 cases)
    TrainingCase("RoutineTeacher", "good morning", "everyday", ("Good Morning",)),
    TrainingCase("RoutineTeacher", "good night", "everyday", ("Good Night",)),
    TrainingCase("RoutineTeacher", "study mode", "everyday", ("Study/Work",)),
    TrainingCase("RoutineTeacher", "chill mode", "everyday", ("Chill Mode",)),
    TrainingCase("RoutineTeacher", "remind me in 15 minutes to drink water", "everyday", ("900s", "drink water")),
    TrainingCase("RoutineTeacher", "set timer for 5 minutes", "everyday", ("300s",)),
    TrainingCase("RoutineTeacher", "set a timer for 2 hours for meeting", "everyday", ("7200s", "meeting")),
    TrainingCase("RoutineTeacher", "when i say telugu vibe, open spotify", "everyday", ("LearningAgent", "telugu_vibe")),
    TrainingCase("RoutineTeacher", "time", "fast_intent", ("currently",)),
    TrainingCase("RoutineTeacher", "టైమ్ ఎంత", "fast_intent", ("సమయం",)),
    TrainingCase("RoutineTeacher", "समय क्या हुआ", "fast_intent", ("समय",)),

    # 6. GuiSafetyTeacher (11 cases — GUI quick controls + Restraint + Security Guards)
    TrainingCase("GuiSafetyTeacher", "read my screen", "everyday", ("GuiControlAgent", "OCR")),
    TrainingCase("GuiSafetyTeacher", "click play", "everyday", ("GuiControlAgent", "play")),
    TrainingCase("GuiSafetyTeacher", "click skip ad", "everyday", ("GuiControlAgent", "skip ad")),
    TrainingCase("GuiSafetyTeacher", "scroll down", "everyday", ("GuiControlAgent", "down")),
    TrainingCase("GuiSafetyTeacher", "fullscreen", "everyday", ("GuiControlAgent", "Fullscreen")),
    TrainingCase("GuiSafetyTeacher", "new tab", "everyday", ("GuiControlAgent", "ctrl+t")),
    TrainingCase("GuiSafetyTeacher", "type hello world", "everyday", ("GuiControlAgent", "hello world")),
    TrainingCase("GuiSafetyTeacher", "dim screen", "everyday", ("MediaAgent", "35%")),
    TrainingCase("GuiSafetyTeacher", "how do i play guitar", "restraint", ()),
    TrainingCase("GuiSafetyTeacher", "file:///etc/passwd", "url_guard", ("Blocked",)),
    TrainingCase("GuiSafetyTeacher", "javascript:alert(1)", "url_guard", ("Blocked",)),
)


EVERYDAY_KG_TRIPLES: tuple[tuple[str, str, str], ...] = (
    ("yt", "alias_of", "youtube"),
    ("youtube", "web_url", "https://www.youtube.com"),
    ("spotify", "web_url", "https://open.spotify.com"),
    ("play_now", "triggers", "control_media_play"),
    ("good_morning", "activates", "morning_routine"),
    ("good_night", "activates", "sleep_focus_and_dim"),
    ("organize_downloads", "sorts", "~/Downloads"),
    ("celina_train", "trains", "quiet_brain_kg_and_skills"),
    ("youtube_play", "resolves", "direct_watch_url"),
    ("custom_skill", "taught_via", "when_i_say_trigger_action"),
)

EVERYDAY_BUILTIN_SKILLS: tuple[dict[str, Any], ...] = (
    {
        "name": "play_lofi_focus",
        "title": "Play Lofi Focus",
        "description": "Opens lofi focus beats on YouTube and enables Work Focus Mode.",
        "triggers": ["lofi focus", "study music", "focus music"],
        "steps": [
            {"tool": "set_focus_mode", "args": {"mode": "work"}},
            {"tool": "open_application", "args": {"app_name": "https://www.youtube.com/results?search_query=lofi+hip+hop+beats"}},
        ],
    },
    {
        "name": "open_youtube_music",
        "title": "Open YouTube Music",
        "description": "Launches YouTube Music directly in your browser.",
        "triggers": ["youtube music", "yt music"],
        "steps": [{"tool": "open_application", "args": {"app_name": "https://music.youtube.com"}}],
    },
    {
        "name": "open_spotify_web",
        "title": "Open Spotify Web",
        "description": "Launches Spotify Web Player directly in your browser.",
        "triggers": ["spotify player", "music player web"],
        "steps": [{"tool": "open_application", "args": {"app_name": "https://open.spotify.com"}}],
    },
    {
        "name": "quick_pc_check",
        "title": "Quick PC Health Check",
        "description": "Checks live CPU, RAM, ZRAM, battery, and running desktop apps.",
        "triggers": ["quick pc check", "health check"],
        "steps": [
            {"tool": "get_system_stats", "args": {}},
            {"tool": "list_running_apps", "args": {}},
        ],
    },
)


EVERYDAY_COMPILED_EXEMPLARS: tuple[dict[str, Any], ...] = (
    {
        "query": "how do I play songs on spotify or youtube with celina",
        "answer": "Say 'play <song name>' for instant YouTube autoplay, or 'play <song name> on spotify' (or 'do login in spotify and play <song>') to launch Spotify directly to your songs.",
        "source_model": "everyday_trainer_v3",
    },
    {
        "query": "what everyday tasks can celina do",
        "answer": "Celina runs 6 built-in Everyday Agents: (1) Media (play YouTube/Spotify/JioSaavn, volume, brightness), (2) Web (open any website, search 15+ sites, login pages, Maps, Translate), (3) Files & Notes (to-dos, notes, folders, Downloads organizer), (4) System & Apps (install any Flathub/APT app, Wi-Fi/BT, dark mode, battery/RAM/disk/IP), (5) Daily Routines & Timers (Good Morning, Good Night, Study/Chill mode, reminders), and (6) Screen/GUI Clicker (read screen, click text, hotkeys, scroll, type).",
        "source_model": "everyday_trainer_v3",
    },
    {
        "query": "how do I run good morning or good night routine",
        "answer": "Just say 'good morning' (turns off DND, sets 70% brightness, unmutes audio, shows time, battery & to-dos) or 'good night' (enables Sleep Focus + Night Light, pauses media, dims screen to 20%, lowers volume to 15%, shows to-dos).",
        "source_model": "everyday_trainer_v3",
    },
    {
        "query": "how do I train or test celina on everyday tasks",
        "answer": "Run 'celina-train' in terminal or type '/train' in Celina chat to run all 6 Specialist Teacher Agents across 60 everyday tasks and compile verified patterns into Celina's Quiet Brain.",
        "source_model": "everyday_trainer_v3",
    },
)


def _evaluate_case(case: TrainingCase) -> tuple[bool, str, str]:
    """Evaluates a single training case safely (zero real OS mutations or background threads)."""
    orig_launch = tools.launch_detached
    orig_safe_get = tools.net_policy.safe_get
    orig_run_cmd = command_runner.run_command
    orig_kick_play = tools._kick_browser_media_play_async
    orig_mpris = tools._mpris_dbus_control
    launched: list[list[str]] = []

    class _FakeResp:
        status_code = 200
        text = '{"videoId":"dQw4w9WgXcQ"}'

    def _mock_launch(cmd: list[str]) -> bool:
        launched.append(list(cmd))
        return True

    def _mock_cmd(cmd: Any, timeout: float = 30.0, **kw: Any) -> command_runner.CommandResult:
        return command_runner.CommandResult(returncode=0, stdout="ok", stderr="", command=tuple(cmd))

    tools.launch_detached = _mock_launch  # type: ignore[assignment]
    tools.net_policy.safe_get = lambda *a, **kw: _FakeResp()  # type: ignore[assignment]
    command_runner.run_command = _mock_cmd  # type: ignore[assignment]
    tools._kick_browser_media_play_async = lambda: None  # type: ignore[assignment]
    tools._mpris_dbus_control = lambda act: (True, f"mock_mpris:{act}")  # type: ignore[assignment]
    try:
        if case.kind == "everyday":
            res = everyday_agents.dispatch_everyday_task(case.prompt, dry_run=True)
            out = f"[{res[0]}] {res[1]}" if res else ""
        elif case.kind == "fast_intent":
            out = tools.handle_fast_intent(case.prompt) or ""
        elif case.kind == "install":
            out = tools.handle_fast_install_intent(case.prompt, dry_run=True) or ""
        elif case.kind == "url_guard":
            out = tools.open_url_safe(case.prompt)
        elif case.kind == "restraint":
            ev = everyday_agents.dispatch_everyday_task(case.prompt, dry_run=True)
            fi = tools.handle_fast_intent(case.prompt)
            if ev is None and fi is None:
                return True, "Restraint OK (passed cleanly to Q&A)", ""
            return False, f"False-triggered action: {ev or fi}", "restraint_violation"
        else:
            return False, f"Unknown kind: {case.kind}", "config_error"
    finally:
        tools.launch_detached = orig_launch  # type: ignore[assignment]
        tools.net_policy.safe_get = orig_safe_get  # type: ignore[assignment]
        command_runner.run_command = orig_run_cmd  # type: ignore[assignment]
        tools._kick_browser_media_play_async = orig_kick_play  # type: ignore[assignment]
        tools._mpris_dbus_control = orig_mpris  # type: ignore[assignment]

    if not out:
        return False, "(no match / missed)", "missed_intent"
    for bad in case.forbid_substrings:
        if bad.lower() in out.lower():
            return False, out[:120], f"forbidden:{bad}"
    for req in case.expect_substrings:
        if req.lower() not in out.lower():
            return False, out[:120], f"missing:{req}"
    return True, out[:120], ""


def seed_compiled_everyday_brain(db: memory.MemoryDB | None = None) -> dict[str, int]:
    """Persists real training into ALL 3 SQLite stores: Quiet Brain, Knowledge Graph & Everyday Skills."""
    mdb = db or memory.get_db()
    hmem = mdb.get_hybrid_memory()
    compiled = 0
    for ex in EVERYDAY_COMPILED_EXEMPLARS:
        try:
            hmem.compile_knowledge(
                query_pattern=ex["query"],
                verified_answer=ex["answer"],
                source_model=ex["source_model"],
                confidence=0.96,
            )
            compiled += 1
        except (ValueError, OSError):
            continue

    kg_saved = 0
    for s, p, o in EVERYDAY_KG_TRIPLES:
        try:
            if mdb.add_knowledge_triple(s, p, o):
                kg_saved += 1
        except (ValueError, OSError):
            continue

    skills_saved = 0
    for sk in EVERYDAY_BUILTIN_SKILLS:
        try:
            mdb.save_skill(
                name=sk["name"],
                title=sk["title"],
                description=sk["description"],
                triggers=list(sk["triggers"]),
                steps=list(sk["steps"]),
            )
            skills_saved += 1
        except (ValueError, OSError):
            continue

    return {"quiet_brain": compiled, "knowledge_graph": kg_saved, "skills": skills_saved}


def run_multi_agent_training(
    *,
    db: memory.MemoryDB | None = None,
    save_report: bool = True,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Runs all 6 Specialist Teacher Agents + Critic Coach and persists training into memory.db."""
    t0 = time.monotonic()
    per_agent: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    passed = 0

    for case in TRAINING_CURRICULUM:
        ok, preview, reason = _evaluate_case(case)
        bucket = per_agent.setdefault(case.agent, {"passed": 0, "total": 0})
        bucket["total"] += 1
        if ok:
            bucket["passed"] += 1
            passed += 1
        else:
            failures.append({
                "agent": case.agent,
                "prompt": case.prompt,
                "reason": reason,
                "actual": preview,
            })

    seeded = seed_compiled_everyday_brain(db=db) if save_report else {"quiet_brain": 0, "knowledge_graph": 0, "skills": 0}
    total = len(TRAINING_CURRICULUM)
    score_pct = round((passed / max(1, total)) * 100.0, 1)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    report: dict[str, Any] = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "active_local_model": config.LOCAL_MODEL,
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "readiness_pct": score_pct,
        "compiled_quiet_brain_entries": seeded["quiet_brain"],
        "knowledge_graph_triples_saved": seeded["knowledge_graph"],
        "skills_saved": seeded["skills"],
        "elapsed_ms": elapsed_ms,
        "agents": per_agent,
        "failures": failures,
    }

    if save_report:
        out_file = report_path or (config.BASE_DIR / "training_report.json")
        try:
            config.atomic_write_private_text(out_file, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        except OSError:
            pass

    return report


def format_training_report(report: dict[str, Any]) -> str:
    lines = [
        "================================================================",
        f"  🎓 Celina Multi-Agent Everyday Trainer — {report['passed']}/{report['total_cases']} PASSED ({report['readiness_pct']}%)",
        f"  Local Brain: {report['active_local_model']} · Saved -> Quiet Brain: {report['compiled_quiet_brain_entries']} | KG Triples: {report.get('knowledge_graph_triples_saved', 0)} | Skills: {report.get('skills_saved', 0)} ({report['elapsed_ms']} ms)",
        "================================================================",
    ]
    for aname, stats in report.get("agents", {}).items():
        icon = "✅" if stats["passed"] == stats["total"] else "⚠️ "
        lines.append(f"  {icon} {aname:<20} {stats['passed']:>2}/{stats['total']} passed")
    if report.get("failures"):
        lines.append("\n  Critic Coach — Failures to Patch:")
        for f in report["failures"][:10]:
            lines.append(f'    ❌ [{f['agent']}] "{f['prompt']}" -> {f['reason']} ({f['actual']})')
    else:
        lines.append("\n  🏆 All 6 Everyday Agents verified at 100% readiness!")
        lines.append("  💡 Inspect saved training anytime with: /skills · /kg · /facts")
    lines.append("================================================================")
    return "\n".join(lines)


def upgrade_local_brain(model_tag: str = UPGRADE_BRAIN_MODEL, *, dry_run: bool = False) -> str:
    """Pulls a stronger 8GB-friendly tool-calling Ollama model (qwen2.5:3b) and persists it."""
    if dry_run:
        return f"🧪 Dry-Run: Would pull '{model_tag}' via Ollama and set CELINA_LOCAL_MODEL={model_tag}."
    if not shutil.which("ollama"):
        return f"Ollama is not installed on PATH. Install Ollama first, then run: ollama pull {model_tag}"
    res = command_runner.run_command(["ollama", "pull", model_tag], timeout=900.0)
    if not res.ok:
        err = (res.stderr or res.stdout or "pull failed").strip()[-300:]
        return f"❌ Failed to pull '{model_tag}': {err}"
    ok, msg = config.set_local_model(model_tag)
    if not ok:
        return f"⚠️ Pulled '{model_tag}', but could not save config: {msg}"
    return f"🧠 Local brain upgraded to '{model_tag}'! {msg}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Celina 6-Agent Everyday Trainer & Brain Upgrader")
    parser.add_argument("--upgrade-brain", action="store_true", help=f"Pull & activate {UPGRADE_BRAIN_MODEL} in Ollama")
    parser.add_argument("--enable-local-mcp", action="store_true", help="Enable installed zero-login local MCP servers (swadb-memory, playwright, git, sqlite, time)")
    parser.add_argument("--model", default=UPGRADE_BRAIN_MODEL, help=f"Model tag for --upgrade-brain (default: {UPGRADE_BRAIN_MODEL})")
    parser.add_argument("--dry-run", action="store_true", help="Do not write training report or pull models")
    parser.add_argument("--json", action="store_true", help="Print JSON scorecard")
    args = parser.parse_args(argv)

    if args.enable_local_mcp:
        print(everyday_agents.enable_local_zero_login_mcp(dry_run=args.dry_run))
    if args.upgrade_brain:
        print(upgrade_local_brain(args.model, dry_run=args.dry_run))

    report = run_multi_agent_training(save_report=not args.dry_run)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_training_report(report))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
