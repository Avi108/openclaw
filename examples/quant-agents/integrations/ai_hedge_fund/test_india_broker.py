"""Run with the fund on PYTHONPATH: PYTHONPATH=src:integrations/ai_hedge_fund:<fund checkout> pytest integrations"""
from datetime import date

import pytest

pytest.importorskip("hedge_fund")

from india_broker import IndiaBrokerConsensusModel, register  # noqa: E402
from quant_agents.ingest import parse_calls  # noqa: E402

CALLS = parse_calls(
    "\n".join(
        [
            "2026-10-01 | GOOD | B1 | buy | target 150 | cmp 100",
            "2026-10-02 | GOOD | B2 | add | target 145 | cmp 100",
            "2026-10-03 | GOOD | B3 | accumulate | target 155 | cmp 100",
            "2026-10-03 | LONE | B1 | buy | target 160 | cmp 100",
            "2026-10-03 | HYPE | B1 | buy | target 500 | cmp 100",
            "2026-10-03 | HYPE | B2 | buy | target 520 | cmp 100",
            "2026-10-12 | LATE | B1 | buy | target 150 | cmp 100",
            "2026-10-12 | LATE | B2 | buy | target 150 | cmp 100",
            "2026-08-01 | OLD | B1 | buy | target 150 | cmp 100",
            "2026-08-01 | OLD | B2 | buy | target 150 | cmp 100",
            "2026-10-01 | DOWN | B1 | sell | target 70 | cmp 100",
            "2026-10-01 | DOWN | B2 | reduce | target 75 | cmp 100",
        ]
    )
).calls


def model():
    return IndiaBrokerConsensusModel(loader=lambda: CALLS)


def test_positive_conviction_bounded_and_explained():
    s = model().predict("GOOD", "2026-10-14")
    assert 0.3 < s.value <= 1.0 and "3 brokers" in s.reasoning and not s.metadata.get("abstained")


def test_abstains_on_thin_or_implausible_data():
    m = model()
    for t in ("LONE", "HYPE", "NOPE"):
        s = m.predict(t, "2026-10-14")
        assert s.value == 0.0 and s.metadata["abstained"] is True


def test_point_in_time_no_future_calls():
    m = model()
    assert m.predict("LATE", "2026-10-10").metadata.get("abstained")  # published 10-12, not yet public
    assert not m.predict("LATE", "2026-10-13").metadata.get("abstained")


def test_stale_calls_ignored():
    assert model().predict("OLD", "2026-10-14").metadata["abstained"]


def test_bearish_consensus_gives_negative_value():
    assert model().predict("DOWN", "2026-10-14").value < 0


def test_registration_and_declared_approach():
    from hedge_fund.signals import ALPHA_MODEL_REGISTRY, get_investment_approach

    register()
    assert ALPHA_MODEL_REGISTRY["india_broker"] is IndiaBrokerConsensusModel
    assert get_investment_approach("india_broker") == "long_only"
