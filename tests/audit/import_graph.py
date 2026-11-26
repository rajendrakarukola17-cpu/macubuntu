"""
Build an import graph for celina_pkg using AST parsing.
Handles both absolute and relative imports.
Outputs: audit-reports/import-graph.txt (edge list), audit-reports/import-cycles.txt
"""

import ast
import os
import sys
from collections import defaultdict

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "features" / "celina"))

PKG_DIR = str(Path(__file__).resolve().parents[2] / "features" / "celina" / "celina_pkg")


def get_module_name(filepath):
    rel = os.path.relpath(filepath, PKG_DIR)
    parts = rel.replace(".py", "").split(os.sep)
    return "celina_pkg." + ".".join(parts)


def resolve_relative_import(current_mod, level, module):
    """Resolve a relative import to an absolute module name.

    current_mod: e.g. "celina_pkg.connectors.base"
    level: number of dots (1 for ".", 2 for "..", etc.)
    module: the module part after the dots, or None
    """
    parts = current_mod.split(".")  # ['celina_pkg', 'connectors', 'base']
    # The package containing the current file is everything except the last part
    # (the last part is the module name itself)
    package_parts = parts[:-1]  # ['celina_pkg', 'connectors']

    # Go up `level` packages from the current package
    # level=1 means the current package itself (from . import x)
    # level=2 means the parent package (from .. import x)
    base_parts = package_parts[: len(package_parts) - (level - 1)]

    base = ".".join(base_parts)
    if module:
        return base + "." + module
    return base


def parse_imports(filepath, current_mod):
    """Return list of imported module names (resolved to celina_pkg submodules where possible)."""
    imports = []
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except SyntaxError as e:
        print(f"SYNTAX ERROR in {filepath}: {e}", file=sys.stderr)
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None or node.level > 0:
                if node.level > 0:
                    full = resolve_relative_import(current_mod, node.level, node.module)
                else:
                    full = node.module
                imports.append(full)

    return imports


# Build module -> file mapping
module_file = {}
for root, dirs, files in os.walk(PKG_DIR):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in files:
        if f.endswith(".py"):
            mod = get_module_name(os.path.join(root, f))
            module_file[mod] = os.path.join(root, f)

# For each module, find which other celina_pkg modules it imports
edges = defaultdict(set)
for mod, fpath in module_file.items():
    for imp in parse_imports(fpath, mod):
        # Check if this import resolves to a celina_pkg submodule
        if imp in module_file:
            edges[mod].add(imp)
            continue
        if imp.startswith("celina_pkg.") and imp in module_file:
            edges[mod].add(imp)
            continue
        # Try matching a top-level celina_pkg submodule (e.g., "config" -> "celina_pkg.config")
        imp_parts = imp.split(".")
        candidate = "celina_pkg." + imp_parts[0]
        if candidate in module_file:
            edges[mod].add(candidate)

# Detect cycles using DFS
WHITE, GRAY, BLACK = 0, 1, 2
color = {mod: WHITE for mod in module_file}
cycles = []


def dfs(mod, path):
    color[mod] = GRAY
    path.append(mod)
    for nxt in edges.get(mod, set()):
        if nxt not in color:
            continue
        if color[nxt] == GRAY:
            cycle_start = path.index(nxt)
            cycles.append(path[cycle_start:] + [nxt])
        elif color[nxt] == WHITE:
            dfs(nxt, path)
    path.pop()
    color[mod] = BLACK


for mod in sorted(module_file):
    if color[mod] == WHITE:
        dfs(mod, [])

# Deduplicate cycles
seen = set()
unique_cycles = []
for c in cycles:
    key = tuple(c)
    if key not in seen:
        seen.add(key)
        unique_cycles.append(c)

# Write edge list
with open(Path(__file__).resolve().parents[2] / "audit-reports" / "import-graph.txt", "w") as f:
    f.write("Import edges (caller -> callee, both in celina_pkg):\n")
    for src in sorted(edges):
        for dst in sorted(edges[src]):
            f.write(f"  {src} -> {dst}\n")

# Write cycles
with open(Path(__file__).resolve().parents[2] / "audit-reports" / "import-cycles.txt", "w") as f:
    if unique_cycles:
        f.write(f"Found {len(unique_cycles)} import cycle(s):\n")
        for c in unique_cycles:
            f.write("  " + " -> ".join(c) + "\n")
    else:
        f.write("No import cycles detected.\n")

print(f"Modules: {len(module_file)}")
print(f"Edges: {sum(len(v) for v in edges.values())}")
print(f"Cycles: {len(unique_cycles)}")
for c in unique_cycles[:10]:
    print("  CYCLE:", " -> ".join(c))