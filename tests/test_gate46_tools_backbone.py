"""Offline tool boundary and parsing tests; no real network or hardware is used."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from celina_pkg import config, tools

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Confine file tools to the real temporary fixture tree."""
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda *_args, **_kwargs: None)
    return tmp_path


def test_listing_and_search_limits_and_sensitive_exclusions(home: Path) -> None:
    for i in range(105):
        (home / f"file{i:03}.txt").write_text("needle in a haystack\n", encoding="utf-8")
    (home / ".netrc").write_text("SENSITIVE", encoding="utf-8")
    (home / "private").mkdir()
    output = tools.list_files(str(home))
    assert "capped at 100" in output and ".netrc" not in output
    assert "No entries" in tools.list_files(str(home), "*.missing")
    assert "No entries" in tools.list_files(str(home), ".netrc")
    assert "capped at 30" in tools.search_files("file", str(home))
    assert "capped at 25" in tools.search_in_files("needle", str(home))
    assert "Error" in tools.list_files(str(home.parent))


def test_bounded_read_failures_and_line_limits(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (home / ".ssh").mkdir()
    secret = home / ".ssh" / "id_rsa"
    secret.write_text("FIXTURE", encoding="utf-8")
    for func in (tools.read_lines, tools.read_function_signatures):
        assert "sensitive" in func(str(secret)).lower()
        assert "not found" in func(str(home / "missing.py")).lower()
    source = home / "source.py"
    source.write_text("def first(x):\n    return x\n\ndef second():\n    pass\n", encoding="utf-8")
    assert "more lines" in tools.read_lines(str(source), 1, 2)
    assert "start line" in tools.read_lines(str(source), 100, 101)
    assert "def first" in tools.read_function_signatures(str(source))
    monkeypatch.setattr(tools, "_MAX_EDIT_FILE_BYTES", 10)
    assert "too large" in tools.read_lines(str(source)).lower()
    assert "too large" in tools.read_function_signatures(str(source)).lower()


def test_search_skips_large_files_and_caps(home: Path) -> None:
    (home / "big.txt").write_text("needle" + "x" * 520_000, encoding="utf-8")
    (home / "small.txt").write_text("needle\n", encoding="utf-8")
    result = tools.search_in_files("needle", str(home))
    assert "small.txt" in result and "big.txt" not in result
    assert "Provide text" in tools.search_in_files(" ", str(home))


def test_html_parsers_extract_text_and_ignore_scripts() -> None:
    parser = tools._DDGParser()
    parser.feed('<div class="result__snippet">no prior result</div>')
    parser.feed('<a class="result__a" href="https://example.com">Title</a>'
                '<div class="result__snippet">Summary</div>')
    assert parser.results == [{"title": "Title", "url": "https://example.com", "snippet": "Summary"}]
    plain = tools._PlainTextParser()
    plain.feed("<p>Visible</p><script>secret</script><p> text </p>")
    assert "Visible" in plain.chunks and "secret" not in plain.chunks


def test_web_result_parser_with_simulated_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise text parsing with synthetic data; no network request is made."""
    replies = iter([
        SimpleNamespace(status_code=503, text="unavailable"),
        SimpleNamespace(status_code=200, text='<a class="result__a" href="https://example.com">Result</a>'),
    ])
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda *_args, **_kwargs: next(replies))
    assert tools._search_ddg_html("query")[0]["title"] == "Result"
    replies = iter([SimpleNamespace(status_code=200, json=lambda: ["q", ["A"], ["desc"], ["https://a.test"]]),
                    SimpleNamespace(status_code=200, json=lambda: {"hits": [{"title": "", "url": ""},
                        {"title": "B", "objectID": "2", "points": 1, "num_comments": 2}]})])
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda *_args, **_kwargs: next(replies))
    result = tools._search_fallback_apis("q")
    assert len(result) == 2 and result[0]["title"].startswith("A") and result[1]["title"] == "B"


def test_live_tool_empty_inputs_and_mock_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A simulated HTTP failure yields no false success; no network is contacted."""
    assert "city" in tools.get_weather("").lower()
    assert "city" in tools.get_current_time_zone("").lower()
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda *_args, **_kwargs: SimpleNamespace(status_code=503))
    assert "HTTP 503" in tools.get_weather("Test City")
    assert "HTTP 503" in tools.get_news_headlines("Test")
    assert "Current time" in tools.get_current_time_zone("UTC")
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda *_args, **_kwargs: SimpleNamespace(status_code=200, text="<rss/>"))
    assert "No news" in tools.get_news_headlines("Test")


def test_memory_search_aggregates_independent_store_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use an in-memory stub solely to test formatting of each memory channel."""
    db = SimpleNamespace(
        recall_relevant=lambda *_args, **_kwargs: ([('favorite', 'tea')], ['episode']),
        recall_knowledge_triples=lambda *_args, **_kwargs: ['triple'],
        search_history=lambda *_args, **_kwargs: [{'time': 'noon', 'role': 'user', 'content': 'tea'}],
    )
    monkeypatch.setattr(tools.memory, "get_db", lambda: db)
    out = tools.search_memory("tea")
    for fragment in ("favorite", "episode", "triple", "recent history"):
        assert fragment in out


def test_declared_schema_coercion_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = {"parameters": {"properties": {
        "i": {"type": "integer"}, "n": {"type": "number"}, "b": {"type": "boolean"},
    }}}
    monkeypatch.setitem(tools._TOOL_SCHEMA_BY_NAME, "example", schema)
    def handler(**_kwargs: object) -> None:
        pass
    for raw, expected in [
        ({"i": 1.7, "n": "12.5%", "b": "off"}, {"i": 1, "n": 12.5, "b": False}),
        ({"i": "bad", "n": "bad", "b": 1}, {"i": "bad", "n": "bad", "b": True}),
        ({"i": [], "n": 1.0, "b": "unknown"}, {"i": [], "n": 1.0, "b": "unknown"}),
    ]:
        assert tools._coerce_and_filter_tool_args("example", handler, raw) == expected


def test_tiered_presentation_fills_names_without_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A minimal synthetic schema inventory demonstrates ordering without model calls."""
    schemas = [{"function": {"name": name}} for name in ("one", "two", "three")]
    monkeypatch.setattr(tools, "_TOOL_SCHEMA_BY_NAME", {name: {"name": name} for name in ("one", "two", "three")})
    monkeypatch.setattr(tools, "get_enabled_tool_schemas", lambda: schemas)
    monkeypatch.setattr(tools, "get_contextual_tool_schemas", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(tools, "_lexical_rank_tools", lambda *_args, **_kwargs: [])
    result = tools.build_tiered_tool_presentation("", detailed_k=2)
    assert result["detailed_names"] == ["one", "two"]
    assert result["name_only_tools"] == ["three"]


def test_sandbox_requires_bound_approval_before_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulated bwrap availability exercises approval denials, not sandbox isolation."""
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *_a, **_k: pytest.fail("executed"))
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *_a, **_k: (None, "DENIED"))
    assert tools.run_python_sandbox("print(1)", approval_cb=lambda *_a: True) == "DENIED"
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *_a, **_k: (object(), ""))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *_a, **_k: (False, "BAD TOKEN"))
    assert tools.run_python_sandbox("print(1)", approval_cb=lambda *_a: True) == "BAD TOKEN"
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    assert tools.run_python_sandbox("print(1)", approval_cb=lambda *_a: True) == "BAD TOKEN"
    responses = iter([(object(), ""), (None, "SECOND DENIED")])
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *_a, **_k: next(responses))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *_a, **_k: (True, ""))
    assert tools.run_python_sandbox("print(1)", approval_cb=lambda *_a: True) == "SECOND DENIED"
    responses = iter([(object(), ""), (object(), "")])
    checks = iter([(True, ""), (False, "SECOND TOKEN BAD")])
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *_a, **_k: next(responses))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *_a, **_k: next(checks))
    assert tools.run_python_sandbox("print(1)", approval_cb=lambda *_a: True) == "SECOND TOKEN BAD"


def test_sandbox_simulated_output_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Output cap test substitutes the command runner; it proves no actual sandbox behavior."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *_a, **_k: (object(), ""))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *_a, **_k:
                        SimpleNamespace(timed_out=False, stdout="x" * 4000, stderr="", returncode=0))
    result = tools.run_python_sandbox("print(1)", approval_cb=lambda *_a: True)
    assert "[truncated]" in result and len(result) < 3100


def test_open_application_searches_local_desktop_metadata(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake launcher records selection; no desktop application is launched."""
    apps = home / ".local/share/applications"
    apps.mkdir(parents=True)
    (apps / "org.example.editor.desktop").write_text("[Desktop Entry]\nName=Editor Pro\n", encoding="utf-8")
    (apps / "org.example.diff.desktop").write_text("[Desktop Entry]\nGenericName=Document Viewer\n", encoding="utf-8")
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/gtk-launch" if name == "gtk-launch" else None)
    launched: list[list[str]] = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: launched.append(cmd) or True)
    assert "Launched" in tools.open_application("editor pro")
    assert launched[-1][-1] == "org.example.editor.desktop"
    assert "Launched" in tools.open_application("document viewer")
    assert launched[-1][-1] == "org.example.diff.desktop"
    assert "not found" in tools.open_application("not-real-app-at-all")


def test_intent_web_links_and_safe_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inspect URLs supplied to a fake launcher, with zero browser or network calls."""
    opened: list[str] = []
    monkeypatch.setattr(tools, "open_url_safe", lambda url: opened.append(url) or "opened")
    monkeypatch.setattr(tools, "open_application", lambda name: f"Application '{name}' not found.")
    monkeypatch.setattr(tools.spotlight_index, "convert_units", lambda _text: None)
    monkeypatch.setattr(tools.spotlight_index, "safe_calculate", lambda _text: None)
    assert "opened" in str(tools.handle_fast_intent("search youtube for piano music"))
    assert "opened" in str(tools.handle_fast_intent("open youtube and search for cats"))
    assert "credentials" in str(tools.handle_fast_intent("login to github")).lower()
    assert tools.handle_fast_intent("open my file") is None
    assert tools.handle_fast_intent("1+2") is None
    assert all(url.startswith("https://") for url in opened)


def test_workspace_snapshot_formats_only_provided_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock desktop enumeration and persistence; no desktop or persistent state is touched."""
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(tools.automation, "list_visible_workspace_apps", lambda: [{"app": "", "doc_path": ""}])
    assert "Could not extract" in tools.save_workspace_snapshot("test")
    monkeypatch.setattr(tools.automation, "list_visible_workspace_apps", lambda: [
        {"app": "editor", "doc_path": "/home/user/notes.txt"},
    ])
    monkeypatch.setattr(tools.skills, "learn_skill", lambda **kw: seen.append(kw) or "saved")
    result = tools.save_workspace_snapshot("Work", triggers="work workspace")
    assert "1 apps" in result
    assert len(seen[0]["steps"]) == 2
    assert seen[0]["triggers"].count("work workspace") == 1


def test_office_edit_denied_before_mutation(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sensitive files and denied or invalid approval tokens never reach UNO."""
    (home / ".ssh").mkdir()
    secret = home / ".ssh" / "id_rsa"
    secret.write_text("secret", encoding="utf-8")
    assert "sensitive" in tools.libreoffice_edit(str(secret), "edit").lower()
    doc = home / "test.odt"
    doc.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *_a, **_k: (None, "DENIED"))
    assert tools.libreoffice_edit(str(doc), "edit", approval_cb=lambda *_a: True) == "DENIED"
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *_a, **_k: (object(), ""))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *_a, **_k: (False, "BAD TOKEN"))
    assert tools.libreoffice_edit(str(doc), "edit", approval_cb=lambda *_a: True) == "BAD TOKEN"
    assert doc.read_text(encoding="utf-8") == "fixture"


def test_media_resolution_parses_simulated_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only parser behavior is tested; the HTTP response is synthetic and offline."""
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda *_a, **_k: None)
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda *_a, **_k:
                        SimpleNamespace(status_code=200, text='"videoId":"abcdefghijk"'))
    assert tools._resolve_youtube_direct_watch_url("song").endswith("watch?v=abcdefghijk")
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda *_a, **_k:
                        SimpleNamespace(status_code=200, text="no video"))
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)
    assert "results" in tools._resolve_youtube_direct_watch_url("song")


def test_edit_denials_precede_approval(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Protected and oversized files cannot be mutated or prompt for approval."""
    blocked = home / ".bashrc"
    blocked.write_text("before", encoding="utf-8")
    assert "sensitive" in tools.edit_file(str(blocked), "before", "after").lower()
    normal = home / "data.txt"
    normal.write_text("before", encoding="utf-8")
    monkeypatch.setattr(tools, "_MAX_EDIT_FILE_BYTES", 2)
    assert "too large" in tools.edit_file(str(normal), "before", "after").lower()
    assert normal.read_text(encoding="utf-8") == "before"
    assert "Document not found" in tools.read_file(str(home / "nonexistent.pdf"))


def test_url_fallbacks_remain_bounded_and_are_synthetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """No HTTP or desktop execution: check empty and failed search-response behavior."""
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda *_a, **_k:
                        SimpleNamespace(status_code=200, text="<p>no results</p>"))
    assert tools._search_ddg_html("q") == []
    replies = iter([SimpleNamespace(status_code=200, json=lambda: {"bad": "response"}),
                    SimpleNamespace(status_code=503)])
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda *_a, **_k: next(replies))
    assert tools._search_fallback_apis("q") == []
    parser = tools._DDGParser()
    parser.feed('<a class="result__a" href="/x">Title <b>bold</b></a>')
    parser.feed('<div class="result__snippet">Text <em>em</em></div>')
    assert "Title bold" == parser.results[0]["title"]
    plain = tools._PlainTextParser()
    plain.feed("<p> </p><script>x</script>ok")
    assert plain.chunks == ["ok"]


def test_media_actions_with_fake_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test routing and wording only; no media, API, browser or network is invoked."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"opened {url}")
    monkeypatch.setattr(tools, "_resolve_youtube_direct_watch_url", lambda _q: "https://youtube.test/watch?v=123")
    monkeypatch.setattr(tools, "_kick_browser_media_play_async", lambda: None)
    assert "directly" in tools._open_media_play("youtube", "melody from artist")
    monkeypatch.setattr(tools.api_actuators, "spotify_api_play", lambda *_a, **_k: (True, "played via api"))
    assert "played via api" in tools._open_media_play("spotify", "melody from artist", approval_cb=lambda *_a: True)
    assert "sign in" in tools._open_media_play("spotify", "melody from artist", login_requested=True).lower()
    assert "Sign in" in tools._open_media_play("unknown", "melody from artist", login_requested=True)
    assert "Opened" in tools._open_media_play("unknown", "melody from artist")


def test_fast_intents_do_not_forward_file_actions_to_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter fakes record intent dispatch; they neither launch apps nor touch the network."""
    calls: list[str] = []
    monkeypatch.setattr(tools, "open_url_safe", lambda url: calls.append(url) or "opened")
    monkeypatch.setattr(tools, "open_application", lambda name: calls.append(name) or "opened")
    monkeypatch.setattr(tools.spotlight_index, "convert_units", lambda _text: None)
    assert "opened" in str(tools.handle_fast_intent("login to spotify"))
    assert "opened" in str(tools.handle_fast_intent("open calculator"))
    before = len(calls)
    assert tools.handle_fast_intent("open my document") is None
    assert len(calls) == before


def test_write_and_edit_reject_shell_startup_paths(home: Path) -> None:
    """Writable workspace roots still reject privileged startup-file mutation."""
    startup = home / ".profile"
    startup.write_text("before", encoding="utf-8")
    assert "sensitive" in tools.write_file(str(startup), "after", lambda *_a: True).lower()
    assert "sensitive" in tools.edit_file(str(startup), "before", "after", lambda *_a: True).lower()
    assert startup.read_text(encoding="utf-8") == "before"


def test_sensitive_file_search_never_returns_credential(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real temporary files exercise credential filtering without a search subprocess."""
    (home / ".netrc").write_text("secret", encoding="utf-8")
    (home / "public.netrc").write_text("public", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", home / "private.env")
    (home / "private.env").write_text("secret", encoding="utf-8")
    assert tools._is_sensitive_path(home / "private.env")
    result = tools.search_files("", str(home), extension="netrc")
    assert "public.netrc" in result and "/.netrc" not in result
    assert "Error" not in tools.read_lines(str(home / "public.netrc"), 1, 20)


def test_media_resume_and_spotify_search_no_device_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Injected adapters explicitly avoid contacting a media player or browser."""
    monkeypatch.setattr(tools, "control_media", lambda _action: "local adapter")
    monkeypatch.setattr(tools, "open_url_safe", lambda _url: "opened")
    assert "local adapter" in tools._open_media_play("youtube", "")
    assert "Opened Spotify" in tools._open_media_play("spotify", "a longer album title")
