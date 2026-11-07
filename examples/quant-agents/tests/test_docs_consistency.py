"""Docs must match reality: every command, module, file and function the docs mention has to exist."""
import importlib
import re

from conftest import ROOT
from quant_agents.cli import build_parser

README = (ROOT / "README.md").read_text()
GAP = (ROOT / "docs" / "GAP_ANALYSIS.md").read_text()
SKILL = (ROOT / "skills" / "india-equity-check" / "SKILL.md").read_text()
SRC = ROOT / "src" / "quant_agents"


def commands():
    return set(next(a for a in build_parser()._actions if a.dest == "cmd").choices)


def test_every_command_is_in_the_readme_reference_table():
    missing = sorted(c for c in commands() if f"`{c}`" not in README)
    assert not missing, f"commands missing from the README command reference: {missing}"


def test_every_documented_command_exists():
    said = set(re.findall(r"quant-agents ([a-z][a-z-]+)", README + GAP + SKILL)) - {"--help"}
    assert said <= commands(), f"docs mention commands that do not exist: {sorted(said - commands())}"


def test_every_module_is_in_the_readme_layout_and_every_listed_module_exists():
    layout = README[README.index("## Layout"):README.index("## Command reference")]
    listed = set(re.findall(r"^\s{2}([a-z_]+\.py)\b", layout, re.M))
    real = {p.name for p in SRC.glob("*.py") if p.name != "__init__.py"}
    assert listed == real, f"layout drift: only in README {sorted(listed - real)}, only in src {sorted(real - listed)}"


def test_no_hardcoded_test_counts_or_stale_network_claims():
    assert not re.search(r"\b\d+ tests\b", README + GAP), "hard-coded test counts go stale; describe the gates instead"
    assert "no network access" not in README.lower()


def test_files_the_readme_refers_to_exist():
    for rel in set(re.findall(r"`((?:data|docs|skills|integrations|watch)/[\w./-]+)`", README + GAP)):
        if rel.endswith("/") or "*" in rel or "<" in rel:
            continue
        assert (ROOT / rel).exists() or (ROOT / rel).parent.exists(), f"README refers to missing path {rel}"


def test_gap_analysis_modules_exist():
    for mod in set(re.findall(r"`([a-z_]+)\.py`", GAP)):
        assert (SRC / f"{mod}.py").exists(), f"GAP_ANALYSIS mentions {mod}.py which does not exist"


def test_skill_md_functions_resolve():
    """Every `module.function` the skill tells the agent to call must be importable."""
    refs = set(re.findall(r"`([a-z_]+)\.([A-Za-z_]+)`", SKILL))
    assert refs, "expected the skill to reference tools"
    for mod, attr in refs:
        m = importlib.import_module(f"quant_agents.{mod}")
        assert hasattr(m, attr), f"SKILL.md references {mod}.{attr}, which does not exist"
    assert "quant-agents consensus" in SKILL and "quant-agents propose" in SKILL


def test_skill_front_matter_is_valid():
    head = SKILL.split("---")[1]
    assert re.search(r"^name: india-equity-check$", head, re.M) and re.search(r"^description: .{40,}", head, re.M)


def test_makefile_has_the_documented_targets():
    mk = (ROOT / "Makefile").read_text()
    for target in ("check", "test", "lint", "types", "smoke", "mutate", "cov"):
        assert re.search(rf"^{target}:", mk, re.M), f"Makefile lacks target {target}"
