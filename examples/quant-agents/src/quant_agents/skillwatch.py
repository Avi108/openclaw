"""Keep our skill current: watch reference skill repos and report what changed.

For each watched repo it records the HEAD commit and a manifest of its skills
(name -> content hash). On the next check, a changed HEAD triggers a shallow clone
and a diff of the manifest: skills added, removed or edited since last time. Skill
names that match none of our capability keywords are flagged as possible gaps.

It only reads public repos and writes a digest. It never changes our code: a human
(or a reviewed task) decides what to adopt, and licenses must be checked before any
reuse. Reference repo contents are untrusted data and are never executed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# What our own skill already covers, as keywords that appear in skill names.
OUR_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "broker call consensus": ("consensus", "broker", "analyst-rating", "analyst", "reco"),
    "data quality and source audit": ("data-quality", "fact-check", "source", "validator", "result-validator"),
    "risk limits and sizing": ("risk", "position-size", "sizer", "sizing", "kelly", "circuit", "drawdown", "exposure"),
    "backtesting and validation": ("backtest", "validation", "edge", "walk-forward"),
    "scenario and stress tests": ("stress", "scenario"),
    "paper trading and review": ("paper", "postmortem", "performance", "portfolio-review", "trade-performance"),
    "global cues and macro": ("macro", "cues", "economic-calendar", "economics", "regime"),
    "valuation": ("valuation", "dcf", "intrinsic"),
    "fundamental quality": ("fundamental", "financial-health", "piotroski", "altman", "quality", "financial-health"),
    "thesis tracking": ("thesis", "bear-case"),
    "market breadth and sectors": ("breadth", "sector", "theme", "rotation"),
    "events and earnings": ("earnings", "catalyst", "calendar"),
    "tax": ("tax",),
    "exchange data feeds": ("nse", "bse", "bhavcopy", "yahoo", "yfinance", "alpaca", "finnhub", "mcp", "data"),
}


@dataclass(frozen=True)
class WatchItem:
    repo: str            # owner/name
    region: str          # e.g. "global", "india", "china", "emerging"
    focus: str = ""


@dataclass
class RepoChange:
    repo: str
    region: str
    first_seen: bool
    head: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)
    possible_gaps: list[str] = field(default_factory=list)
    error: str = ""


def load_watchlist(path: str | Path) -> list[WatchItem]:
    return [WatchItem(**i) for i in json.loads(Path(path).read_text())["repos"]]


def _git(*args: str, cwd: str | None = None, timeout: int = 180) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"})
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else f"git {args[0]} failed")
    return r.stdout


def remote_head(url: str) -> str:
    out = _git("ls-remote", url, "HEAD")
    if not out.strip():
        raise RuntimeError("no HEAD returned")
    return out.split()[0]


_FEATURE_DIRS = re.compile(r"(agent|signal|skill|strateg|analyst|workflow|persona)", re.I)


def skill_manifest(root: Path, root_name: str | None = None) -> dict[str, str]:
    """name -> short sha256 of each SKILL.md under root; duplicate names get a numeric suffix (stable order).

    Repos that are frameworks rather than skill packs (no SKILL.md) fall back to their agent / signal /
    strategy modules, so a new analyst or strategy file still shows up as a change.
    """
    out: dict[str, str] = {}
    files = [p for p in sorted(root.rglob("SKILL.md")) if ".git" not in p.parts]
    if not files:
        files = [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in (".py", ".md", ".ts")
                 and ".git" not in p.parts and "test" not in p.name.lower() and "__init__" not in p.name
                 and _FEATURE_DIRS.search("/".join(p.relative_to(root).parts[:-1]))]
    for p in files:
        stem = p.parent.name if p.name == "SKILL.md" else p.stem
        name = (root_name or root.name) if p.parent == root else stem
        key, n = name, 2
        while key in out:
            key, n = f"{name}#{n}", n + 1
        out[key] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


def is_covered(skill_name: str) -> bool:
    tokens = re.split(r"[-_ #]+", skill_name.lower())
    flat = skill_name.lower()
    # short keywords ("nse", "data", "tax") must match a whole word, or "license" and "database" count as covered
    return any(k in tokens or (len(k) >= 6 and k in flat) for kws in OUR_CAPABILITIES.values() for k in kws)


def check(items: list[WatchItem], state_path: str | Path, url_for=lambda repo: f"https://github.com/{repo}",
          now: datetime | None = None) -> list[RepoChange]:
    """Compare each repo with the saved state and update the state file. Errors are recorded per repo, not raised."""
    sp = Path(state_path)
    state: dict = json.loads(sp.read_text()) if sp.exists() else {}
    now = now or datetime.now(timezone.utc)
    changes: list[RepoChange] = []
    for it in items:
        url = url_for(it.repo)
        try:
            head = remote_head(url)
            prev = state.get(it.repo)
            if prev and prev["head"] == head:
                continue  # nothing new
            with tempfile.TemporaryDirectory(prefix="skillwatch-") as tmp:
                _git("clone", "--depth", "1", "--quiet", url, tmp + "/r", timeout=300)
                manifest = skill_manifest(Path(tmp) / "r", it.repo.split("/")[-1])
            old = prev["skills"] if prev else {}
            ch = RepoChange(it.repo, it.region, first_seen=prev is None, head=head[:12])
            ch.added = sorted(set(manifest) - set(old))
            ch.removed = sorted(set(old) - set(manifest))
            ch.edited = sorted(k for k in manifest if k in old and manifest[k] != old[k])
            ch.possible_gaps = [k for k in ch.added if not is_covered(k)]
            state[it.repo] = {"head": head, "skills": manifest, "checked_at": now.isoformat()}
            changes.append(ch)
        except Exception as e:  # network, clone, or parse failure for one repo must not stop the sweep
            changes.append(RepoChange(it.repo, it.region, False, "", error=str(e)[:200]))
    sp.write_text(json.dumps(state, indent=2, sort_keys=True))
    return changes


def render_digest(changes: list[RepoChange], today: str) -> str:
    lines = [f"# Skill watch digest, {today}", ""]
    if not changes:
        return "\n".join(lines + ["No watched repo changed since the last check."])
    for c in changes:
        if c.error:
            lines.append(f"- {c.repo} ({c.region}): check failed: {c.error}")
            continue
        if c.first_seen:  # baseline only: gaps are judged by hand once, then tracked as changes
            lines.append(f"- {c.repo} ({c.region}): baseline recorded, {len(c.added)} skills/modules (HEAD {c.head})")
            continue
        parts = [f"+{len(c.added)}" if c.added else "", f"-{len(c.removed)}" if c.removed else "", f"~{len(c.edited)}" if c.edited else ""]
        lines.append(f"- {c.repo} ({c.region}) HEAD {c.head}: " + " ".join(p for p in parts if p))
        for label, names in (("new", c.added), ("removed", c.removed), ("edited", c.edited)):
            if names:
                lines.append(f"    {label}: {', '.join(names[:12])}{' ...' if len(names) > 12 else ''}")
        if c.possible_gaps:
            lines.append(f"    NEW capability we may lack: {', '.join(c.possible_gaps)}")
    lines += ["", "Review before adopting anything: check each repo's license, never copy text, and re-test against our own suite."]
    return "\n".join(lines)
