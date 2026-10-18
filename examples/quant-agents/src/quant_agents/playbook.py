"""Event playbook: how sectors have tended to behave around wars, pandemics, weather and other shocks.

This is a small, hand-curated reference of past episodes. It is NOT a dataset and NOT a predictor:
- Dates are public facts; the sector effects (win / flop / mixed) are qualitative recollections of how
  Indian-listed sectors broadly behaved, entered without price data. Treat each as a hypothesis to check
  with `measure_episode` on real sector-index returns before relying on it.
- Few episodes per category (often 3 to 6), each with its own context (valuations, rates, policy). The
  judgment says so and downgrades itself when the sample is thin or the evidence split.
- Output is a tilt for research and for the discipline gate, never an order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from collections.abc import Mapping, Sequence

Effect = str  # "win" | "flop" | "mixed"
CONF_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3}
SCORE = {"win": 1.0, "flop": -1.0, "mixed": 0.0}


@dataclass(frozen=True)
class Episode:
    id: str
    name: str
    category: str
    start: date
    end: date
    region: str
    confidence: str  # how sure we are of the sector effects recalled below
    effects: Mapping[str, Effect]
    aftermath: str  # what tended to happen after the shock, in words


def _e(id_: str, name: str, cat: str, s: tuple[int, int, int], e: tuple[int, int, int], region: str, conf: str,
       effects: Mapping[str, Effect], aftermath: str) -> Episode:
    return Episode(id_, name, cat, date(*s), date(*e), region, conf, effects, aftermath)


W, F, M = "win", "flop", "mixed"

CATALOG: tuple[Episode, ...] = (
    # ---- wars and geopolitics ----
    _e("gulf-1990", "Gulf War / oil spike", "war", (1990, 8, 2), (1991, 2, 28), "global", "low",
       {"oil_gas": W, "airlines_travel": F, "defense": W, "paints": F},
       "Oil spiked then fell back when the conflict ended; broad markets recovered within months."),
    _e("sept11-2001", "9/11 attacks", "war", (2001, 9, 11), (2001, 10, 31), "global", "medium",
       {"airlines_travel": F, "insurance": F, "defense": W, "gold_jewelry": W, "it": F},
       "Sharp one-to-two-week fall, then recovery inside weeks to a few months; airlines and insurers lagged."),
    _e("kargil-1999", "Kargil conflict", "war", (1999, 5, 3), (1999, 7, 26), "india", "low",
       {"defense": W, "airlines_travel": F},
       "Local conflict; the index drifted rather than crashed and recovered after the ceasefire."),
    _e("ukraine-2022", "Russia invades Ukraine", "war", (2022, 2, 24), (2022, 6, 30), "global", "medium",
       {"oil_gas": W, "metals": M, "chemicals_fertilizers": W, "defense": W, "airlines_travel": F, "paints": F,
        "auto": F, "consumer_discretionary": F, "fmcg": F},
       "Commodity inflation squeezed input-cost-heavy sectors; central banks tightened; equities fell through mid-2022."),
    _e("israel-hamas-2023", "Israel-Hamas war / Red Sea shipping disruption", "war", (2023, 10, 7), (2024, 3, 31), "global", "low",
       {"shipping_logistics": W, "defense": W, "oil_gas": M, "textiles_exporters": F},
       "Markets absorbed it quickly; freight rates rose on rerouted shipping and hurt exporters' margins."),
    _e("sindoor-2025", "India-Pakistan strikes (May 2025)", "war", (2025, 5, 7), (2025, 5, 10), "india", "low",
       {"defense": W, "airlines_travel": F},
       "Short flare-up; defense names rallied hard and then gave back part of it."),
    _e("abqaiq-2019", "Abqaiq attack on Saudi oil", "oil_shock", (2019, 9, 14), (2019, 9, 30), "global", "low",
       {"oil_gas": M, "airlines_travel": F, "paints": F},
       "Oil jumped then retraced within weeks as output was restored."),
    # ---- pandemics ----
    _e("sars-2003", "SARS", "pandemic", (2002, 11, 16), (2003, 7, 5), "asia", "low",
       {"airlines_travel": F, "hotels_leisure": F, "pharma": W},
       "Regional; markets recovered once containment was announced."),
    _e("swine-flu-2009", "H1N1 swine flu", "pandemic", (2009, 4, 24), (2009, 8, 10), "global", "low",
       {"pharma": W, "airlines_travel": F},
       "Mild market impact next to the post-2008 recovery rally."),
    _e("covid-2020", "COVID-19 crash and rebound", "pandemic", (2020, 2, 20), (2020, 12, 31), "global", "high",
       {"airlines_travel": F, "hotels_leisure": F, "real_estate": F, "banks": F, "nbfc": F, "oil_gas": F, "auto": F,
        "pharma": W, "it": W, "chemicals_fertilizers": W, "metals": W, "fmcg": M, "telecom": W},
       "A roughly month-long crash to 23 March 2020, then a stimulus-fed rebound led by pharma, IT, chemicals "
       "and later metals; travel and hospitality lagged for years."),
    # ---- financial and policy shocks ----
    _e("dotcom-2000", "Dot-com bust", "financial_crisis", (2000, 3, 10), (2002, 10, 9), "global", "medium",
       {"it": F, "telecom": F, "fmcg": W, "pharma": W, "banks": M},
       "Technology fell for over two years; defensives held up better; recovery began in 2003."),
    _e("gfc-2008", "Global financial crisis", "financial_crisis", (2008, 9, 15), (2009, 3, 9), "global", "high",
       {"banks": F, "nbfc": F, "real_estate": F, "metals": F, "capital_goods": F, "auto": F, "fmcg": W, "pharma": W,
        "it": M, "power": F},
       "Credit freeze and a deep drawdown to March 2009, then a violent rebound led by cyclicals."),
    _e("taper-2013", "US taper tantrum / rupee slide", "financial_crisis", (2013, 5, 22), (2013, 9, 5), "india", "medium",
       {"banks": F, "nbfc": F, "real_estate": F, "capital_goods": F, "it": W, "pharma": W, "textiles_exporters": W,
        "fmcg": W},
       "Rupee fell sharply; exporters with dollar earnings benefited, rate-sensitive and import-heavy sectors lagged."),
    _e("china-deval-2015", "China devaluation / commodity slump", "financial_crisis", (2015, 8, 11), (2016, 2, 29), "global", "medium",
       {"metals": F, "oil_gas": F, "capital_goods": F, "fmcg": W, "pharma": M, "it": M},
       "Commodity-linked sectors fell with Chinese demand fears; domestic consumption held better."),
    _e("demonetisation-2016", "Demonetisation", "policy_shock", (2016, 11, 8), (2017, 3, 31), "india", "medium",
       {"real_estate": F, "gold_jewelry": F, "consumer_discretionary": F, "auto": F, "nbfc": F, "banks": M,
        "fmcg": F},
       "Cash-heavy sectors slowed for a few quarters; markets recovered by early 2017 as liquidity returned."),
    _e("ilfs-2018", "IL&FS default / NBFC liquidity squeeze", "financial_crisis", (2018, 9, 21), (2019, 3, 31), "india", "high",
       {"nbfc": F, "real_estate": F, "banks": M, "auto": F, "fmcg": W},
       "Funding for NBFCs and housing finance dried up; weaker names never recovered, large ones took a year or more."),
    _e("trade-war-2018", "US-China trade war", "trade_war", (2018, 6, 15), (2019, 12, 13), "global", "low",
       {"metals": F, "it": M, "textiles_exporters": M, "chemicals_fertilizers": W},
       "Supply chains shifted; some Indian chemical and manufacturing names benefited from 'China plus one'."),
    _e("tariffs-2025", "US tariff announcements (April 2025)", "trade_war", (2025, 4, 2), (2025, 4, 30), "global", "low",
       {"it": F, "textiles_exporters": F, "pharma": M, "metals": F},
       "Sharp global fall then partial rebound as tariffs were paused or negotiated."),
    # ---- elections ----
    _e("election-2004", "2004 election surprise", "election", (2004, 5, 14), (2004, 5, 31), "india", "medium",
       {"banks": F, "capital_goods": F, "power": F, "fmcg": W, "it": W},
       "Circuit-breaker crash on 17 May 2004 when the result surprised; recovered over the following months."),
    _e("election-2024", "2024 election result", "election", (2024, 6, 3), (2024, 6, 14), "india", "medium",
       {"capital_goods": F, "power": F, "defense": F, "fmcg": W, "it": W},
       "Large one-day fall on 4 June 2024 when the majority came in below exit polls; recovered within days."),
    # ---- natural disasters ----
    _e("gujarat-quake-2001", "Gujarat earthquake", "earthquake_tsunami", (2001, 1, 26), (2001, 3, 31), "india", "low",
       {"cement_construction": W, "insurance": F},
       "Local; reconstruction demand helped cement and construction later."),
    _e("tsunami-2004", "Indian Ocean tsunami", "earthquake_tsunami", (2004, 12, 26), (2005, 2, 28), "asia", "low",
       {"insurance": F, "cement_construction": W},
       "Negligible index effect; coastal insurers and relief spending were the visible channels."),
    _e("japan-quake-2011", "Tohoku earthquake and tsunami", "earthquake_tsunami", (2011, 3, 11), (2011, 5, 31), "global", "medium",
       {"auto": F, "capital_goods": M, "cement_construction": W, "it": M},
       "Supply-chain disruption for autos and electronics; global markets recovered within weeks."),
    _e("mumbai-floods-2005", "Mumbai floods", "flood_cyclone", (2005, 7, 26), (2005, 8, 15), "india", "low",
       {"insurance": F, "real_estate": F, "auto": M}, "Local, short; claims and disrupted sales for one quarter."),
    _e("kerala-floods-2018", "Kerala floods", "flood_cyclone", (2018, 8, 8), (2018, 9, 30), "india", "low",
       {"insurance": F, "gold_jewelry": F, "banks": M}, "State-level; effect on regional lenders and retailers."),
    _e("amphan-2020", "Cyclone Amphan", "flood_cyclone", (2020, 5, 20), (2020, 6, 15), "india", "low",
       {"power": F, "insurance": F}, "Masked by COVID; local infrastructure damage."),
    # ---- weather: monsoon, heat, cold ----
    _e("monsoon-2002", "Weak monsoon 2002", "drought_weak_monsoon", (2002, 6, 1), (2002, 9, 30), "india", "low",
       {"agri_rural": F, "auto": F, "fmcg": F, "chemicals_fertilizers": F},
       "Rural demand slowed; markets were weak for other reasons too."),
    _e("monsoon-2009", "Drought 2009", "drought_weak_monsoon", (2009, 6, 1), (2009, 9, 30), "india", "medium",
       {"agri_rural": F, "chemicals_fertilizers": F, "fmcg": M, "banks": W},
       "Food inflation rose; the equity rally from the 2008 low overwhelmed the drought effect."),
    _e("monsoon-2014-15", "Two consecutive weak monsoons", "drought_weak_monsoon", (2014, 6, 1), (2015, 9, 30), "india", "medium",
       {"agri_rural": F, "auto": F, "fmcg": F, "chemicals_fertilizers": F, "banks": M},
       "Rural wages and tractor/two-wheeler demand softened; policy support partly offset the damage."),
    _e("monsoon-2023", "Uneven monsoon / driest August in decades (El Nino)", "drought_weak_monsoon", (2023, 6, 1), (2023, 9, 30), "india", "low",
       {"agri_rural": M, "fmcg": M, "chemicals_fertilizers": M},
       "Food prices spiked (rice, pulses, vegetables) and export curbs followed; the index kept rising."),
    _e("heat-2024", "Severe summer heatwave", "heat_cold_weather", (2024, 4, 1), (2024, 6, 15), "india", "low",
       {"consumer_discretionary": W, "power": W, "fmcg": M, "agri_rural": F},
       "Cooling products and power demand rose; crop damage and food inflation were the downside."),
    _e("unseasonal-rain-2023", "Unseasonal rain and hail on wheat", "heat_cold_weather", (2023, 3, 1), (2023, 4, 30), "india", "low",
       {"agri_rural": F, "fmcg": M}, "Crop-damage fears; limited index effect."),
)

ALIASES = {
    "bank": "banks", "banking": "banks", "psu_bank": "banks", "private_bank": "banks", "financials": "banks",
    "finance": "nbfc", "hfc": "nbfc", "tech": "it", "software": "it", "technology": "it", "healthcare": "pharma",
    "drug": "pharma", "consumer_staples": "fmcg", "staples": "fmcg", "automobile": "auto", "autos": "auto",
    "metal": "metals", "steel": "metals", "oil": "oil_gas", "oil_and_gas": "oil_gas", "energy": "oil_gas",
    "realty": "real_estate", "property": "real_estate", "cement": "cement_construction",
    "construction": "cement_construction", "infra": "cement_construction", "infrastructure": "cement_construction",
    "defence": "defense", "aviation": "airlines_travel", "airlines": "airlines_travel", "travel": "airlines_travel",
    "hotels": "hotels_leisure", "chemicals": "chemicals_fertilizers", "fertilizers": "chemicals_fertilizers",
    "fertilisers": "chemicals_fertilizers", "agri": "agri_rural", "agriculture": "agri_rural", "rural": "agri_rural",
    "consumer_discretionary": "consumer_discretionary", "discretionary": "consumer_discretionary",
    "jewellery": "gold_jewelry", "gold": "gold_jewelry", "shipping": "shipping_logistics",
    "logistics": "shipping_logistics", "textiles": "textiles_exporters", "capex": "capital_goods",
    "engineering": "capital_goods", "utilities": "power",
}


def categories() -> list[str]:
    return sorted({e.category for e in CATALOG})


def sector_key(name: str) -> str:
    k = name.strip().lower().replace("&", "and").replace("-", "_").replace(" ", "_")
    return ALIASES.get(k, k)


def known_sectors() -> list[str]:
    return sorted({s for e in CATALOG for s in e.effects})


def episodes(category: str | None = None, region: str | None = None) -> list[Episode]:
    cat = category.strip().lower().replace("-", "_").replace(" ", "_") if category else None
    if cat is not None and cat not in categories():
        raise ValueError(f"unknown event category {category!r}; choose from {', '.join(categories())}")
    return [e for e in CATALOG if (cat is None or e.category == cat) and (region is None or e.region in (region, "global"))]


@dataclass(frozen=True)
class SectorJudgment:
    sector: str
    stance: str  # "tended to hold up" | "tended to be hit" | "inconsistent" | "no precedent in catalog"
    score: float  # weighted mean of win=+1 / flop=-1 / mixed=0 across matching episodes
    n_episodes: int
    wins: int
    flops: int
    mixed: int
    caveat: str


def judge_sector(sector: str, eps: Sequence[Episode], threshold: float = 0.3) -> SectorJudgment:
    key = sector_key(sector)
    hits = [e for e in eps if key in e.effects]
    if not hits:
        return SectorJudgment(key, "no precedent in catalog", 0.0, 0, 0, 0, 0, "no matching episode; do not infer")
    weights = [CONF_WEIGHT[e.confidence] for e in hits]
    score = sum(SCORE[e.effects[key]] * w for e, w in zip(hits, weights, strict=True)) / sum(weights)
    wins, flops, mixed = (sum(e.effects[key] == k for e in hits) for k in (W, F, M))
    stance = "tended to hold up" if score >= threshold else "tended to be hit" if score <= -threshold else "inconsistent"
    if len(hits) < 3:
        caveat = f"only {len(hits)} episode(s): anecdote, not a pattern"
    elif wins and flops:
        caveat = "episodes disagree: the setup mattered more than the event"
    else:
        caveat = "consistent across episodes, but each had a different backdrop"
    return SectorJudgment(key, stance, score, len(hits), wins, flops, mixed, caveat)


def judge(sectors: Sequence[str], categories_: Sequence[str], region: str | None = None) -> list[SectorJudgment]:
    """Pool the episodes of every named category (simultaneous events, such as war plus weak monsoon) and judge each sector."""
    if not categories_:
        raise ValueError("name at least one event category")
    eps: list[Episode] = []
    for c in categories_:
        eps.extend(x for x in episodes(c, region) if x not in eps)
    return [judge_sector(s, eps) for s in sectors]


def measure_episode(series: Mapping[str, Mapping[date, float]], episode: Episode, after_days: int = 60) -> dict[str, dict[str, float | None]]:
    """Measure real sector behavior from daily return series ({sector: {day: return}}), as fractions.

    Per sector: `during_pct` = compounded return over the episode window, `after_pct` = compounded return over the
    `after_days` calendar days that follow. None where the series has no day in that window, so a gap is not read as calm.
    """
    after_end = episode.end + timedelta(days=after_days)

    def window(rets: Mapping[date, float], lo: date, hi: date) -> float | None:
        xs = [r for d, r in rets.items() if lo <= d <= hi]
        if not xs:
            return None
        if any(not math.isfinite(r) or r <= -1 for r in xs):
            raise ValueError("returns must be finite and above -100%")
        return (math.prod(1 + r for r in xs) - 1) * 100

    return {sector_key(s): {"during_pct": window(r, episode.start, episode.end),
                            "after_pct": window(r, episode.end + timedelta(days=1), after_end)}
            for s, r in series.items()}


def winners_and_flops(measured: Mapping[str, Mapping[str, float | None]], key: str = "during_pct") -> tuple[list[str], list[str]]:
    """Sectors ranked best-first and worst-first by the chosen measure; sectors with no data are left out."""
    rows = sorted(((v[key], s) for s, v in measured.items() if v.get(key) is not None), key=lambda t: (-t[0], t[1]))  # type: ignore[operator]
    return [s for _, s in rows], [s for _, s in reversed(rows)]
