# Copyright 2026 Celina contributors.
"""Self-improvement module layering and size guardrails."""

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "features" / "celina" / "celina_pkg" / "selfimprove"


ALLOWED = {
    "L0": set(),
    "L1": {"L0"},
    "L2": {"L0", "L1"},
    "L3": {"L0", "L1", "L2", "bridge"},
    "L4": {"L0", "bridge"},
    "L5": {"L0", "L1", "L2", "L4", "bridge"},
    "L6": {"L0", "L1", "L3", "bridge"},
    "L7": {"L0", "L1", "L2", "L3", "bridge"},
    "wiring": {"L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "bridge"},
    "bridge": {"L0"},
    "package": set(),
}
PREFIX = "celina_pkg.selfimprove"
MAX_FILE_LINES = 300
MAX_FUNCTION_LINES = 40


def classify_module(relative: Path) -> str | None:
    """Classify a source path into its declared layer."""
    parts = relative.with_suffix("").parts
    if parts == ("__init__",):
        return "package"
    head = parts[0]
    if head in {"contracts", "flags"}:
        return "L0"
    if head in {"wiring", "budget"}:
        return {"wiring": "wiring", "budget": "L1"}[head]
    if head == "ledger_bridge":
        return "bridge"
    if head.lower() in {f"l{i}" for i in range(8)}:
        return head.upper()
    return None


def module_name(relative: Path) -> str:
    """Resolve a file path into its package-qualified module name."""
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join((PREFIX, *parts))


def import_targets(node: ast.AST, current: str, is_package: bool) -> list[str]:  # noqa: FBT001  # Existing internal call signature.
    """Resolve statically expressed import targets."""
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    base = node.module or ""
    if node.level:
        package = current if is_package else current.rpartition(".")[0]
        try:
            base = importlib.util.resolve_name("." * node.level + base, package)
        except (ImportError, ValueError):
            return ["<invalid-relative-import>"]
    if any(a.name == "*" for a in node.names) and (base == PREFIX or base.startswith(PREFIX + ".")):
        return ["<selfimprove-wildcard-import>"]
    if base in (PREFIX, "celina_pkg"):
        return [base + "." + a.name for a in node.names]
    return [base]


def check_tree(package: Path) -> list[str]:  # noqa: C901, PLR0912, PLR0915  # Cohesive AST audit.
    """Check source files for layering, size, cycles and dynamic imports."""
    if package.is_symlink():
        return ["selfimprove package is a symlink"]
    if not package.exists():
        return []  # Pre-L0: no selfimprove package has been created yet.
    if not package.is_dir():
        return ["selfimprove package is not a real directory"]
    errors, graph, layers = [], {}, {}
    errors.extend(
        f"{entry.relative_to(package)}: symlink is forbidden"
        for entry in package.rglob("*") if entry.is_symlink()
    )
    files = sorted(package.rglob("*.py"))
    for path in files:
        rel = path.relative_to(package)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != package.parent):
            errors.append(f"{rel}: symlink is forbidden")
            continue
        name = module_name(rel)
        if name in graph:
            errors.append(f"{rel}: duplicate module name {name}")
        graph[name] = set()
        layers[name] = classify_module(rel)
        if layers[name] is None:
            errors.append(f"{rel}: unclassified module")
    for path in files:
        rel = path.relative_to(package)
        name = module_name(rel)
        if path.is_symlink() or name not in graph:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(rel))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"{rel}: cannot parse: {exc}")
            continue
        if rel.parts == ("wiring", "__init__.py") and (
            len(text.splitlines()) != 1 or len(tree.body) != 1 or ast.get_docstring(tree) is None
        ):
            errors.append(f"{rel}: initializer must contain only a single-line docstring")
        if len(text.splitlines()) >= MAX_FILE_LINES:
            errors.append(f"{rel}: file length must be < 300 lines")
        importer_names = {"__import__"}
        importlib_aliases = {"importlib"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                importlib_aliases.update(
                    a.asname or a.name for a in node.names if a.name == "importlib"
                )
            if isinstance(node, ast.ImportFrom) and node.module == "importlib":
                importer_names.update(
                    a.asname or a.name for a in node.names if a.name == "import_module"
                )
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                if node.end_lineno - start + 1 >= MAX_FUNCTION_LINES:
                    errors.append(f"{rel}:{node.lineno}: function length must be < 40 lines")
            if isinstance(node, ast.Call):
                f = node.func
                if (isinstance(f, ast.Name) and f.id in importer_names | {"exec", "eval"}) or (
                    isinstance(f, ast.Attribute)
                    and f.attr == "import_module"
                    and isinstance(f.value, ast.Name)
                    and f.value.id in importlib_aliases
                ):
                    errors.append(f"{rel}:{node.lineno}: dynamic import or execution forbidden")
            targets = import_targets(node, name, path.name == "__init__.py")
            if isinstance(node, ast.ImportFrom) and len(targets) == 1 and targets[0] in graph:
                targets += [
                    targets[0] + "." + alias.name
                    for alias in node.names
                    if targets[0] + "." + alias.name in graph
                ]
            for target in targets:
                if target.startswith("<"):
                    errors.append(f"{rel}:{node.lineno}: unsupported {target}")
                    continue
                if target == PREFIX:
                    errors.append(
                        f"{rel}:{node.lineno}: importing the selfimprove package is ambiguous",
                    )
                    continue
                if not target.startswith(PREFIX + "."):
                    continue
                matching = target
                while matching not in graph and matching.startswith(PREFIX + "."):
                    matching = matching.rpartition(".")[0]
                if matching not in graph:
                    errors.append(f"{rel}:{node.lineno}: unknown selfimprove import {target}")
                    continue
                graph[name].add(matching)
                if path.name == "__init__.py":
                    errors.append(
                        f"{rel}:{node.lineno}: initializer cannot import selfimprove layers",
                    )
                    continue
                allowed_sibling = (
                    len(rel.parts) > 1
                    and rel.parts[0] in {"l2", "l3", "l4", "l5", "l6", "l7", "wiring"}
                    and matching.startswith(PREFIX + "." + rel.parts[0] + ".")
                )
                if not allowed_sibling and (
                    layers.get(name) is None
                    or layers.get(matching) not in ALLOWED.get(layers.get(name), set())
                ):
                    errors.append(f"{rel}:{node.lineno}: upward or disallowed import {target}")
    colors = {}

    def visit(node: str) -> None:
        colors[node] = 1
        for target in graph[node]:
            if colors.get(target) == 1:
                errors.append(f"import cycle: {node} -> {target}")
            elif colors.get(target, 0) == 0:
                visit(target)
        colors[node] = 2

    for name in graph:
        if colors.get(name, 0) == 0:
            visit(name)
    return errors


def test_project_selfimprove_layering() -> None:
    """Verify project selfimprove layering."""
    assert check_tree(PACKAGE) == []


def test_upward_import_is_rejected(tmp_path: Path) -> None:
    """Verify upward import is rejected."""
    (tmp_path / "contracts.py").write_text("from . import budget\n")
    (tmp_path / "budget.py").write_text("VALUE = 1\n")
    assert any("upward or disallowed" in e for e in check_tree(tmp_path))


def test_file_at_300_lines_is_rejected(tmp_path: Path) -> None:
    """Verify file at 300 lines is rejected."""
    (tmp_path / "contracts.py").write_text("# line\n" * 300)
    assert any("file length" in e for e in check_tree(tmp_path))


def test_function_at_40_lines_is_rejected(tmp_path: Path) -> None:
    """Verify function at 40 lines is rejected."""
    (tmp_path / "contracts.py").write_text("def example():\n" + "    pass\n" * 39)
    assert any("function length" in e for e in check_tree(tmp_path))


def test_cycle_is_rejected(tmp_path: Path) -> None:
    """Verify cycle is rejected."""
    (tmp_path / "contracts.py").write_text("from . import flags\n")
    (tmp_path / "flags.py").write_text("from . import contracts\n")
    assert any("cycle" in e for e in check_tree(tmp_path))


def test_downward_import_and_small_files_are_allowed(tmp_path: Path) -> None:
    """Verify downward import and small files are allowed."""
    (tmp_path / "contracts.py").write_text("VALUE = 1\n")
    (tmp_path / "budget.py").write_text("from .contracts import VALUE\n")
    assert check_tree(tmp_path) == []


def test_unknown_module_and_dynamic_import_are_rejected(tmp_path: Path) -> None:
    """Verify unknown module and dynamic import are rejected."""
    (tmp_path / "unknown.py").write_text("VALUE = 1\n")
    (tmp_path / "flags.py").write_text("__import__('celina_pkg.selfimprove.budget')\n")
    errors = check_tree(tmp_path)
    assert any("unclassified module" in e for e in errors)
    assert any("dynamic import" in e for e in errors)


@pytest.mark.parametrize(
    ("source", "target", "allowed"),
    [
        ("l2", "budget", True),
        ("l3", "l2", True),
        ("l4", "l2", False),
        ("l4", "ledger_bridge", True),
        ("l5", "l4", True),
        ("l5", "l3", False),
        ("l6", "l3", True),
        ("l6", "l4", False),
        ("l7", "l3", True),
        ("l7", "l4", False),
    ],
)
def test_declared_layer_matrix(tmp_path: Path, source: str, target: str, allowed: bool) -> None:  # noqa: FBT001  # Pytest parameter.
    """Verify declared layer matrix."""
    (tmp_path / f"{source}.py").write_text(f"from .{target} import VALUE\n")
    (tmp_path / f"{target}.py").write_text("VALUE = 1\n")
    errors = check_tree(tmp_path)
    assert not errors if allowed else any("upward or disallowed" in e for e in errors)


def test_absolute_upward_import_and_symlink_are_rejected(tmp_path: Path) -> None:
    """Verify absolute upward import and symlink are rejected."""
    (tmp_path / "contracts.py").write_text("import celina_pkg.selfimprove.budget\n")
    (tmp_path / "budget.py").write_text("VALUE = 1\n")
    (tmp_path / "l2").symlink_to(tmp_path, target_is_directory=True)
    errors = check_tree(tmp_path)
    assert any("upward or disallowed" in e for e in errors)
    assert any("symlink" in e for e in errors)


def test_l2_acyclic_sibling_import_is_allowed(tmp_path: Path) -> None:
    """L2 verifiers may statically import the sibling runner without a cycle."""
    layer = tmp_path / "l2"
    layer.mkdir()
    (layer / "runner.py").write_text("VALUE = 1\n")
    (layer / "verifiers.py").write_text("from .runner import VALUE\n")
    assert check_tree(tmp_path) == []


def test_l2_sibling_cycle_is_rejected(tmp_path: Path) -> None:
    """A cycle between L2 sibling modules remains forbidden."""
    layer = tmp_path / "l2"
    layer.mkdir()
    (layer / "runner.py").write_text("from .verifiers import VALUE\n")
    (layer / "verifiers.py").write_text("from .runner import VALUE\n")
    assert any("cycle" in error for error in check_tree(tmp_path))


def test_l2_initializer_cannot_import_selfimprove_layers(tmp_path: Path) -> None:
    """The L2 package initializer must not re-export even L0 or L2 modules."""
    layer = tmp_path / "l2"
    layer.mkdir()
    (tmp_path / "contracts.py").write_text("VALUE = 1\n")
    (layer / "__init__.py").write_text("from ..contracts import VALUE\n")
    assert any("initializer" in error for error in check_tree(tmp_path))


def test_l7_acyclic_sibling_import_is_allowed(tmp_path: Path) -> None:
    """L7 may import acyclic sibling modules."""
    layer = tmp_path / "l7"
    layer.mkdir()
    (layer / "runner.py").write_text("VALUE = 1\n")
    (layer / "other.py").write_text("from .runner import VALUE\n")
    assert check_tree(tmp_path) == []


def test_l3_acyclic_sibling_and_bridge_imports_are_allowed(tmp_path: Path) -> None:
    """L3 may depend on acyclic siblings and the audit bridge."""
    layer = tmp_path / "l3"
    layer.mkdir()
    (tmp_path / "ledger_bridge.py").write_text("VALUE = 1\n")
    (layer / "suite.py").write_text("VALUE = 1\n")
    (layer / "runner.py").write_text(
        "from .suite import VALUE\nfrom ..ledger_bridge import VALUE\n",
    )
    assert check_tree(tmp_path) == []


def test_l3_sibling_cycle_is_rejected(tmp_path: Path) -> None:
    """Mutually importing L3 siblings are still rejected as a cycle."""
    layer = tmp_path / "l3"
    layer.mkdir()
    (layer / "suite.py").write_text("from .runner import VALUE\n")
    (layer / "runner.py").write_text("from .suite import VALUE\n")
    errors = check_tree(tmp_path)
    assert any("cycle" in error for error in errors)


def test_l3_upward_import_is_rejected(tmp_path: Path) -> None:
    """L3 cannot depend on L4."""
    layer = tmp_path / "l3"
    layer.mkdir()
    (tmp_path / "l4.py").write_text("VALUE = 1\n")
    (layer / "runner.py").write_text("from ..l4 import VALUE\n")
    assert any("upward or disallowed" in error for error in check_tree(tmp_path))


def test_l4_acyclic_sibling_import_is_allowed(tmp_path: Path) -> None:
    """L4 may import an acyclic sibling without changing cross-layer rules."""
    layer = tmp_path / "l4"
    layer.mkdir()
    (layer / "redactor.py").write_text("VALUE = 1\n")
    (layer / "store.py").write_text("from .redactor import VALUE\n")
    assert check_tree(tmp_path) == []


def test_l4_sibling_cycle_is_rejected(tmp_path: Path) -> None:
    """A circular L4 dependency remains forbidden."""
    layer = tmp_path / "l4"
    layer.mkdir()
    (layer / "redactor.py").write_text("from .store import VALUE\n")
    (layer / "store.py").write_text("from .redactor import VALUE\n")
    assert any("cycle" in error for error in check_tree(tmp_path))


@pytest.mark.parametrize("target", ["budget", "l2", "l3", "l5"])
def test_l4_disallowed_layer_import_is_rejected(tmp_path: Path, target: str) -> None:
    """L4 cannot import L1, L2, L3 or L5."""
    layer = tmp_path / "l4"
    layer.mkdir()
    (layer / "store.py").write_text(f"from ..{target} import VALUE\n")
    (tmp_path / f"{target}.py").write_text("VALUE = 1\n")
    assert any("upward or disallowed" in error for error in check_tree(tmp_path))


def test_l5_acyclic_sibling_import_is_allowed(tmp_path: Path) -> None:
    """L5 can import acyclic sibling modules."""
    layer = tmp_path / "l5"
    layer.mkdir()
    (layer / "models.py").write_text("VALUE = 1\n")
    (layer / "loop.py").write_text("from .models import VALUE\n")
    assert check_tree(tmp_path) == []


def test_l5_sibling_cycle_is_rejected(tmp_path: Path) -> None:
    """L5 sibling cycles are forbidden."""
    layer = tmp_path / "l5"
    layer.mkdir()
    (layer / "critic.py").write_text("from .loop import VALUE\n")
    (layer / "loop.py").write_text("from .critic import VALUE\n")
    assert any("cycle" in error for error in check_tree(tmp_path))


@pytest.mark.parametrize("target", ["l3", "l6", "l7"])
def test_l5_forbidden_layer_import_is_rejected(tmp_path: Path, target: str) -> None:
    """L5 cannot depend on L3, L6 or L7."""
    layer = tmp_path / "l5"
    layer.mkdir()
    (layer / "loop.py").write_text(f"from ..{target} import VALUE\n")
    (tmp_path / f"{target}.py").write_text("VALUE = 1\n")
    assert any("upward or disallowed" in error for error in check_tree(tmp_path))


def test_l6_acyclic_sibling_import_is_allowed(tmp_path: Path) -> None:
    """L6 may import acyclic sibling modules."""
    layer = tmp_path / "l6"
    layer.mkdir()
    (layer / "types.py").write_text("VALUE = 1\n")
    (layer / "registry.py").write_text("from .types import VALUE\n")
    assert check_tree(tmp_path) == []


def test_l6_sibling_cycle_is_rejected(tmp_path: Path) -> None:
    """L6 sibling cycles are forbidden."""
    layer = tmp_path / "l6"
    layer.mkdir()
    (layer / "types.py").write_text("from .registry import VALUE\n")
    (layer / "registry.py").write_text("from .types import VALUE\n")
    assert any("cycle" in error for error in check_tree(tmp_path))


@pytest.mark.parametrize("target", ["l2", "l4", "l5", "l7"])
def test_l6_forbidden_layer_import_is_rejected(tmp_path: Path, target: str) -> None:
    """L6 cannot depend on L2, L4, L5 or L7."""
    layer = tmp_path / "l6"
    layer.mkdir()
    (layer / "optimizer.py").write_text(f"from ..{target} import VALUE\n")
    (tmp_path / f"{target}.py").write_text("VALUE = 1\n")
    assert any("upward or disallowed" in error for error in check_tree(tmp_path))


def test_l6_initializer_cannot_import_selfimprove(tmp_path: Path) -> None:
    """L6 initializer cannot re-export even an allowed lower layer."""
    layer = tmp_path / "l6"
    layer.mkdir()
    (tmp_path / "contracts.py").write_text("VALUE = 1\n")
    (layer / "__init__.py").write_text("from ..contracts import VALUE\n")
    assert any("initializer" in error for error in check_tree(tmp_path))


def test_l7_sibling_cycle_is_rejected(tmp_path: Path) -> None:
    """Mutually importing L7 siblings are forbidden."""
    layer = tmp_path / "l7"
    layer.mkdir()
    (layer / "worktree.py").write_text("from .policy import VALUE\n")
    (layer / "policy.py").write_text("from .worktree import VALUE\n")
    assert any("cycle" in error for error in check_tree(tmp_path))


@pytest.mark.parametrize("target", ["l4", "l5", "l6"])
def test_l7_forbidden_layer_import_is_rejected(tmp_path: Path, target: str) -> None:
    """L7 cannot depend on L4, L5, or L6."""
    layer = tmp_path / "l7"
    layer.mkdir()
    (layer / "patcher.py").write_text(f"from ..{target} import VALUE\n")
    (tmp_path / f"{target}.py").write_text("VALUE = 1\n")
    assert any("upward or disallowed" in error for error in check_tree(tmp_path))


def test_l7_initializer_cannot_import_selfimprove(tmp_path: Path) -> None:
    """The L7 initializer may not import even an allowed lower layer."""
    layer = tmp_path / "l7"
    layer.mkdir()
    (tmp_path / "contracts.py").write_text("VALUE = 1\n")
    (layer / "__init__.py").write_text("from ..contracts import VALUE\n")
    assert any("initializer" in error for error in check_tree(tmp_path))


def test_wiring_allows_acyclic_siblings_and_lower_layers(tmp_path: Path) -> None:
    """The top layer may import lower layers and acyclic wiring siblings."""
    layer = tmp_path / "wiring"
    layer.mkdir()
    (layer / "__init__.py").write_text('"""Wiring."""\n')
    (layer / "facade.py").write_text("from .adapter import VALUE\nfrom ..l7.policy import VALUE\n")
    (layer / "adapter.py").write_text("import celina_pkg.approval\nVALUE = 1\n")
    (tmp_path / "l7").mkdir()
    (tmp_path / "l7" / "policy.py").write_text("VALUE = 1\n")
    assert check_tree(tmp_path) == []


def test_wiring_cycle_is_rejected(tmp_path: Path) -> None:
    """Cycles between wiring modules remain forbidden."""
    layer = tmp_path / "wiring"
    layer.mkdir()
    (layer / "first.py").write_text("from .second import VALUE\n")
    (layer / "second.py").write_text("from .first import VALUE\n")
    assert any("cycle" in error for error in check_tree(tmp_path))


@pytest.mark.parametrize("lower", ["l5", "l7", "ledger_bridge"])
def test_lower_layers_cannot_import_wiring(tmp_path: Path, lower: str) -> None:
    """No lower-layer import may create a dependency on the integration layer."""
    layer = tmp_path / "wiring"
    layer.mkdir()
    (layer / "adapter.py").write_text("VALUE = 1\n")
    (tmp_path / f"{lower}.py").write_text("from .wiring.adapter import VALUE\n")
    assert any("upward or disallowed" in error for error in check_tree(tmp_path))


def test_wiring_initializer_is_only_single_line_docstring(tmp_path: Path) -> None:
    """Wiring initializer cannot import a layer or perform other work."""
    layer = tmp_path / "wiring"
    layer.mkdir()
    (layer / "adapter.py").write_text("VALUE = 1\n")
    for content in ("from .adapter import VALUE\n", '"""Wiring."""\nVALUE = 1\n'):
        (layer / "__init__.py").write_text(content)
        assert any("initializer" in error for error in check_tree(tmp_path))
