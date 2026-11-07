#!/usr/bin/env python3
"""Mutation testing for the risk-critical modules.

For each module it applies one small change at a time (flip a comparison, swap + and -, swap and/or, drop `not`,
flip a boolean, nudge a number), runs the tests that exercise that module, and reports whether any test failed
("killed") or all passed ("survived"). A survivor is code that could be wrong without any test noticing; it is
either a missing test or an equivalent mutant (a change that cannot alter behavior).

Works on throwaway copies of the project in parallel, with bytecode caching off (a same-size edit in the same
second would otherwise load stale .pyc files and give false results).

    python3 scripts/mutate.py                       # default modules
    python3 scripts/mutate.py risk consensus -j 4   # chosen modules
    python3 scripts/mutate.py --report mutation.md
"""
from __future__ import annotations

import argparse
import ast
import copy
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "quant_agents"
DEFAULT_MODULES = ["risk", "consensus", "tax", "discipline", "posture", "ladder", "quality", "thesis", "valuation",
                   "corporate", "liquidity", "crosscheck", "events", "fundamentals"]
SLOW_TESTS = {"test_properties.py", "test_smoke.py"}  # excluded unless --with-slow

CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
ARITH = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}


@dataclass(frozen=True)
class Mutant:
    module: str
    index: int
    line: int
    description: str


def candidates(tree: ast.AST) -> list[ast.AST]:
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Compare) and type(node.ops[0]) in CMP) or (isinstance(node, ast.BinOp) and type(node.op) in ARITH) or isinstance(node, ast.BoolOp) or (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)) or (isinstance(node, ast.Constant) and isinstance(node.value, (bool, int, float))):
            out.append(node)
    return out


def describe(node: ast.AST) -> str:
    if isinstance(node, ast.Compare):
        return f"{type(node.ops[0]).__name__} -> {CMP[type(node.ops[0])].__name__}"
    if isinstance(node, ast.BinOp):
        return f"{type(node.op).__name__} -> {ARITH[type(node.op)].__name__}"
    if isinstance(node, ast.BoolOp):
        return "and <-> or"
    if isinstance(node, ast.UnaryOp):
        return "remove `not`"
    v = node.value  # type: ignore[attr-defined]
    return f"constant {v!r} -> {mutate_constant(v)!r}"


def mutate_constant(v):
    if isinstance(v, bool):
        return not v
    if isinstance(v, int):
        return v + 1
    return v + 1.0 if v == 0 else v * 1.5 + 1.0 if abs(v) < 1 else v + 1.0


def apply(tree: ast.AST, index: int) -> ast.AST:
    t = copy.deepcopy(tree)
    nodes = candidates(t)
    node = nodes[index]
    if isinstance(node, ast.Compare):
        node.ops[0] = CMP[type(node.ops[0])]()
    elif isinstance(node, ast.BinOp):
        node.op = ARITH[type(node.op)]()
    elif isinstance(node, ast.BoolOp):
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif isinstance(node, ast.UnaryOp):
        # replace in parent: easiest is to turn `not x` into `x` by mutating into a no-op double negation removal
        node.op = ast.UAdd()  # type: ignore[assignment]
    else:
        node.value = mutate_constant(node.value)  # type: ignore[attr-defined]
    return ast.fix_missing_locations(t)


def list_mutants(module: str, source: str) -> list[Mutant]:
    tree = ast.parse(source)
    lines = source.splitlines()
    out = []
    for i, node in enumerate(candidates(tree)):
        line = getattr(node, "lineno", 0)
        if line and "pragma: no mutate" in lines[line - 1]:
            continue
        out.append(Mutant(module, i, line, describe(node)))
    return out


def imported_modules(test_file: Path) -> set[str]:
    """Names of quant_agents modules a test file imports (handles multi-line and `from quant_agents import (a, b)`)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(test_file.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "quant_agents":
                found.update(a.name for a in node.names)
            elif node.module.startswith("quant_agents."):
                found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".")[1] for a in node.names if a.name.startswith("quant_agents."))
    return found


def tests_for(module: str, with_slow: bool) -> list[str]:
    files = []
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        if f.name in SLOW_TESTS and not with_slow:
            continue
        if module in imported_modules(f) or f.name == "test_golden.py":
            files.append(f"tests/{f.name}")
    return files


def make_workdir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="mut-"))
    shutil.copytree(ROOT, d / "p", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "watch", "*.pyc", ".git"))
    return d / "p"


def run_tests(work: Path, files: list[str], timeout: int) -> tuple[bool, str]:
    env = {**os.environ, "PYTHONPATH": str(work / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *files], cwd=work, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timeout (counted as killed)"
    return r.returncode == 0, r.stdout[-300:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modules", nargs="*", default=DEFAULT_MODULES)
    ap.add_argument("-j", "--jobs", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--with-slow", action="store_true", help="also run property and smoke tests (much slower)")
    ap.add_argument("--report", help="write a Markdown report here")
    ap.add_argument("--limit", type=int, help="only the first N mutants per module (for a quick look)")
    a = ap.parse_args()

    workdirs = [make_workdir() for _ in range(a.jobs)]
    free = list(range(a.jobs))
    import threading
    lock = threading.Lock()

    def job(m: Mutant, files: list[str] = (), original: str = "", tree: ast.AST | None = None):
        with lock:
            w = free.pop()
        try:
            path = workdirs[w] / "src" / "quant_agents" / f"{m.module}.py"
            path.write_text(ast.unparse(apply(tree, m.index)))
            passed, _ = run_tests(workdirs[w], files, a.timeout)
            return m, passed  # passed == survived
        finally:
            (workdirs[w] / "src" / "quant_agents" / f"{m.module}.py").write_text(original)
            with lock:
                free.append(w)

    lines = ["# Mutation report", ""]
    total_k = total_n = 0
    for mod in a.modules:
        src_path = SRC / f"{mod}.py"
        original = src_path.read_text()
        files = tests_for(mod, a.with_slow)
        ok, out = run_tests(workdirs[0], files, a.timeout)
        if not ok:
            print(f"{mod}: baseline tests FAIL on the unmutated copy; fix before mutating\n{out}")
            return 2
        tree = ast.parse(original)
        muts = list_mutants(mod, original)[: a.limit]
        with ThreadPoolExecutor(a.jobs) as ex:
            res = list(ex.map(partial(job, files=files, original=original, tree=tree), muts))
        survived = [m for m, passed in res if passed]
        killed = len(res) - len(survived)
        total_k, total_n = total_k + killed, total_n + len(res)
        pct = 100.0 * killed / len(res) if res else 100.0
        print(f"{mod:<14} {killed:>4}/{len(res):<4} killed  {pct:5.1f}%   tests: {', '.join(Path(f).name for f in files)}")
        lines += [f"## {mod}: {killed}/{len(res)} killed ({pct:.1f}%)", ""]
        src_lines = original.splitlines()
        for m in survived:
            snippet = src_lines[m.line - 1].strip() if m.line else ""
            print(f"    SURVIVED line {m.line}: {m.description}    | {snippet[:90]}")
            lines.append(f"- line {m.line}: `{m.description}` in `{snippet[:100]}`")
        lines.append("")
    score = 100.0 * total_k / total_n if total_n else 100.0
    print(f"\nTOTAL {total_k}/{total_n} killed = {score:.1f}%")
    lines.insert(2, f"Overall: {total_k}/{total_n} mutants killed ({score:.1f}%).\n")
    if a.report:
        Path(a.report).write_text("\n".join(lines) + "\n")
    for w in workdirs:
        shutil.rmtree(w.parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
