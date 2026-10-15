# Decay Monitoring Thresholds

## State Machine

```
created → benching → active → monitoring → decayed → disabled
                        ↑           │
                        └───────────┘ (recovery: IC ratio > 0.7)
```

## Decay Signal Evaluation

Each metric is evaluated independently. The **worst** signal across all provided metrics determines the overall decay signal.

| Metric | Healthy | Warning | Decayed | Critical |
|--------|---------|---------|---------|----------|
| IC Ratio (rolling/baseline) | > 0.7 | 0.5 – 0.7 | 0.3 – 0.5 | < 0.3 |
| IR (rolling window) | > 1.0 | 0.5 – 1.0 | 0.1 – 0.5 | < 0.1 |
| IC Positive Ratio | > 55% | 45% – 55% | 35% – 45% | < 35% |
| Sharpe (strategy only) | > 1.0 | 0.5 – 1.0 | 0.0 – 0.5 | < 0.0 |

## State Transition Rules

| Transition | Condition | Consecutive Requirement |
|------------|-----------|------------------------|
| active → monitoring | WARNING or worse | 3 consecutive periodic benches |
| monitoring → decayed | DECAYED or worse | 2 additional consecutive checks |
| monitoring → active | HEALTHY | 1 reading (recovery) |
| decayed → disabled | CRITICAL | 3 monitoring periods |
| disabled → active | Manual enable | — |

## Trigger Modes

1. **Manual**: Agent calls `sdm_decay_scan(universe=...)` tool
2. **Scheduled**: `ScheduledResearchExecutor` configured with periodic scan job
3. **Passive**: Each `alpha_bench` run updates the scanned factor's decay snapshot

## Baseline Calculation

- Baseline IC mean = average of all `bench_history` entries with `bench_type='initial'` or the first 5 `periodic` entries
- Rolling IC mean = average of the most recent N entries (configurable via `rolling_window_days`)
- IC ratio = rolling_ic_mean / baseline_ic_mean

## Configuration

All thresholds are configurable via `DecayThresholds` dataclass in `src/research/strategy_store/decay.py`.
