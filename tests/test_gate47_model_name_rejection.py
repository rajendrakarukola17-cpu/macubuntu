"""Fail closed on stale or unknown SmolLM identities without loading models."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run_probe(home: Path, *, missing_template: bool, env_extra: dict[str, str] | None = None) -> dict:
    env = {"HOME": str(home), "TARGET_HOME": str(home), "PATH": os.defpath,
           "PYTHONPATH": str(ROOT / "features/celina"), "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(env_extra or {})
    script = f'''import json
from pathlib import Path
original = Path.is_file
if {missing_template!r}:
    Path.is_file = lambda self: False if self.name == "neural-schema.json" and "features/celina/config" in str(self) else original(self)
from celina_pkg import config
results = {{"configured": config.SMOLLM_MODEL}}
try:
    from celina_pkg import gear0_5_smolLM as model
    results["runner"] = model.SMOLLM_MODEL_PATH
except Exception as exc:
    results["runner_error"] = type(exc).__name__
try:
    from celina_pkg import model_selector
    results["selector"] = model_selector.make_full_model_decision().smolLM["model_path"]
except Exception as exc:
    results["selector_error"] = type(exc).__name__
print(json.dumps(results))
'''
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                            text=True, check=True, timeout=15)
    return json.loads(result.stdout)


def test_missing_schema_rejects_stale_protected_fallback(tmp_path: Path) -> None:
    result = run_probe(tmp_path, missing_template=True)
    assert result == {"configured": "smollm-117m", "runner_error": "UnknownSmolLMModelError",
                      "selector_error": "UnknownSmolLMModelError"}


def test_unknown_override_is_rejected_in_both_consumers(tmp_path: Path) -> None:
    result = run_probe(tmp_path, missing_template=False, env_extra={"CELINA_SMOLLM_MODEL": "not-a-catalog-model"})
    assert result == {"configured": "not-a-catalog-model", "runner_error": "UnknownSmolLMModelError",
                      "selector_error": "UnknownSmolLMModelError"}


def test_known_model_and_explicit_path_share_runner_and_selector(tmp_path: Path) -> None:
    default = run_probe(tmp_path, missing_template=False)
    assert Path(default["runner"]).name == "smollm-135m.Q4_K_M.gguf"
    assert default["runner"] == default["selector"]
    override = str(tmp_path / "custom.gguf")
    selected = run_probe(tmp_path, missing_template=False, env_extra={"CELINA_SMOLLM_MODEL_PATH": override})
    assert selected["runner"] == selected["selector"] == override
