# Autonomous swarm: independent review and fix record

Date: 2026-10-19. Reviewer: fresh-context GPT-6 Astra, read-only whole-change review.
Scope: autonomous Swarm models, storage, runtime, preset, API/CLI integration,
tests and documentation. Existing dirty backtest/frontend work was excluded.

## Findings

No Critical findings. Three Important findings were reproduced before fixes:

1. Worker cancellation depended on lease renewal (10 seconds by default).
   Fix: track active attempt cancellation events and signal them on supervisor
   termination independently of the renewal interval. Preserve the physical
   admission slot until the actual Worker exits.
   Regression: `test_cancel_does_not_release_capacity_until_underlying_worker_exits`.
2. `list_runs()` read stale run.json for autonomous history.
   Fix: hydrate autonomous list entries from SQLite, matching detail reads.
   Regression: `test_history_listing_uses_authoritative_state_before_json_projection`.
3. SSE could observe terminal state after reading an earlier event batch and
   close without delivering committed terminal events.
   Fix: drain the durable event tail after observing immutable terminal state.
   Regression: `test_sse_drains_committed_terminal_events_before_done`.

All three regressions failed against the reviewed implementation, then passed
after one consolidated fix pass. The combined new tests and existing REST/SSE
contracts passed: **48 passed**. No Minor findings were deferred.
The final complete backend run passed **18,121 tests**, with **84 skips**.

A retained-callback lease capture was also independently reproduced and corrected
during review. Bind both callback and renewal closures to their attempt's lease,
completion event and cancellation event.
Regression: `test_late_worker_callback_cannot_borrow_a_later_tasks_lease` (RED→GREEN).

## Rulings on review boundaries

- Live model/provider availability, market evidence quality and investment validity
  were not evaluated. Real Worker integration was tested with the provider replaced;
  no live strategy-performance claim is made. Live deployments still need their
  ordinary provider/data configuration.
- Dynamic task generation and scientific acceptance remain future increments;
  preset-defined capability claiming is the agreed first release.
- SQLite bounds valid leases across processes. Each runtime bounds its physical
  workers; it cannot kill an expired worker in another process. Strict host-wide
  physical quotas require one runtime or an additional process-owner protocol.
- Existing unrelated user modifications remain outside the feature and review.

## Framework and workspace rulings

- Reuse existing Worker/Pi boundaries and SQLite instead of a second agent runtime.
  Tradeoff: the domain collaboration protocol remains maintained in this project.
- Native worktree creation succeeded, but sandbox writes were denied. Its unused
  checkout was archived. Implementation uses a local feature branch in the user's
  existing checkout; pre-existing changes remain untouched. This trades filesystem
  isolation for scoped file ownership and review.
- Recovery rejects a changed captured model/provider configuration. The operator
  must retry with a new run to change that configuration or reset the budget.
- Full-suite verification found an unchanged test still referencing the frontend
  module's former location. Correct its one path to the current module so the
  existing frontend/server message-limit contract remains testable. The failure
  was reproduced from exported base HEAD before this narrow test-only correction.
