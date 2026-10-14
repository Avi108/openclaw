# quant-agents

Agent workflows for equity research: turn messy broker calls into a risk-checked, human-approved list of order proposals. Pure Python standard library, 52 tests, no network access.

## Why this exists

Public broker-call roundups are noisy. Building a research agent on top of them surfaced real data problems, and each one became a design decision here:

| Problem seen in real data | How the pipeline handles it |
|---|---|
| A target of Rs 377 against a price of Rs 138 (about 170% "upside") looks like a unit or price mismatch | Medians above 120% upside are flagged `implausible-upside` and score 0 |
| One broker's headline number gets treated as "the market view" | One vote per broker (latest call wins); `single-broker` flag; score weights breadth and agreement |
| Calls with no price printed | `no-price` flag; never guessed |
| Mixed Buy/Sell views behind a high average | `has-sell` and `high-dispersion` flags; the analyst step vetoes them |
| Stale articles recycled as news | Freshness filter before scoring |
| Agents that can trade by accident | Pipeline only *proposes*; approval gate denies by default |

## Architecture

```mermaid
flowchart LR
    A[raw broker calls] --> B[ingest: parse + reject with reasons]
    B --> C[freshness filter]
    C --> D[consensus: 1 vote per broker, flags, score]
    D --> E[screen: min upside, no flagged rows]
    E --> F[Analyst: rules or LLM, veto only]
    F --> G[size: min of order / position / cash / volatility caps]
    G --> H[risk check: all violations reported]
    H --> I{human approval}
    I -- approved --> J[Broker.place_limit_buy]
    I -- denied / default --> K[no order]
    B & C & D & E & F & G & H & I -.-> L[(JSONL audit log)]
```

Key rules, enforced by tests:
- An analyst (including an LLM-backed one) can veto a candidate but cannot raise a size.
- Sizing returns the smallest applicable cap, so no heuristic can exceed a limit.
- The backtest signal sees only prior-day prices (no look-ahead), and costs are charged on exposure changes.
- Capital is reserved across proposals in one run, so later proposals see less cash.

## Try it

```bash
pip install -e ".[dev]"
pytest                                   # 52 tests
quant-agents consensus data/sample_broker_calls.txt --today 2026-10-14
quant-agents propose   data/sample_broker_calls.txt --today 2026-10-14 --audit audit.jsonl
quant-agents backtest                    # seeded synthetic prices
```

Output on the bundled **synthetic** sample (fictional companies):

```
ticker     brokers  buy%  upside%   score  flags
ALPHAPWR         3   100       71    51.2
BETAHOME         2   100       53    38.9
GAMMAHOT         1   100       51    31.8  single-broker
EPSILONBK        2    50        2     0.5  has-sell
DELTAFIN         1   100      174     0.0  single-broker,implausible-upside
ZETAMOTO         1   100      n/a     0.0  single-broker,no-price
```

Only `ALPHAPWR` and `BETAHOME` become proposals, each capped at 5% of a 3,800 portfolio.

## Use it on real broker reports (Indian market)

Download research PDFs from any free source (broker sites, Economic Times, Moneycontrol, exchange filings) into one folder, named `TICKER__BROKER__YYYY-MM-DD.pdf`, then:

```bash
pip install -e ".[pdf]"
quant-agents extract reports/ > calls.txt     # refuses ambiguous reports and says why (stderr)
quant-agents consensus calls.txt              # flags, scores
quant-agents propose   calls.txt --audit audit.jsonl
```

The extractor reads only the first pages, understands Indian wording (Accumulate, Add, Reduce, Neutral), and refuses reports with conflicting ratings or multiple targets. An ambiguous price is left unknown, never guessed.

## Do broker targets actually predict returns? (`validate`)

Given past calls and a price CSV (`ticker,date,close`), `quant-agents validate calls.txt prices.csv --horizon 180` enters the day *after* publication, scores only finished windows, and reports hit rate, how much targets overstated realized returns, rank IC, a permutation p-value, and realized return by implied-upside tercile.

Harness self-check on **simulated** worlds (150 stocks, 180-day horizon), which shows the method works and says nothing about real brokers:

| World | mean implied | mean realized | hit rate | rank IC | p-value |
|---|---|---|---|---|---|
| targets are noise | 40.9% | 2.7% | 22% | +0.054 | 0.254 (not significant) |
| planted edge | 40.9% | 28.0% | 43% | +0.431 | 0.000 |

Unit tests also check there is no look-ahead (a price spike on the publication day is ignored) and that the false-positive rate on noise stays near the nominal level.

## Plug into AI Hedge Fund

`integrations/ai_hedge_fund/india_broker.py` adds an `india_broker` analyst to [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) (MIT) through its own `AlphaModel` interface and registry, with no fork needed. It is point-in-time, uses one vote per broker, returns a bounded conviction in [-1, 1], and abstains on thin or implausible data.

```bash
export QUANT_AGENTS_CALLS=calls.txt
PYTHONPATH=src:integrations/ai_hedge_fund:/path/to/ai-hedge-fund python -c "import india_broker; india_broker.register()"
```

Tested against the fund's real `AlphaModel` and `Signal` classes (fund checkout at commit `78b779c`).

## Prior art and what was reused

Surveyed public GitHub projects before extending this one:

| Project | What it is | Used here |
|---|---|---|
| [vishalmdi/india-equity-report-skill](https://github.com/vishalmdi/india-equity-report-skill) (MIT, ~25 stars) | Prompt-only skill for NSE/BSE reports | The *discipline*, rewritten as code: two-source price cross-check, staleness limits, and a `[Source: ...]` coverage audit (`quality.py`). No text copied. Its benchmark is a self-run qualitative comparison, so its claims are not independently validated. |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | Investor-persona agents with a risk and portfolio manager (very widely starred) | Reviewed as a design reference for the analyst/risk/portfolio split only. US-focused; its license and code were not audited here. |
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Debate-style multi-agent trading framework | Reference only; not audited here. |

`skills/india-equity-check/SKILL.md` packages the workflow as a Claude skill that calls these tools instead of trusting free-text numbers.

## Limits (read before trusting any number)

- The sample data and the backtest prices are synthetic. The backtest demonstrates a correct engine, not a trading edge, and says so in its output.
- The consensus score is a transparent heuristic, not a fitted model. It has not been validated against realized returns; doing that needs a history of calls and outcomes.
- Broker targets are typically 12-month views and are often wrong. This is research tooling, not financial advice.
- `Broker` is an interface only. No brokerage integration is included.

## Layout

```
src/quant_agents/
  models.py     dataclasses (BrokerCall, ConsensusRow)
  ingest.py     parser with explicit rejection reasons
  consensus.py  aggregation, flags, scoring
  risk.py       policy, sizing, order checks
  quality.py    price cross-check, staleness, source-tag audit
  pdf_extract.py  conservative broker-report extractor (text/PDF)
  validation.py   point-in-time evaluation: do targets predict returns?
  backtest.py   engine + metrics
  workflow.py   pipeline, approval gate, audit log
  cli.py        command-line entry point
integrations/   AI Hedge Fund adapter
skills/         Claude skill wrapping the workflow
tests/          unit + workflow tests
```

## Next steps

- Run `validate` on a real history of Indian broker calls (hundreds of calls across at least a year, adjusted prices) to learn whether consensus score has any predictive value. Not yet done: this needs data the sandbox cannot download.
- LLM-backed `Analyst` that summarizes the thesis behind each call, with the veto-only constraint kept.
- Adapter for a real brokerage API behind the `Broker` interface, with order-status reconciliation.
