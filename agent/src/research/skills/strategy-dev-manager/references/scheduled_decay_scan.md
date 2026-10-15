# Scheduled Decay Scan Configuration

## Overview

Periodic decay scanning ensures that factor performance degradation is detected early, before it silently erodes strategy returns. The `ScheduledResearchExecutor` provides a background polling loop that fires research prompts on a cron or interval schedule. By configuring a scheduled job with a decay-scan prompt, operators automate the `sdm_decay_scan` workflow without manual intervention.

Each scheduled fire creates a fresh agent session that runs the configured prompt through the standard tool registry. The agent calls `sdm_decay_scan`, evaluates decay signals against the thresholds defined in `decay_thresholds.md`, applies warranted state transitions, and produces a summary report.

## Configuration

### Enable the scheduler

The background executor is **off by default**. Start the server with the environment flag:

```bash
VIBE_TRADING_ENABLE_SCHEDULER=1 vibe-trading serve --port 8899
```

Without this flag, the `/scheduled-runs` endpoints still record jobs but nothing fires.

### Create a weekly decay scan job

```bash
curl -X POST http://localhost:8899/scheduled-runs \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Run a decay scan on all active factors in the CSI300 universe. For any factors showing decay signals, generate a summary report.",
    "schedule": "0 2 * * 1",
    "config": {}
  }'
```

The API returns a job record with `id`, `status`, `next_run_at`, and the full schedule definition. Jobs persist under `~/.vibe-trading/` and survive server restarts.

### Create a multi-universe scan

For operators tracking factors across multiple universes, create separate jobs per universe:

```bash
# CSI300 weekly scan
curl -X POST http://localhost:8899/scheduled-runs \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Run a decay scan on all active factors in the CSI300 universe. Generate a decay report with status transitions.",
    "schedule": "0 2 * * 1",
    "config": {}
  }'

# S&P 500 weekly scan (offset by one day)
curl -X POST http://localhost:8899/scheduled-runs \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Run a decay scan on all active factors in the SP500 universe. Generate a decay report with status transitions.",
    "schedule": "0 2 * * 2",
    "config": {}
  }'
```

### Authentication

When `API_AUTH_KEY` is configured, include the bearer token:

```bash
curl -X POST http://localhost:8899/scheduled-runs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-api-key>" \
  -d '{ ... }'
```

## Schedule Examples

The `schedule` field accepts two formats:

| Format | Example | Description |
|--------|---------|-------------|
| Interval (ms) | `"21600000"` | Every 6 hours (6 * 60 * 60 * 1000) |
| Cron (5-field) | `"0 2 * * 1"` | Monday at 02:00 UTC |

Cron fields: `minute hour day-of-month month day-of-week`. Each field accepts a number, `*` (any), or `*/n` (step).

### Common cron patterns for decay scanning

| Pattern | Schedule | Use case |
|---------|----------|----------|
| Weekly (Monday 2am UTC) | `0 2 * * 1` | Standard weekly scan for most factor portfolios |
| Monthly (1st, 3am UTC) | `0 3 1 * *` | Low-frequency monitoring for stable, long-horizon factors |
| Daily (midnight UTC) | `0 0 * * *` | High-frequency monitoring during volatile regimes |
| Bi-weekly (1st and 15th) | `0 2 1,15 * *` | Balanced cadence for mixed-horizon portfolios |
| Every 6 hours (interval) | `21600000` | Continuous monitoring for live-trading factor stacks |

## What Happens on Each Scan

When the executor fires a scheduled decay scan job, the agent session performs the following steps:

1. **Invoke `sdm_decay_scan`** -- Calls the decay scan tool with the configured universe (e.g., `sdm_decay_scan(universe="CSI300")`).

2. **Evaluate decay signals** -- For each active factor, computes rolling IC mean, IR, IC positive ratio, and Sharpe ratio. Compares each metric against the thresholds defined in `decay_thresholds.md`.

3. **Classify factor health** -- Assigns each factor to one of five categories:
   - **Healthy**: All metrics above healthy thresholds
   - **Warning**: At least one metric in the warning band
   - **Decayed**: At least one metric in the decayed band
   - **Critical**: At least one metric in the critical band
   - **Insufficient data**: Not enough bench history for reliable evaluation

4. **Apply state transitions** -- Factors meeting the consecutive-reading requirements (see `decay_thresholds.md` State Transition Rules) are transitioned to their next state (e.g., `active` to `monitoring`, `monitoring` to `decayed`).

5. **Generate a summary report** -- Produces a structured report with counts per category, a transitions table, and recommendations. See `templates/decay_report.md` for the report format.

## Monitoring Scan Results

### List scheduled jobs

```bash
curl http://localhost:8899/scheduled-runs
```

Returns all configured jobs with their current status, `last_run_at`, and `next_run_at` timestamps.

### Check scan history via the agent

After a scan completes, the agent stores results as artifacts. Query them with:

```
sdm_status(action="detail", artifact_id="<scan_artifact_id>")
```

This returns the full decay scan output including per-factor metrics, applied transitions, and the generated report.

### Cancel a scheduled job

```bash
curl -X DELETE http://localhost:8899/scheduled-runs/<job_id>
```

Cancelled jobs are marked terminal and never re-dispatched.

## Tuning Scan Frequency

Choose scan intervals based on the factor's `decay_horizon` metadata and the portfolio's sensitivity to performance degradation:

| Factor type | Recommended schedule | Rationale |
|-------------|---------------------|-----------|
| Short-horizon (holding period < 5 days) | Daily (`0 0 * * *`) | Decay can manifest within a single trading week |
| Medium-horizon (5-20 days) | Weekly (`0 2 * * 1`) | Balances detection speed with noise reduction |
| Long-horizon (> 20 days) | Monthly (`0 3 1 * *`) | Monthly rebalance cadence aligns with evaluation window |
| Live-trading factors | Every 6 hours (`21600000`) | Minimize exposure to degraded factors in production |

### General guidance

- **Avoid over-scanning**: Each scan consumes agent iterations and data-loader calls. Daily scans on a 500-factor universe are expensive; reserve them for live-trading portfolios.
- **Align with rebalance cycles**: A factor that rebalances monthly gains little from weekly scans -- the transition would not take effect until the next rebalance anyway.
- **Stagger multi-universe jobs**: Offset scan times across universes to avoid concurrent data-loader pressure on shared sources (e.g., tushare rate limits).
- **Monitor scan duration**: If a scan consistently takes longer than the interval between fires, the executor will queue dispatches. Increase the interval or narrow the universe.

## Job Lifecycle

```
POST /scheduled-runs
        |
        v
    [PENDING] --(executor tick, is_due=true)--> [RUNNING]
        |                                           |
        |                              dispatch success: [COMPLETED]
        |                              dispatch failure: [FAILED]
        |                                           |
        |                              next_run_at advanced, re-enters
        |                              PENDING for the next fire
        |
    DELETE /scheduled-runs/<id>
        |
        v
    [CANCELLED] (terminal, never re-dispatched)
```

Failed jobs are terminal. To retry, create a new job with the same prompt and schedule.
