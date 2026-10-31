# Testing

Last measured: 2026-10-16. Everything below uses synthetic data and local fakes; no real exchange file or live endpoint
has been tested (the build sandbox cannot reach NSE, BSE, HKEX or KRX).

## Layers

| Layer | Where | What it catches |
|---|---|---|
| Unit | `tests/test_*.py` | Behavior of each module on hand-built inputs |
| Smoke | `tests/test_smoke.py` | Every CLI command runs in a subprocess and exits cleanly; a command without a scenario fails the suite |
| Golden regression | `tests/test_golden.py`, `tests/golden/*.txt` | Unintended output changes. Refresh deliberately with `UPDATE_GOLDEN=1` and review the diff |
| Property-based | `tests/test_properties.py` (hypothesis) | Invariants over random inputs. `PROP_EXAMPLES=2000` for a deep run |
| Docs consistency | `tests/test_docs_consistency.py` | README commands and layout match the code |
| Boundary / mutation killers | `tests/test_boundaries*.py` | Exact thresholds, defaults and money arithmetic found weak by mutation testing |
| Static | `ruff`, `mypy` | Lint and types (`make lint`, `make types`) |
| Coverage | `make cov` | 95% branch floor |

`make check` runs the fast gates. `make mutate` runs `scripts/mutate.py`.

## Mutation testing

`scripts/mutate.py` applies one AST mutation at a time (comparison flips, constant changes, arithmetic swaps) to a copy of
the source and reruns only the test files that import that module. A surviving mutant means no test noticed the change.
Lines marked `# pragma: no mutate (reason)` are mutants proven equivalent (changing them cannot alter behavior).

- 14 core modules: about 98.6% to 100% killed, after the documented equivalents.
- Other 13 modules (ingest, pdf_extract, segmented, papertrade, scenarios, validation, backtest, markets, nse, workflow,
  breadth, universe, cues, models): baseline 63.5% (566 of 892) before round 3. Round 3 added `tests/test_boundaries2.py`
  (papertrade, ingest and others) but the rerun of those 13 modules has **not** been completed, so no new number is claimed.
  Known weak spots: defaults and constants in scenarios/validation/backtest, the MCP client's request ids and SSE parsing,
  breadth windows, cue thresholds.

## Defects the testing found

Swapped means in `beta`; CAGR overflow; duplicate ladder rungs; negative score for a contradictory Buy rating; a split
fixture that did not persist; float noise at the exact 30% KRX limit; `equity_curve` silently ignoring traded tickers with no
prices (now raises); CLI tracebacks on bad input; `markets-verify` ignoring the date in the file name.

## Limits

Synthetic data only; network paths are exercised against fakes; the ai-hedge-fund adapter test needs an external checkout;
mutation coverage covers only the modules listed above; passing tests show the code does what the tests say, not that the
signals make money.
