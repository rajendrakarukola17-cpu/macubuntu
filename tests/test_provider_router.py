"""Regression tests for task-aware cloud provider selection and bounded failover."""
from __future__ import annotations

import json
import time
import types

import pytest

from celina_pkg import config, provider_router, router


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body or {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            exc = router.requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            exc.response = types.SimpleNamespace(status_code=self.status_code)
            raise exc

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def iter_lines(self, decode_unicode=True):
        yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
        yield "data: [DONE]"


@pytest.fixture(autouse=True)
def clean_provider_state(monkeypatch):
    provider_router.reset_state_for_tests()
    for key in ("GROQ_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY", "MISTRAL_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY",
                "XAI_API_KEY", "ZHIPUAI_API_KEY", "DEEPSEEK_API_KEY", "TOGETHER_API_KEY", "PERPLEXITY_API_KEY"):
        monkeypatch.setattr(config, key, "", raising=False)
    monkeypatch.delenv("CELINA_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("CELINA_ENSEMBLE", raising=False)
    yield
    provider_router.reset_state_for_tests()


def test_task_ranking_prefers_gemini_for_documents(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-gemini")
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-groq")
    candidates = provider_router.candidates("Summarize this PDF document", "fast")
    assert [p.name for p, _ in candidates] == ["gemini", "groq"]


def test_provider_status_never_exposes_api_keys(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "do-not-print-this-secret")
    status = provider_router.status()
    assert next(x for x in status if x["name"] == "groq")["configured"] is True
    assert "do-not-print-this-secret" not in json.dumps(status)


def test_http_429_fails_over_immediately_and_records_provider(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "groq-secret")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("CELINA_PROVIDER_ORDER", "groq,gemini")
    obj = object.__new__(router.CelinaRouter)
    calls = []

    def fake_post(self, url, payload, timeout, headers=None, retries=3, stream=False):
        calls.append((url, payload["model"], timeout, headers["Authorization"]))
        if len(calls) == 1:
            return FakeResponse(429, headers={"Retry-After": "10"})
        return FakeResponse(200)

    obj._post_with_retry = types.MethodType(fake_post, obj)
    result = obj._post_openrouter([{"role": "user", "content": "hello"}], "fast", use_tools=False)
    assert result["_celina_provider"] == "gemini"
    assert result["_celina_model"] == "gemini-2.5-flash"
    assert [x[0] for x in calls] == [
        "https://api.groq.com/openai/v1/chat/completions",
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    ]
    assert calls[0][2] <= 12
    assert calls[0][3] == "Bearer groq-secret"
    assert next(x for x in provider_router.status() if x["name"] == "groq")["cooldown_seconds"] > 0


def test_no_keys_returns_empty_cloud_candidates():
    assert provider_router.candidates("hello", "fast") == []


def test_failover_attempt_count_is_bounded_to_three(monkeypatch):
    for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.setattr(config, name, "configured")
    obj = object.__new__(router.CelinaRouter)
    calls = []

    def fail_post(self, url, payload, timeout, headers=None, retries=3, stream=False):
        calls.append(url)
        return FakeResponse(429)

    obj._post_with_retry = types.MethodType(fail_post, obj)
    with pytest.raises(RuntimeError, match="All configured cloud providers failed"):
        obj._post_openrouter([{"role": "user", "content": "hello"}], "fast", use_tools=False)
    assert len(calls) == 3


# --- New provider tests (Update 18) ---

def test_xai_provider_configured(monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", "xai-secret")
    candidates = provider_router.candidates("reason about this math problem", "deep")
    xai_found = any(p.name == "xai" for p, _ in candidates)
    assert xai_found


def test_glm_provider_configured(monkeypatch):
    monkeypatch.setattr(config, "ZHIPUAI_API_KEY", "zhipu-secret")
    candidates = provider_router.candidates("translate this to chinese", "fast")
    glm_found = any(p.name == "glm" for p, _ in candidates)
    assert glm_found


def test_deepseek_provider_configured(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "deepseek-secret")
    candidates = provider_router.candidates("write python code", "fast")
    deepseek_found = any(p.name == "deepseek" for p, _ in candidates)
    assert deepseek_found


def test_together_provider_configured(monkeypatch):
    monkeypatch.setattr(config, "TOGETHER_API_KEY", "together-secret")
    candidates = provider_router.candidates("open source model", "fast")
    together_found = any(p.name == "together" for p, _ in candidates)
    assert together_found


def test_perplexity_provider_configured(monkeypatch):
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "perplexity-secret")
    candidates = provider_router.candidates("search current weather", "fast")
    perplexity_found = any(p.name == "perplexity" for p, _ in candidates)
    assert perplexity_found


# --- Task decomposition tests ---

def test_decompose_task_code(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.setattr(config, "CEREBRAS_API_KEY", "cerebras-secret")
    monkeypatch.setattr(config, "GROQ_API_KEY", "groq-secret")
    subtasks = provider_router.decompose_task("write a python script to backup files", "standard")
    assert len(subtasks) >= 1
    code_task = next((s for s in subtasks if "Code task" in s["subtask"]), None)
    assert code_task is not None
    assert code_task["provider"] == "deepseek"


def test_decompose_task_math(monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", "xai-secret")
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nvidia-secret")
    subtasks = provider_router.decompose_task("solve this calculus equation", "standard")
    math_task = next((s for s in subtasks if "Math" in s["subtask"]), None)
    assert math_task is not None
    assert math_task["provider"] == "xai"


def test_decompose_task_search(monkeypatch):
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "perplexity-secret")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "openrouter-secret")
    subtasks = provider_router.decompose_task("search current weather in Tokyo", "standard")
    search_task = next((s for s in subtasks if "Web research" in s["subtask"]), None)
    assert search_task is not None
    assert search_task["provider"] == "perplexity"


def test_decompose_task_creative(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gemini-secret")
    subtasks = provider_router.decompose_task("write a poem about space", "standard")
    creative_task = next((s for s in subtasks if "Creative writing" in s["subtask"]), None)
    assert creative_task is not None
    assert creative_task["provider"] == "gemini"


def test_decompose_task_no_match_returns_general():
    subtasks = provider_router.decompose_task("just say hello", "standard")
    assert len(subtasks) == 1
    assert subtasks[0]["subtask"] == "just say hello"
    assert subtasks[0]["provider"] == "groq"


# --- Ensemble routing tests ---

def test_ensemble_candidates_returns_parallel_providers(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.setattr(config, "XAI_API_KEY", "xai-secret")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gemini-secret")
    candidates = provider_router.ensemble_candidates("write python code and search weather", "standard")
    # Should return multiple providers for different subtasks
    assert len(candidates) >= 1
    providers = [p.name for p, _, _, _ in candidates]
    assert "deepseek" in providers or "perplexity" in providers


def test_ensemble_candidates_empty_when_no_keys():
    candidates = provider_router.ensemble_candidates("hello world", "standard")
    assert candidates == []


def test_merge_ensemble_results_single():
    results = [{"provider": "groq", "model": "llama-3.3-70b-versatile", "subtask": "general", "response": "Hello world!", "error": None}]
    merged = provider_router.merge_ensemble_results(results)
    assert merged == "Hello world!"


def test_merge_ensemble_results_multiple():
    results = [
        {"provider": "deepseek", "model": "deepseek-coder", "subtask": "code", "response": "def hello(): print('hi')", "error": None},
        {"provider": "gemini", "model": "gemini-2.5-flash", "subtask": "writing", "response": "Here is a story about...", "error": None},
    ]
    merged = provider_router.merge_ensemble_results(results)
    assert "deepseek" in merged
    assert "gemini" in merged
    assert "def hello" in merged
    assert "story" in merged


def test_merge_ensemble_results_all_failed():
    results = [
        {"provider": "groq", "model": "llama-3.3-70b-versatile", "subtask": "general", "response": "", "error": "rate limited"},
    ]
    merged = provider_router.merge_ensemble_results(results)
    assert "rate limited" in merged


def test_is_ensemble_enabled_default_false(monkeypatch):
    monkeypatch.delenv("CELINA_ENSEMBLE", raising=False)
    assert provider_router.is_ensemble_enabled() is False


def test_set_ensemble_toggle():
    provider_router.set_ensemble(True)
    assert provider_router.is_ensemble_enabled() is True
    provider_router.set_ensemble(False)
    assert provider_router.is_ensemble_enabled() is False


# --- Automatic ensemble decision tests (Update 18) ---

def test_should_use_ensemble_multi_domain():
    # Code + search = two domains -> ensemble
    assert provider_router.should_use_ensemble("write python code and search for the latest weather") is True


def test_should_use_ensemble_single_domain_simple():
    # Single domain, short -> no ensemble
    assert provider_router.should_use_ensemble("hello world") is False


def test_should_use_ensemble_complex_single_domain():
    # 60+ words with one domain -> ensemble
    long_text = " ".join(["explain"] * 65)
    assert provider_router.should_use_ensemble(long_text) is True


def test_should_use_ensemble_multiple_conjunctions():
    # Multiple conjunctions -> ensemble
    assert provider_router.should_use_ensemble("do this and then that and also this") is True


# --- Model discovery tests ---

def test_discover_available_models_fallback_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    provider = next(p for p in provider_router.PROVIDERS if p.name == "groq")
    models = provider_router.discover_available_models(provider)
    assert models == ["llama-3.3-70b-versatile"]


def test_discover_available_models_cached(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    provider = next(p for p in provider_router.PROVIDERS if p.name == "groq")
    # Clear cache first
    provider_router._DISCOVERED_MODELS.pop("groq", None)
    provider_router._DISCOVERY_TIMESTAMP.pop("groq", None)
    models = provider_router.discover_available_models(provider)
    assert isinstance(models, list)
    assert len(models) >= 1


def test_select_best_model_code_prefers_coder(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    provider = next(p for p in provider_router.PROVIDERS if p.name == "deepseek")
    provider_router._DISCOVERED_MODELS["deepseek"] = ["deepseek-chat", "deepseek-coder", "some-other-model"]
    provider_router._DISCOVERY_TIMESTAMP["deepseek"] = time.monotonic()
    model = provider_router.select_best_model(provider, "code")
    assert model == "deepseek-coder"


def test_select_best_model_math_prefers_grok(monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", "test-key")
    provider = next(p for p in provider_router.PROVIDERS if p.name == "xai")
    provider_router._DISCOVERED_MODELS["xai"] = ["grok-3-mini", "grok-3", "some-model"]
    provider_router._DISCOVERY_TIMESTAMP["xai"] = time.monotonic()
    model = provider_router.select_best_model(provider, "math")
    assert model == "grok-3-mini"


def test_select_best_model_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    provider = next(p for p in provider_router.PROVIDERS if p.name == "groq")
    provider_router._DISCOVERED_MODELS["groq"] = ["unknown-model-xyz"]
    provider_router._DISCOVERY_TIMESTAMP["groq"] = time.monotonic()
    model = provider_router.select_best_model(provider, "code")
    assert model == "unknown-model-xyz"


def test_decompose_task_uses_auto_discovered_model(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    provider_router._DISCOVERED_MODELS["deepseek"] = ["deepseek-coder-v2"]
    provider_router._DISCOVERY_TIMESTAMP["deepseek"] = time.monotonic()
    provider_router._DISCOVERED_MODELS["groq"] = ["llama-3.3-70b-versatile"]
    provider_router._DISCOVERY_TIMESTAMP["groq"] = time.monotonic()
    subtasks = provider_router.decompose_task("write python code", "standard")
    code_task = next((s for s in subtasks if "Code task" in s["subtask"]), None)
    assert code_task is not None
    assert code_task["model"] == "deepseek-coder-v2"
