> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 1 independent review

- **Spec compliance: ❌ Needs fixes.** Same-store recovery is not held under execution ownership for the duration of its writes; concurrent constructors can produce duplicate terminal evidence. Fixed review range: `67b4ba05da9b89f6e70d37980e613edbc756ba6c..7f6f980e8f90660676d591eb6b019e5ef0a3d675`.
- **Task quality: Needs fixes.** One Important correctness defect is reproduced; no Critical findings.

## Strengths

- `agent/src/session/execution.py:91`, `:108`, `:130`: capacity is checked before admission side effects, and identity-based release checks actual worker futures, the execution continuation and terminal I/O future. This addresses cancelled-wrapper ownership rather than relying on Task cancellation alone.
- `agent/src/session/service.py:299`, `:491`: conditional cancellation compares identity under the gate; terminal persistence appends linked transcript evidence before updating the attempt, with optional projections isolated afterward.
- `agent/tests/test_session_execution_lifecycle.py:40`, `:133`, `:365`, `:402`, `:537`: regressions cover immediate cancellation, storage faults, delete/send ordering, stale callbacks and a slow supervised terminal future using isolated stores and controlled synchronization.

## Critical

- None found in the reviewed diff.

## Important

- **I1 — Recovery itself is an unregistered writer.** `agent/src/session/execution.py:80` checks only `previous.handles` before retiring the previous owner; `agent/src/session/service.py:88` then performs recovery outside that gate without an initializing/recovery claim. While constructor A is recovering, constructor B therefore sees A as idle, retires it and starts another recovery against the same directory. The message lock at `agent/src/session/store.py:43` belongs to each SessionStore object, so it cannot serialize those separate instances. If A pauses in `append_message` after the idempotency lookup at `agent/src/session/store.py:191`, B commits its recovered reply, then A appends its already-admitted candidate: two terminal records with the same message_id are persisted. This violates one same-store writer and exactly-once terminal recovery, and the retired constructor can keep writing while the replacement admits work. Keep initialization/recovery owned until completion (for example, register an initializing/recovery claim checked by replacement, or hold the ownership gate continuously across registration and recovery), including failure cleanup. Add a barrier regression using two SessionStore instances pointed at the same directory and require rejection/serialization plus one terminal record.
- **I1 reproduction:** `/tmp/vibe-task1-review-recovery/test_concurrent_recovery.py:46`; command `.venv/bin/python -m pytest -p agent.tests.conftest /tmp/vibe-task1-review-recovery/test_concurrent_recovery.py -q --tb=short -p no:cacheprovider` → **1 failed in 0.04s**, `recovery owner rejected=False; linked terminal replies=2`, with equal IDs. This was the only test run by this reviewer; providers and external accounts were not touched, and all data used pytest temporary directories and the repository sandbox plugin.

## Minor

- `.superpowers/sdd/2026-10-13-architecture-stability-upgrade/task-1-report.md:85`: the reported focused run contains 9 NumPy timedelta DeprecationWarnings attributed to `agent/backtest/loaders/ccxt_loader.py:39-47`. Record this test-output noise for the controller's scope decision against the 2,097-warning baseline; no suppression or unrelated loader change is requested for this task.

## Checks and cannot-verify items

- Read the provided fixed diff once, in nonoverlapping chunks. Did not rerun Git, inspect full changed files separately, modify production/test/index/HEAD state, or run the existing suite. Source `rg` checks were limited to exact line locations for already-reviewed symbols.
- Named focused outside-diff check: test-runtime isolation for the I1 reproduction. Read relevant `agent/tests/conftest.py` sandbox/bootstrap lines and loaded that existing plugin for the single temporary regression.
- Controller supplied subsequent Ruff verification; inspected `/tmp/vibe-upgrade-task1-ruff.log`: **All checks passed!** This resolves the implementation report's missing-Ruff concern for the six changed files without rerunning lint.
- ⚠️ Full backend/frontend/build/repository integration gates cannot be verified from this task diff; these remain controller-owned. The implementation report records 277 focused passes, but this review did not regenerate that suite.
- ⚠️ Runtime retention of an incomplete closing service/executor and event shutdown ordering are Task 2 integration responsibilities, not established by this diff. Detached provider/read fencing and kernel terminal arbitration remain Tasks 4–6; Task 1's ownership and close-summary interfaces do not alone verify them.
