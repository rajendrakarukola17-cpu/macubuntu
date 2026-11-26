"""Offline checks for the shipped SmolLM routing default and artifact mapping."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = "SmolLM2-135M-Instruct"
FILENAME = "smollm-135m.Q4_K_M.gguf"


def probe(home: Path, extra: dict[str, str] | None = None) -> dict:
    env = {"HOME": str(home), "TARGET_HOME": str(home), "PATH": os.defpath,
           "PYTHONPATH": str(ROOT / "features/celina"), "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(extra or {})
    code = '''import json
from celina_pkg import config, gear0_5_smolLM as model, model_selector
print(json.dumps({"configured": config.SMOLLM_MODEL, "runner": model.SMOLLM_MODEL,
"runner_path": model.SMOLLM_MODEL_PATH, "catalog": model.SMOLLM_MODELS.get(config.SMOLLM_MODEL),
"decision": model_selector.make_full_model_decision().smolLM}))
'''
    completed = subprocess.run([sys.executable, "-c", code], env=env, text=True,
                               capture_output=True, check=True, timeout=10)
    return json.loads(completed.stdout)


def test_default_uses_schema_and_resolves_artifact(tmp_path: Path) -> None:
    result = probe(tmp_path)
    assert result["configured"] == NAME
    assert result["runner"] == NAME
    assert result["catalog"]["tag"] == "HuggingFaceTB/SmolLM2-135M-Instruct"
    assert result["catalog"]["filename"] == FILENAME
    assert result["catalog"]["digest_env"] == "CELINA_SMOLLM_135M_SHA256"
    assert Path(result["runner_path"]).name == FILENAME
    assert result["decision"]["model"] == NAME
    assert result["decision"]["model_path"] == result["runner_path"]


def test_explicit_model_path_override_is_preserved(tmp_path: Path) -> None:
    path = str(tmp_path / "override.gguf")
    result = probe(tmp_path, {"CELINA_SMOLLM_MODEL_PATH": path})
    assert result["runner_path"] == path
    assert result["decision"]["model_path"] == path
