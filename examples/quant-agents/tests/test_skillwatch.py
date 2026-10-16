import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quant_agents.skillwatch import (WatchItem, check, is_covered, load_watchlist, remote_head, render_digest,
                                     skill_manifest)

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd)})


def make_repo(tmp_path, name="up"):
    r = tmp_path / name
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    return r


def add_skill(r, name, body):
    d = r / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body)


def commit(r, msg="c"):
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", msg)


def local(tmp_path):
    return lambda repo: str(tmp_path / repo)


def test_first_check_then_no_change_then_detects_added_edited_removed(tmp_path):
    r = make_repo(tmp_path)
    add_skill(r, "dcf-valuation", "v1")
    add_skill(r, "old-skill", "x")
    commit(r)
    items = [WatchItem("up", "global")]
    state = tmp_path / "state.json"

    c1 = check(items, state, local(tmp_path), NOW)
    assert c1[0].first_seen and c1[0].added == ["dcf-valuation", "old-skill"]

    assert check(items, state, local(tmp_path), NOW) == []  # same HEAD: silent

    add_skill(r, "dcf-valuation", "v2")                      # edited
    add_skill(r, "crypto-funding-rates", "new")              # added, and not something we cover
    (r / "skills" / "old-skill" / "SKILL.md").unlink()       # removed
    commit(r)
    c2 = check(items, state, local(tmp_path), NOW)[0]
    assert (c2.added, c2.edited, c2.removed) == (["crypto-funding-rates"], ["dcf-valuation"], ["old-skill"])
    assert c2.possible_gaps == ["crypto-funding-rates"]
    text = render_digest([c2], "2026-10-17")
    assert "NEW capability we may lack: crypto-funding-rates" in text and "license" in text


def test_failures_are_recorded_per_repo_and_do_not_stop_the_sweep(tmp_path):
    r = make_repo(tmp_path)
    add_skill(r, "breadth-analyzer", "a")
    commit(r)
    out = check([WatchItem("missing", "india"), WatchItem("up", "global")], tmp_path / "s.json", local(tmp_path), NOW)
    assert out[0].error and not out[1].error and out[1].added == ["breadth-analyzer"]
    assert "check failed" in render_digest(out, "d")


def test_manifest_naming_and_coverage_keywords(tmp_path):
    r = make_repo(tmp_path)
    add_skill(r, "a", "1")
    (r / "other" / "a").mkdir(parents=True)
    (r / "other" / "a" / "SKILL.md").write_text("2")
    m = skill_manifest(r)
    assert set(m) == {"a", "a#2"}
    assert is_covered("dcf-valuation") and is_covered("portfolio-risk-manager") and is_covered("nse-bhavcopy-loader")
    assert not is_covered("crypto-funding-rates")


def test_remote_head_and_watchlist_file(tmp_path):
    r = make_repo(tmp_path)
    add_skill(r, "s", "1")
    commit(r)
    assert len(remote_head(str(r))) == 40
    with pytest.raises(RuntimeError):
        remote_head(str(tmp_path / "nope"))
    wl = load_watchlist(Path(__file__).resolve().parent.parent / "data" / "watchlist.json")
    assert len(wl) >= 12 and {w.region for w in wl} >= {"global", "india", "china", "emerging"}


def test_no_change_digest():
    assert "No watched repo changed" in render_digest([], "2026-10-17")


def test_framework_repo_without_skills_falls_back_to_agent_modules(tmp_path):
    r = make_repo(tmp_path, "fw")
    (r / "src" / "agents").mkdir(parents=True)
    (r / "src" / "agents" / "buffett.py").write_text("v1")
    (r / "src" / "agents" / "test_buffett.py").write_text("ignored")
    (r / "src" / "utils").mkdir()
    (r / "src" / "utils" / "helpers.py").write_text("ignored: not a feature dir")
    commit(r)
    state = tmp_path / "st.json"
    first = check([WatchItem("fw", "global")], state, local(tmp_path), NOW)[0]
    assert first.added == ["buffett"]
    (r / "src" / "agents" / "regime.py").write_text("new analyst")
    commit(r)
    second = check([WatchItem("fw", "global")], state, local(tmp_path), NOW)[0]
    assert second.added == ["regime"] and "baseline" in render_digest([first], "d")


def test_root_level_skill_named_after_repo_not_temp_dir(tmp_path):
    r = make_repo(tmp_path, "solo")
    (r / "SKILL.md").write_text("x")
    commit(r)
    assert list(skill_manifest(r, "my-skill")) == ["my-skill"]
