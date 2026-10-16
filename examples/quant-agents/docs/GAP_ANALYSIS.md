# Gap analysis: our skill vs the leading public skills

Last reviewed: 2026-10-16. Method: inventoried 15 public repos (global, India, China, emerging markets), cloned
them read-only, and compared **skill names and their written descriptions** with what this project does. This is a
feature comparison, not a code audit: no repo's text or code was copied, licenses must be checked before any reuse,
and repo contents are treated as untrusted data. Star counts and "popularity" are deliberately not used as evidence of
quality; none of these projects publishes validated out-of-sample returns that I could find.

Repos tracked (`data/watchlist.json`): virattt/ai-hedge-fund, TauricResearch/TradingAgents,
AI4Finance-Foundation/FinRobot, tradermonty/claude-trading-skills (94 skills), himself65/finance-skills,
yennanliu/InvestSkill (34), xvary-research/claude-code-stock-analysis-skill, prof-little-bear/cc-equity-research,
quant-sentiment-ai/claude-equity-research, Jelly13124/stock-analyze-skills, gauss314/skills (emerging markets),
vishalmdi/india-equity-report-skill (India), spikeHongg/china-stock-research-skills, ZhuLinsen/daily_stock_analysis,
liuhao1024/open-skills.

## Where others were ahead, and what we did

| Capability seen elsewhere | Example (skill name) | Status here |
|---|---|---|
| Fundamental quality and forensic screens (Piotroski, Altman, Beneish) | InvestSkill `stock-eval`, gauss314 skills | **Added** `fundamentals.py`; wired into small-cap gates via `piotroski_f` / `altman_z` columns |
| Valuation: DCF, sensitivity, reverse DCF | InvestSkill `dcf-valuation`, `stock-valuation`; finance-skills `company-valuation` | **Added** `valuation.py` (reverse DCF = what growth the price already assumes) |
| Thesis tracker with invalidation triggers, pre-mortem | InvestSkill `thesis-tracker`; cc-equity-research | **Added** `thesis.py` (intact / review / broken; bear case required). Not yet: decision log, KPI history |
| Pre-trade discipline gate (planless, oversized, revenge, regime-blocked) | claude-trading-skills `pre-trade-discipline-gate` | **Added** `discipline.py` with a journal |
| Loss circuit breaker (losing streaks, weekly/monthly limits) | `drawdown-circuit-breaker` | **Added** (we previously had only a peak-drawdown halt) |
| Post-trade review of signals | `signal-postmortem`, `trade-performance-coach` | **Added** round-trip post-mortem (`paper-review`). Not yet: per-signal attribution and feedback into weights |
| Market posture / exposure ceiling | `exposure-coach` | **Added** `posture.py` and `max_invested_pct` in screening (heuristic, unvalidated) |
| Market breadth and sector strength | `market-breadth-analyzer`, `sector-analyst` | **Added** `breadth.py` from bhavcopy. Not yet: composite 0-100 score |
| Stress tests: named-crisis replay, correlation spike, risk budget | InvestSkill `risk-stress-test` | **Added** `replay_window`, `correlation_spike_vol`. Not yet: rate/USD/oil sensitivities, risk-budget score |
| Liquidity: price impact, Amihud | finance-skills `stock-liquidity` | **Added** `liquidity.py` (square-root impact, Amihud) |
| Staged entries (position ladder) | InvestSkill `position-ladder` | **Added** `ladder.py` (no lot accounting) |
| Risk-based sizing from a stop, Kelly | claude-trading-skills `position-sizer`, Jelly13124 | **Added** `risk_based_qty`, capped fractional `kelly_fraction` |
| Tax mechanics | InvestSkill `tax-lens` (US) | **Added** India estimator `tax.py`. Rates must be confirmed (news on 13 Oct 2026 mentions an FM statement on LTCG and STT). Not yet: lot selection, loss harvesting |
| Earnings and event calendars | `earnings-calendar`, `catalyst-calendar` | **Added** `events.py` caveat in screening (calendar data is supplied, not fetched) |

## Where we are ahead (keep)

Executable data-quality gates (two-source price check, source-tag audit), broker-call ingestion with explicit
rejection reasons, **point-in-time validation of whether broker targets predicted returns**, a deny-by-default
approval gate, NSE/BSE official-feed support with ISIN cross-check, segment-aware screening for small and micro caps,
paper trading judged against a benchmark after costs, and 140+ tests.

## Not adopted, on purpose

Social-media and chat readers (Twitter, Telegram, Discord), crypto and options-income strategies, US-only filings
(SEC EDGAR), and persona "debate" agents. They need data or licenses we do not have, or add opinion without
evidence. Revisit if a data source and a validation plan exist.

## Open gaps (next candidates)

1. Multi-market adapters: a bhavcopy-like loader for HKEX, KRX, SGX, B3 and JSE (the `Bar` model is already generic;
   needs per-exchange column maps, calendars, currency and corporate-action conventions).
2. Promoter and institutional activity for India: bulk/block deals, promoter holding changes, FII/DII flows.
3. Signal attribution so post-mortems can say which source (broker, segment, rule) was wrong.
4. Rate, USD and oil sensitivity in stress tests; a composite risk-budget score.
5. India-specific sector playbooks (banks and NBFCs need different quality measures than Altman or Beneish).
6. Validation of the posture and tone heuristics on real history (needs data the sandbox cannot download).

## How this stays current

`quant-agents skillwatch` compares each watched repo's HEAD and skill manifest with the last saved state
(`watch/state.json`) and writes `watch/digest-YYYY-MM-DD.md`: skills added, removed and edited, with names that match none of
our capability keywords flagged as possible gaps. A daily routine runs it. Adoption stays a human decision: check the
license, re-implement rather than copy, add tests, and update this table.
