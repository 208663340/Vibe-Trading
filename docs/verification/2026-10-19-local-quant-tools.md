# Local quant tools verification — 2026-10-19

Baseline: `42d9d61f` on `codex/autonomous-swarm`. External `../quant` files were read only.
Pre-existing custom-program and frontend edits are excluded from this feature.
Plan: `docs/superpowers/plans/2026-10-19-local-quant-tools.md`.
Spec: `docs/architecture/2026-10-19-local-quant-tools.md`.

## Delivered behavior

Three discovered BaseTools provide source discovery, loading and one shared-cash
portfolio backtest. Seven named source adapters are executable; auction-dependent
board_overnight is listed with an explicit unsupported status. Sources are
captured and hashed, their actual causal feature expressions execute in the
existing Runner, and publishers/top-level script code do not run. No new package
dependencies, external model calls or live orders are required.

The default Autonomous research peers can each choose and run a source strategy.
Internal Agent/Swarm run_dir injection is accepted without exposing data/source
paths as model parameters. Native runs remain available, and readable evidence
bundles are copied into the caller's confined workspace. Actual metrics also
register as completed analysis in GroundingLedger.

## Test development and review

- Catalog: 19 missing-behavior failures observed before implementation; source
  expression changes, unknown IDs, path escapes, forward operations and request
  validation covered.
- Pipeline/tools: missing implementation failures observed before implementation;
  real CSV fixtures and Runner subprocesses cover close screening, shared budget,
  fees, next-open execution, warm-up, no candidates, source capture, failures and
  timeout persistence.
- Explicit quarantine and independent calendar: three additional RED tests before
  implementation. No zero-price repair or future-survivor filtering introduced.
- Warning regression: missing Boolean features raised pandas FutureWarning under
  warnings-as-errors; explicit Boolean equality made that test GREEN.
- Autonomous preset exposure: RED for unavailable strategy tools; GREEN after the
  two research peers received tools/skill permissions.
- Worker context: three RED tests reproduced rejection of injected run_dir. The
  correction publishes reports inside the invoking workspace. A real worker loop
  with a scripted model lists, loads, backtests and reads the actual report.
- Grounding: the actual local metrics did not count as completed analysis; RED
  test became GREEN after registering metric-bearing successful results.

One fresh independent review examined the implementation. All four important
findings were reproduced before fixing them, in one fix pass:

| Finding | Reproduction | Corrected behavior |
|---|---|---|
| Missing reindexed bars abort native rebalance | Pending buy and held-position gap tests fail with invalid execution price | Preserve clock/valuation; skip execution on absent bars |
| Monthly targets resize daily | Fill dates appear between decisions | First evaluation initialization and next-session monthly mask only |
| Zero-volume bars receive fills | Pending-buy and pending-sell scenarios trade during a zero-volume session | Local execution gate blocks both directions |
| Empty-screen fallback has stale history | No-data error for stale first symbol and entirely halted requested universe | Preserve the last genuine historical mark; cash-only run succeeds |

No critical findings or deferred minor findings. No second review was requested.

## Real source-data run

Run: `20261019_122828_77_716d7b`.
Root: `/Users/will.he/.vibe-trading/runs/20261019_122828_77_716d7b`.

```json
{
  "strategy_id": "turtle20_10",
  "start_date": "2026-08-01",
  "end_date": "2026-10-14",
  "screen": {"min_close": 3, "ma_window": 20, "rank_by": "momentum20", "top_n": 50},
  "max_positions": 10,
  "initial_cash": 1000000,
  "data_quality": "quarantine"
}
```

| Item | Observed |
|---|---:|
| Effective dates | 2026-08-03–2026-10-14 |
| Final equity | 899,604.669677781 |
| Total return | -10.0395330322% |
| Maximum drawdown | -32.6756649128% |
| Sharpe | -0.9620262223 |
| Closed trades | 255 |
| Actual fill records | 498 |
| Final open symbols | 10 |
| Candidate rows / unique symbols | 2,350 / 402 |
| Invalid input bars quarantined | 10,629 |
| Missing/zero-volume execution decisions skipped | 10 |

Artifact assertions verified final equity matches the summary, selected target
counts do not exceed the configured limit, fills have positive prices and no
pre-evaluation timestamps, fill counts match, and excluded row counts match the
quarantine evidence. Original source data and strategy files were unchanged.
The first strict attempt correctly failed on invalid input rows. Earlier trial
results are superseded by the corrected run above. No parameter search was used
to turn a losing experiment into a positive result.

## Decisions and limits

Ruling: reuse native accounting and adapt only trusted source features — this
preserves auditable fills and shared cash; it does not promise equality with the
source scripts' approximate return arithmetic.

Ruling: make quarantine explicit, never fabricate replacement execution prices —
invalid rows stay auditable; exclusions still make the historical result sensitive
to input quality. The default remains strict.

Ruling: initialize a fresh evaluation account from previously known targets;
unfilled monthly instructions expire on their scheduled session — this defines
repeatable behavior, but a different retry/initialization policy would change returns.

Final: Ruling: historical adjustment, membership, ST/delisting metadata and
profitability are not independently validated in this feature — this is input
and research validation beyond the tool integration. If assumed accurate, the
reported strategy performance could be biased. Reports explicitly disclose this.

The source AST checks reject recognized future-data operations; they are not a
sandbox for hostile arbitrary Python. Models choose named adapters, not arbitrary
files. max_positions limits target selection; blocked exits can retain actual
positions. Native internal-basket reference metrics are not an independent market
benchmark. Scripted worker tests establish tool orchestration, not live-model
strategy-selection quality or investment profitability.

## Final checks

- New feature tests: 53 passed in 7.07 seconds.
- README/distribution count checks: 122 passed.
- Full suite after execution fixes, before the final context/grounding additions:
  18,169 passed, 84 skipped in 206.88 seconds.
- Final complete regression after context/grounding additions: 18,174 passed,
  84 skipped in 210.58 seconds (`.venv/bin/python -m pytest -q`).
- Scoped Ruff check and formatting check: passed; git diff --check: passed.

The earlier cold mixed registry subset exposed an existing order-dependent test
that assumes loaders were already eagerly imported. It passed in the complete
suite; registry production code and unrelated tests were not changed.
