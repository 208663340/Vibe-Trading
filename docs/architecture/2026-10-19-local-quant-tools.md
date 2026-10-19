# Local quant strategy tools

## Contract

Expose `list_local_quant_strategies`, `load_local_quant_strategy`, and
`backtest_local_quant_strategy` through the existing BaseTool registry. Discovery
and loading are readonly; backtesting is a bounded write operation. The agent
chooses a named strategy, an evaluation window, optional codes and a declarative
close-of-session screen. Return actual engine metrics and report artifact paths.

Use the operator's `VIBE_TRADING_QUANT_ROOT`, otherwise the checkout's sibling
`quant` directory (installed fallback: `~/.vibe-trading/quant`). Model arguments
cannot select Python files or data paths. Keep external quant files unchanged.
Read only known source files and `data/daily_from_minutes.csv`; reject symlink
escapes and unknown IDs. Source versions are saved with each run.

## Strategies and time

Adapt the feature-building functions from `simple_tactics_backtest.py` and
`simple_long_backtest.py`, replacing their CSV read with the supplied frame.
Keep actual source feature expressions and the turtle helper, remove the
forward-return bookkeeping (`next_open`, `oo`), and reject obvious future-data
operations. Do not import the scripts or execute their publishers/main functions.
Extracted feature code executes only in the existing Runner subprocess.

Support ma20, turtle20_10, momentum20_top30, limitup_nextday, ma60,
momentum60_top50 and lowvol60_top50. The monthly rules rebalance at observed
month ends; daily rules use each close. The auction strategy is discoverable
but explicitly unavailable to this close-only executor.

Read only the causal daily input columns. Future label columns never enter the
strategy frame. Screen by positive price/volume, optional trailing mean and
momentum thresholds, then rank and cap candidates. Apply strategy eligibility
inside those candidates. Falling out of the candidate basket removes the next
target at that strategy's next decision. Default universe is A-share main-board
codes; stock-name ST filtering uses the snapshot, not historical ST metadata.

Use the built-in ChinaAEngine with its next-bar-open signal shift, warm-up
boundary, cash accounting, market restrictions, lot sizing, fees and slippage.
Keep the initial capital baseline and real engine fills. Each run is one shared
portfolio, not an average of independently full-capital stock backtests. No live
orders or model/broker/network calls are needed.

Use the observed market calendar before narrowing the requested universe.
Reindex the execution clock without inventing OHLC: the local engine adapter
skips missing and zero-volume rows and retains valid valuation marks. Monthly
strategies initialize from the prior known target on the first evaluation open,
then execute only after month-end decisions. Blocked monthly instructions expire
on that session; daily strategies replan from the next close. Keep terminal
positions marked to market instead of inventing a forced final liquidation.

The main Agent and Swarm inject an internal run_dir not exposed to the model.
Accept that execution context separately from strict research parameters. Keep
the native application run, publish a unique report bundle inside the validated
invoking workspace, and return its readable artifact paths. Both default
Autonomous research peers receive the same tools and local strategy skill;
neither becomes a permanent controller. Record successful metric-bearing local
backtests in the existing grounding ledger.

## Artifacts and failures

Save config, extracted code, source snapshots/hashes, dataset hash/statistics,
candidate decisions, engine equity/trades/positions/metrics/run card, and a
Chinese Markdown + JSON summary. Mark success only after valid outputs exist.
Zero selections is a legitimate cash-only result, with no invented trades.
Invalid dates, parameters, unavailable source files, duplicates/bad prices,
missing required columns and unsupported strategies fail explicitly. Persist
Runner timeout/failure state and logs. Bound reply sizes and numeric inputs.

Reports distinguish requested/effective coverage, screen rules, strategy timing,
execution costs, initial/final equity, return, annual return, drawdown, Sharpe,
closed trade count and remaining holdings. State that price adjustment,
historical membership/ST/delistings and model-selection bias are not independently
validated; the report is historical evidence rather than verified profitability.

## Verification

Use real tiny CSVs and trusted strategy fixtures to prove source feature changes
affect signals; list/load never execute top-level code; future columns and future
price changes cannot change earlier decisions; screening precedes strategy
selection; fees, shared cash and next-open timing match hand-checked examples;
warm-up does not enter metrics; zero candidates, monthly holding, missing bars,
malformed input, symlinks, timeouts and tool autodiscovery are covered. Run a real
local-data example without editing quant sources, then backend regression suite.
