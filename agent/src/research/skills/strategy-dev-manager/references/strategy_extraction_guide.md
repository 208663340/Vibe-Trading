# Strategy Extraction Guide

This guide supplements SKILL.md Phase 2 (EXTRACT) for strategy-type artifacts. It provides a structured methodology for extracting complete, implementable trading strategies from academic papers and research reports.

## 1. Strategy Components to Extract

A complete strategy consists of 5 components. Every extraction must identify or explicitly mark each as "not specified in paper."

### 1.1 Signal Logic

The core indicator or model that generates buy/sell signals.

- **Indicator type**: moving average, RSI, MACD, Bollinger Bands, custom formula, machine learning model
- **Mathematical formula**: preserve LaTeX notation from the paper
- **Input variables**: OHLCV columns, fundamental fields, alternative data
- **Lookback period**: number of bars or days used in computation
- **Signal output**: continuous score, binary flag, ranked cross-sectional value

### 1.2 Entry Rules

Conditions for opening a position.

- **Signal threshold**: the value at which the signal triggers entry (for example, RSI < 30)
- **Confirmation filters**: additional conditions that must be met (for example, volume > 2x average)
- **Timing rules**: specific days, times, or rebalance frequencies
- **Direction**: long-only, short-only, or long/short

### 1.3 Exit Rules

Conditions for closing a position.

- **Profit target**: close when unrealized PnL exceeds a threshold (for example, +5%)
- **Stop-loss**: close when unrealized loss exceeds a threshold (for example, -3%)
- **Time-based exit**: close after N bars regardless of PnL
- **Signal reversal**: close when the signal crosses back (for example, RSI > 70)
- **Trailing stop**: dynamic stop that follows price

### 1.4 Position Sizing

How much capital to allocate per trade.

- **Fixed fraction**: constant percentage of portfolio per position
- **Equal weight**: 1/N allocation across N selected instruments
- **Kelly criterion**: optimal bet size based on win rate and payoff ratio
- **Volatility targeting**: size inversely proportional to realized volatility
- **Maximum position**: cap on any single instrument's weight

### 1.5 Risk Management

Portfolio-level constraints beyond individual trade stops.

- **Maximum drawdown limit**: halt trading if portfolio drawdown exceeds threshold
- **Correlation constraints**: limit exposure to correlated instruments
- **Sector exposure caps**: maximum weight in any single sector
- **Leverage limits**: maximum gross or net exposure
- **Turnover constraints**: maximum rebalance turnover per period

## 2. Extraction Methodology

Follow these steps in order when extracting a strategy from a paper.

### Step 1: Identify the Strategy Type

Classify the strategy into one of the standard categories:

| Category | Description | Example Papers |
|----------|-------------|----------------|
| Trend-following | Ride persistent price trends | Moving average crossovers, breakout systems |
| Mean-reversion | Bet on price returning to a mean | RSI oversold/overbought, pairs trading |
| Momentum | Buy past winners, sell past losers | Jegadeesh and Titman (1993) |
| Carry | Earn yield differential | FX carry, bond carry |
| Value | Buy cheap, sell expensive | Fama-French (1993), Piotroski F-Score |
| Multi-factor | Combine multiple signals | Composite scoring models |

### Step 2: Extract the Signal Generation Logic

Locate the section of the paper that describes how signals are computed.

- Extract the exact mathematical formula (preserve LaTeX)
- Identify all input variables and their definitions
- Note the lookback period or window size
- Determine whether the signal is continuous or discrete
- Check whether the signal is cross-sectional (ranked across instruments) or time-series (absolute level)

### Step 3: Extract Entry Conditions

Find the rules that determine when to open a position.

- Identify the signal threshold value and its direction (above or below)
- Note any confirmation filters (volume, volatility, regime filters)
- Record timing rules (daily rebalance, weekly, monthly, event-driven)
- Determine if entry is immediate or requires confirmation over multiple bars

### Step 4: Extract Exit Conditions

Find the rules that determine when to close a position.

- Identify profit targets (fixed percentage, trailing, or signal-based)
- Identify stop-loss levels (fixed percentage, ATR-based, or time-based)
- Note any time-based exits (maximum holding period)
- Check for signal-reversal exits (signal crosses back through threshold)

### Step 5: Extract Position Sizing Rules

Find how the paper allocates capital to each trade.

- If the paper specifies a sizing method, extract the formula and parameters
- If the paper uses equal-weight portfolios, note the number of instruments
- If the paper uses volatility targeting, extract the target volatility and lookback
- If not specified, default to equal-weight (see Section 5)

### Step 6: Extract Risk Management Parameters

Find portfolio-level risk constraints.

- Maximum drawdown limits
- Sector or industry exposure caps
- Leverage constraints
- Correlation or concentration limits
- If not specified, apply conservative defaults (see Section 5)

### Step 7: Identify Required Data Inputs

List all data columns needed to implement the strategy.

- **OHLCV**: open, high, low, close, volume (always required)
- **Fundamental fields**: earnings, book value, revenue (for value/quality strategies)
- **Alternative data**: sentiment, options flow, macro indicators
- Verify that the required columns are available in the target universe's data loader

### Step 8: Map to SignalEngine Template Parameters

Translate the extracted components into the `strategy_signal_engine.py` template parameters.

| Extracted Component | SignalEngine Parameter | Notes |
|---------------------|----------------------|-------|
| Signal threshold for entry | `entry_threshold` | Normalize to [0, 1] range where possible |
| Signal threshold for exit | `exit_threshold` | May be negative for reversal exits |
| Stop-loss level | `stop_loss` | Expressed as negative decimal (for example, -0.05 for 5%) |
| Lookback window | `lookback` | Integer number of bars |
| Maximum position weight | `max_position` | 1.0 for full position, fraction for partial |

## 3. Common Strategy Patterns

Reference table mapping typical paper descriptions to SignalEngine parameters.

| Paper Description | SignalEngine Parameter | Example Value |
|-------------------|----------------------|---------------|
| "buy when RSI < 30" | `entry_threshold` | 0.3 (signal = -RSI/100, enter when signal < -0.3) |
| "sell when RSI > 70" | `exit_threshold` | -0.7 (exit when signal > 0.7, or -(-0.7)) |
| "sell when profit > 5%" | profit target in exit logic | 0.05 |
| "stop loss at 3%" | `stop_loss` | -0.03 |
| "20-day lookback" | `lookback` | 20 |
| "equal weight portfolio of 10 stocks" | `max_position` | 0.1 (1.0 / 10) |
| "rebalance monthly" | timing rule in generate() | ~21 trading days |
| "volatility target of 15%" | volatility scaling factor | 0.15 / realized_vol |
| "buy when MA20 > MA50" | dual-MA crossover logic | lookback=20 and lookback=50 |
| "hold for 5 days" | time-based exit | 5 bars |
| "trailing stop at 2x ATR" | dynamic stop in exit logic | 2.0 * ATR(14) |

## 4. Quality Checks

Before registering a strategy artifact, verify all of the following.

### 4.1 Completeness

- [ ] All 5 components identified (Signal Logic, Entry Rules, Exit Rules, Position Sizing, Risk Management)
- [ ] Components not found in the paper are explicitly marked "not specified in paper" with defaults applied
- [ ] Required data columns are listed and available in the target universe

### 4.2 Implementability

- [ ] Entry rules are unambiguous: a clear boolean condition that can be coded
- [ ] Exit rules are unambiguous: at least one exit condition is defined
- [ ] Signal formula uses only data available at time T (no look-ahead bias)
- [ ] All numeric thresholds have concrete values (not "approximately" or "around")

### 4.3 Data Availability

- [ ] Required OHLCV columns are available in the target universe's loader
- [ ] Fundamental fields (if needed) are available through the configured provider
- [ ] The lookback period does not exceed the available history length

### 4.4 Look-Ahead Bias Check

- [ ] Signal values on day T use only data from day T and earlier
- [ ] Returns are computed from T+1 onward (never same-day)
- [ ] No negative shifts or forward-looking references in the formula
- [ ] The `delta(df, d)` operator uses `d >= 1`

### 4.5 Deduplication

- [ ] Check `sdm_status(action="list")` for existing strategies with similar logic
- [ ] If Pearson correlation between the new strategy's signals and an existing strategy exceeds 0.99, treat as duplicate
- [ ] Correlation between 0.90 and 0.99 may be a variant worth keeping with a note

## 5. Handling Incomplete Strategies

Academic papers frequently omit one or more strategy components. Apply the following defaults and always document them in the artifact's `signal_definition` field.

### 5.1 Missing Position Sizing

**Default**: Equal-weight allocation across all selected instruments.

```
max_position = 1.0 / n_stocks
```

If the number of stocks is not specified, use `max_position = 1.0` (full position per instrument) and note the assumption.

### 5.2 Missing Risk Management

**Default**: Conservative risk limits.

| Parameter | Default Value | Rationale |
|-----------|--------------|-----------|
| Stop-loss | -5% per trade | Limits single-trade loss |
| Maximum drawdown | 20% portfolio-level | Halts trading during severe drawdowns |
| Maximum position | 10% per instrument | Prevents concentration |
| Leverage | 1.0x (no leverage) | Avoids amplified losses |

### 5.3 Missing Exit Rules

**Default**: Signal reversal exit.

Close the position when the entry signal reverses direction (for example, if entry was triggered by signal < -threshold, exit when signal > +threshold). If no clear reversal is defined, use a time-based exit of 20 trading days.

### 5.4 Missing Entry Timing

**Default**: Daily evaluation with immediate entry when conditions are met.

If the paper does not specify rebalance frequency, evaluate entry conditions at the close of each trading day and enter at the next day's open.

### 5.5 Missing Stop-Loss

**Default**: -5% fixed stop-loss from entry price.

This is a safety net. Document that the paper did not specify a stop-loss and the default was applied.

### 5.6 Documentation Requirement

Every default applied must be recorded in the artifact registration:

```
signal_definition: "Mean-reversion strategy from Avramov & Chordia (2006).
  Entry: normalized return < -0.3. Exit: normalized return > 0.1 or stop-loss.
  Defaults applied: stop-loss=-5% (not in paper), position sizing=equal-weight (not in paper)."
```

## 6. Extraction Output Format

The final extraction output should follow this structure for registration via `sdm_register`:

```
artifact_type: strategy
name: <short_identifier>
universe: <target_market>
columns_required:
  - open
  - high
  - low
  - close
  - volume
  - <additional columns>
entry_rules: <clear boolean condition>
exit_rules: <clear boolean condition or list of conditions>
position_sizing: <method and parameters>
risk_management: <constraints or "defaults applied: ...">
signal_definition: <full description including defaults>
source_paper: <paper title and authors>
```
