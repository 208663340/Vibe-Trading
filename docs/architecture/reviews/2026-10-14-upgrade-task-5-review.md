> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

### Spec Compliance

- ❌ Issues found: cancellation does not guard the actual readonly implementation dispatch. `agent/src/agent/tool_execution.py:154` checks before the argument/context snapshot, but the worker at `:169` checks only its private revoked event. A cancellation accepted during snapshot or before worker scheduling can therefore launch a previously unstarted tool. This misses Task 5's cancelled-work admission requirement.
- ✅ The requested execution/replay modules, AgentLoop facade integration, narrowly scoped runtime counter handoff, and both new regression suites are present. No new production dependency, public tool schema, database, or framework appears in the reviewed diff (`agent/src/agent/tool_execution.py:21`, `agent/src/agent/tool_replay.py:30`, `agent/src/api/runtime.py:67`).
- ⚠️ Cannot verify from this task diff: Task 6 terminal/watchdog ownership and request/model budget semantics remain explicitly deferred (`task-5-report.md:198`; `agent/src/agent/loop.py:1711`). Task 7 full integration and complete backend/frontend regression remain controller work; this review does not claim those gates passed.

### Strengths

- `agent/src/agent/tool_execution.py:49` and `:166`: process-default leases persist across invokers and are released by producer cleanup, while caller cancellation retains occupied capacity. `agent/tests/test_tool_execution_lifecycle.py:185` exercises 32 real blocked fake readers across loop/runtime instances and explicitly joins those workers.
- `agent/src/agent/tool_execution.py:354`: active and retired producers are counted separately from settled results, maintaining the eight-producer batch bound after timeout; `agent/tests/test_tool_execution_lifecycle.py:346` verifies queued calls receive capacity outcomes while eight actual producers remain blocked.
- `agent/src/agent/tool_execution.py:94` and `:208`: reader progress has a bounded deque and caller-owned delivery, including a final drain after a blocked callback. `agent/tests/test_tool_execution_lifecycle.py:377` exercises a 10,000-event flood and asserts both bounded delivery and caller thread identity.
- `agent/src/agent/tool_execution.py:309` and `agent/src/agent/tool_replay.py:69`: writes remain caller-owned and mutable replay invalidation occurs immediately before execution after the last cancellation check. The new write-barrier tests cover partial errors/exceptions, denied writes, persistence failure, and deterministic-cache retention (`agent/tests/test_tool_replay_write_barriers.py:43`, `:68`, `:103`, `:127`).
- `agent/src/agent/loop.py:2063` and `:2104`: executed raw results reach grounding before truncation/redaction at ordered caller commit. Synthetic cache/replay retain the existing canonical evidence path at `:1611`; the review applies the controller's explicit no-alias-migration ruling. `agent/tests/test_tool_execution_lifecycle.py:278` verifies caller identity, raw ingestion, and ordered transcript/trace output.
- `agent/src/agent/loop.py:1705` and `:1723`: existing invocation/batch helper seams remain available, and the replay policy uses the existing canonical key builder. `agent/src/api/runtime.py:67` refreshes the reader count without changing writer/finalizer ownership; all `aclose` return paths use that snapshot (`:84`).

### Issues

#### Critical (Must Fix)

- None found in the Task 5 scope.

#### Important (Should Fix)

- **Reject cancelled readers at actual dispatch.** `agent/src/agent/tool_execution.py:163`–`:171`: the only cancellation check is before potentially lengthy `deepcopy`, context capture, and thread scheduling. `reader.revoked` is only set later by caller polling, so a worker can begin registry execution even though the shared cancel signal is already set. A focused fake probe set cancellation during `__deepcopy__`; actual output was `{'effects': ['read'], 'outcome': 'tool_cancelled', 'executed': True}`, and the assertion that no implementation ran failed (exit 1). This wastes external work after stop admission and violates the stated guarantee. Recheck the captured stop predicate at the worker's dispatch boundary before setting `executed` and calling the implementation, preserve permit cleanup, and add a barrier-controlled regression for cancellation between initial admission and actual worker dispatch. Existing `agent/tests/test_tool_execution_lifecycle.py:83` covers the ninth queued call, but not this admitted-yet-unstarted window.

#### Minor (Nice to Have)

- `agent/backtest/loaders/ccxt_loader.py:39`–`:47`: the recorded expanded covering run emits nine NumPy generic-timedelta deprecation warnings. The saved log confirms the noise; the worker identifies it as pre-existing (`task-5-report.md:163`). Track separately from this task rather than filtering warnings or expanding Task 5 to unrelated loader changes.

### Checks and Evidence

- Read the supplied brief, report, current design, and complete packaged diff for base `a422e87dbb54fc6c8a191b47003c1f3886493457` to `83a84ec5` (production commit `784c39d7`). The initial combined tool output was truncated; recovered the package in bounded sequential chunks rather than issuing Git commands.
- Named-risk focused inspection: the diff cuts off `_finalize_tool_result` mid-function, so inspected the complete commit boundary to assess raw grounding and cache mutation. Also checked the adjacent unchanged lost-result/unblock helpers and existing replay test fixture to determine whether write invalidation could conflict with the success gate. Lost readonly results remove their `_called_ok` gate before marking replay-ready (`agent/src/agent/loop.py:1978`); no additional finding on that path.
- Named-risk focused inspection: read unchanged `RuntimeServices.aclose` return paths (`agent/src/api/runtime.py:84`) to verify the new counter snapshot also runs for repeated close and foreign-owner returns.
- Read `/tmp/task5-final-green2.log`: recorded result is **467 passed, 9 warnings in 9.46s**. Did not rerun the covering suites. Baseline RED and additional iteration results are documented at `task-5-report.md:103`; historical ordering/sign-off was not independently established through Git inspection.
- Ran two bounded, local fake-operation probes only: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=agent .venv/bin/python -` with a `PreparedToolCall` whose snapshot sets a cancellation Event, a private capacity-one budget, and a recording fake execute callback. First interleaving: initial `should_cancel=False` → argument snapshot sets cancel → worker sees `revoked=False` → registry callback records an effect → caller returns cancellation. Result: **exit 1**. Second interleaving: a patched thread-start seam starts a worker paused behind Events → cancellation is set while the worker is admitted but has not dispatched → worker is released and joined before caller polling resumes. The registry callback again records an effect and the returned result is `tool_cancelled, executed=True`; the no-invocation assertion again fails (**exit 1**). Both probes measure actual registry callback invocation, not only the executed flag, and reproduce the Important issue above. No network, live tools, accounts, production/test edits, index/HEAD mutation, or suite rerun.

### Assessment

**Task quality: Needs fixes.**

The separation of resource ownership, replay policy, and ordered commits is coherent and the regression coverage is substantial. The actual-dispatch cancellation gap must be closed before Task 5 can satisfy its admission guarantee; terminal/model-budget work remains with Task 6.
