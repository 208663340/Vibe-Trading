# Architecture stability upgrade: delivery and verification

Date: 2026-10-14. Branch: `codex/architecture-stability-upgrade`.
Sanitized base: `c25fea2bc261a2d0ed971c989d194de9f04ec963`.
Verified code revision: `3fab9117dce6f5b0631fc2ffdb7d1c69743a45b2`.

## Delivery

All seven plan tasks are complete. Frontend, backend and Agent audits informed the
design; each implementation task had a separate implementer and independent reviewer.
Production edits were sequential. Review findings received focused regressions and
scoped re-review. One complete branch review, one consolidated final fix and one
scoped final review closed the remaining two frontend defects. No open
Critical/Important finding remains in the reviewed upgrade scope.

The branch remains local and unmerged, as specified by Task 7. The existing checkout
is a normal repository, with no task-owned worktree to remove. No upgrade branch was
pushed, deployed or released. No real provider, brokerage, credentials or trading
flow was used in regression tests.

## Resulting boundaries

| Area | Owner/module | Observable behavior |
|---|---|---|
| Session execution | `agent/src/session/execution.py`, `service.py` | One attempt owns actual execution and finalization until both exit. Immediate cancellation and failed finalization cannot strand or prematurely release it. Active deletion returns 409; admission is bounded to 16 attempts by default. |
| Durable outcomes | Session store/service and search projection | Stable linked terminal replies are persisted before optional projections. Recovery adopts committed evidence once; indexing/notification failures do not downgrade it. Partial JSONL tails are repaired. |
| API lifecycle | `agent/src/api/runtime.py` | Stops inbound work before session/resource shutdown, owns a four-worker executor and reports incomplete close honestly. A closing same-store owner is retained until actual writers exit. |
| SSE | `agent/src/session/events.py`, `frontend/src/hooks/useSSE.ts` | Bounded ingress, delivery and replay retain independent terminal/gap/close controls. Resync triggers authoritative reads and replacement transport. ID-less controls survive inherited browser Last-Event-ID and do not acknowledge unseen ordinary events. |
| Web state | `features/agent/sessionRuntime.ts`, `history.ts`, store and Agent leaves | View/operation/attempt guards fence stale reads, sends and cancellation. Durable entities reconcile once with revision checks in both report-read orderings. Base history renders before bounded enrichment. Stream updates subscribe at leaves. |
| Providers | `agent/src/providers/call_runner.py` | Bounded producer waiting and late-result fencing work even when an SDK iterator/invoke blocks. Default process capacity is eight, held through actual producer/client cleanup. Context and provider metadata are preserved. |
| Tools and replay | `agent/src/agent/tool_execution.py`, loop dispatch | Process read capacity 32 and physical per-batch capacity eight include abandoned live readers. Fast completions are observed promptly; transcript/trace/raw grounding commits remain ordered. Executed writes invalidate mutable replay at dispatch; running writes are awaited. |
| Run lifecycle | `agent/src/agent/run_lifecycle.py`, `context_budget.py`, `core/state.py` | One main-owned terminal transition, serialized cancellation handoff and unconditional cleanup. Usage is anchored to actual request shape. State JSON uses flushed/fsynced temporary content and atomic replacement. |

Public API, AgentLoop/ChatLLM facades, tool schemas and existing event names remain
compatible. Additive contracts are authenticated attempt snapshots, optional
`expected_attempt_id` cancellation and `session.resync`. Cancellation acknowledgement
means accepted intent; terminal SSE or an authoritative snapshot confirms completion.
The UI keeps busy ownership until that confirmation and never automatically resends a
user mutation during resync.

## Integration evidence

The controller inspected the actual command results and exit codes. Focused task runs
overlap and are not added to full-suite totals. Python runtime was CPython 3.12.13.
Existing CI-declared optional test dependencies were installed in the project `.venv`;
production dependency declarations were unchanged. `pip check` passed.

| Check | Result / exit code | Revision and local log |
|---|---|---|
| Complete non-live backend baseline | 17,890 passed / 85 skipped / 2,097 warnings; 210.48s; exit 0 | Sanitized base before production edits; `/private/tmp/vibe-upgrade-backend-baseline-complete.log` |
| Complete frontend baseline | 77 files / 744 tests passed; exit 0 | Before frontend edits; `/private/tmp/vibe-upgrade-frontend-baseline.log` |
| Complete non-live backend integration | **18,026 passed / 85 skipped / 2,097 warnings**; 218.50s; exit 0 | `8a745c4072a5cec58b3366fc72baa43b7c8d79d6`; `/private/tmp/vibe-upgrade-backend-integration-initial.log` |
| Final complete frontend | **80 files / 787 tests passed**; 22.02s; exit 0 | `3fab9117`; `/private/tmp/vibe-upgrade-frontend-final.log` |
| Final production build (`tsc -b && vite build`) | **PASS**; Vite 1.08s; exit 0 | `3fab9117`; `/private/tmp/vibe-upgrade-frontend-build-final.log` |
| CI environment gate tests | 23 passed; 0.03s; exit 0 | Integrated backend; `/private/tmp/vibe-upgrade-ci-env-tests.log` |
| Repository grep gates | All six PASS; exit 0 | Final code; `/private/tmp/vibe-upgrade-ci-gates-final.log` |
| Changed Python Ruff, AST and compilation | 31 files PASS; exit 0 | Python 3.11 syntax checked with `ast.parse(feature_version=(3, 11))`; compilation on current 3.12 runtime; `/private/tmp/vibe-upgrade-static-gates.log` |
| Base-to-HEAD and working diff checks | PASS; exit 0 | Final code, and repeated after documentation changes |
| Backend tree after final fix | Unchanged; exit 0 | `git diff --exit-code 8a745c40 3fab9117 -- agent pyproject.toml` |
| Confidentiality recheck | Removed source/skill/test/cache and reachable paths absent; 18 reintroduced unique objects absent after cleanup | `/private/tmp/vibe-upgrade-confidentiality-check.log`; object existence checks used `GIT_NO_LAZY_FETCH=1` |

The final fix only changed four frontend files. The unchanged-backend tree check above
establishes that the complete backend result still covers the final code; no redundant
second full backend run was needed. Compared with the passing baseline, there are 136
more backend tests and 43 more frontend tests. The 85 skips and 2,097 warning count
match the backend baseline; skip reasons were not captured with `-rs`, and no coverage
of those skipped cases is claimed. Live E2E paths were explicitly excluded.

Commands, from repository root unless stated:

```sh
.venv/bin/python -m pytest --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --tb=short -q
.venv/bin/python -m pytest tools/test_ci_env_var_gate.py -q
source .venv/bin/activate
bash tools/ci_grep_gates.sh
git diff --check c25fea2bc261a2d0ed971c989d194de9f04ec963 HEAD
git diff --check
git diff --exit-code 8a745c4072a5cec58b3366fc72baa43b7c8d79d6 3fab9117dce6f5b0631fc2ffdb7d1c69743a45b2 -- agent pyproject.toml
```

The full backend command needs local loopback/Unix-socket permission for its test
fixtures. It does not start an externally reachable application server. From
`frontend/`, run `npm run test:run`, then `npm run build`.

Changed-file static checks enumerate added/modified Python files using
`git diff --name-only --diff-filter=ACMR <base> HEAD -- '*.py'`, pass that explicit list
to `.venv/bin/python -m ruff check`, and parse/compile each selected file. Python 3.11
syntax compatibility was checked; a complete Python 3.11 runtime suite was not run.
Temporary logs above are local command evidence, not durable repository artifacts;
the results, commands, revisions and detailed task records are archived here.

Existing warnings remain visible: NumPy/ccxt and other baseline backend warnings;
four `os.environ.pop` warnings in unchanged `agent/src/providers/llm.py` at lines
1412/1416/1417/1418; Vite native-config/`__dirname` and the 742.71 kB chart vendor
chunk warning. The final Agent chunk is 190.75 kB (51.90 kB gzip). These facts do not
establish native-browser latency or FPS. The controlled render regression measured
12 parent renders on baseline versus zero after moving 12 stream updates to the
leaf, while asserting the final text/reasoning DOM.

## Review closure and archived records

Archived reports retain their original revision-specific status and pending notes;
this delivery report supersedes those historical statuses. Temporary diff packages
mentioned in them can be reconstructed from their recorded Git ranges. No private
runtime/memory/trading report or confidential source was archived.

| Task | Implementation and fixes | Final gate and evidence |
|---|---|---|
| 1 — session ownership/persistence | `7f6f980e`, `1c864c99` | Startup recovery fix passed scoped independent review, recorded in the controller ledger; no separate scoped report was emitted. 93 covering tests passed after fix. [Implementation](reviews/2026-10-14-upgrade-task-1-report.md), [initial review](reviews/2026-10-14-upgrade-task-1-review.md). |
| 2 — API lifecycle/events | `56da7461`, `493a64be` | Wrong-loop close and publisher ingress findings addressed; 106 covering / 22 core tests passed. [Implementation](reviews/2026-10-14-upgrade-task-2-report.md), [review](reviews/2026-10-14-upgrade-task-2-review.md), [scoped gate](reviews/2026-10-14-upgrade-task-2-rereview-1.md). |
| 3 — frontend ownership/history | `0cfee39c`, `7579d540` | Scoped findings addressed; 67 fix tests passed. O1 retained for Task 7 and then closed. [Implementation](reviews/2026-10-14-upgrade-task-3-report.md), [review](reviews/2026-10-14-upgrade-task-3-review.md), [scoped gate](reviews/2026-10-14-upgrade-task-3-rereview-1.md). |
| 4 — provider producers | `0d70544c`, `e9cb3849`, `a422e87d` | Async client-cleanup startup failure addressed; 65 fix tests passed. [Implementation](reviews/2026-10-14-upgrade-task-4-report.md), [review](reviews/2026-10-14-upgrade-task-4-review.md), [scoped gate](reviews/2026-10-14-upgrade-task-4-rereview-1.md). |
| 5 — tools/replay | `784c39d7`, `9d4ed876`, `37e1e757` | Dispatch cancellation and refusal/completion race addressed; 414 first-fix / 76 second-fix tests passed. [Implementation](reviews/2026-10-14-upgrade-task-5-report.md), [review](reviews/2026-10-14-upgrade-task-5-review.md), [first re-review](reviews/2026-10-14-upgrade-task-5-rereview-1.md), [final scoped gate](reviews/2026-10-14-upgrade-task-5-rereview-2.md). |
| 6 — lifecycle/context/atomic state | `d2e0601a`, `cd0682e5` | Cancellation handoff addressed; 1,448 initial covering / 83 fix tests passed. [Implementation](reviews/2026-10-14-upgrade-task-6-report.md), [review](reviews/2026-10-14-upgrade-task-6-review.md), [scoped gate](reviews/2026-10-14-upgrade-task-6-rereview-1.md). |
| 7 — final integration | `3fab9117` | I1 inherited control IDs and I2 reverse-order stale SSE report overwrite both addressed; nine new regressions, 171 covering tests passed. [Whole-branch review](reviews/2026-10-14-upgrade-task-7-whole-branch-review.md), [consolidated fix](reviews/2026-10-14-upgrade-task-7-fix-report.md), [scoped final gate](reviews/2026-10-14-upgrade-task-7-scoped-review.md). |

The final review used the complete sanitized-base-to-`8a745c40` patch. The scoped final
review covered exactly `8a745c40..3fab9117`, confirmed both findings addressed and
identified no new Critical/Important breakage. Its supplied focused test/build evidence
was followed by the controller's fresh full frontend suite/build above.

## Controller rulings, in decision order

These six rulings were recorded during implementation and preserved before scratch
cleanup. Each states the tradeoff and the consequence if the underlying assumption
does not fit a future requirement.

1. **Coalesce bounded tool progress.** Only the caller commits progress; a stalled
   caller cannot grow an unbounded callback queue. Superseded intermediate progress
   may be dropped while latest accepted progress/completion remains. If every progress
   sample must be retained, this design would lose that intermediate detail.
2. **Unknown/denied tools are never-started writes.** They preserve diagnostics and
   ordered call IDs but do not invalidate replay. No execution means no mutation. If
   a future refusal path produces side effects, it must declare actual execution or
   mutable replay could become stale.
3. **Per-batch eight counts physical live readers.** Timed-out/revoked readers retain
   their batch and process leases until real exit. When all eight remain blocked,
   later calls receive explicit capacity outcomes. The cost is reduced availability
   under stuck SDKs; releasing permits early would permit unbounded live work.
4. **Synthetic replay/cache retains canonical grounding.** Each call has one ordered
   transcript/trace result, but only actual raw executions create external grounding
   observations. Existing synthetic-call evidence aliases are not migrated. A future
   requirement for a separate evidence alias on every synthetic call needs an explicit
   identity migration rather than treating replay as a new observation.
5. **Artifact failure cannot rewrite a claimed outcome.** State/trace failures are
   logged; the actual main-owned result remains authoritative for SessionService.
   Physically unwritable artifacts can retain old status. Consumers treating that
   failed artifact as the sole authority could therefore see stale state.
6. **Callbacks run outside the lifecycle gate.** Admission is serialized, but external
   callbacks cannot hold the cancellation/terminal lock indefinitely. Already-admitted
   callbacks may finish after the fence; later admissions are rejected. A requirement
   to retract a callback already executing cannot be satisfied by this lock protocol.

## Final-review observations and practical limits

The whole-branch review's considered/set-aside observations were adjudicated, not
dropped. No concrete additional regression was established for pre-start session
cancellation, real worker/finalizer ownership, cross-loop/repeated close, provider
cleanup, tool refusal/context/replay barriers, initialization construction, store
binding or legacy facades. No extra production fix was justified for those paths.

Cancellation bounds waiting and fences late observations; it cannot kill an uncooperative
Python/SDK thread. Already-admitted provider work may race cancellation before its
first SDK call. Running write tools remain owned and awaited without an uncertain-write
retry. Process budgets and same-store claims are single-process guarantees, not a
multi-process shared-writer protocol. Some short synchronous filesystem/callback
entry points do not have universal hard deadlines.

Atomic replacement protects ordinary file integrity and process interruption;
directory-fsync/power-loss durability and a multi-file transaction are not claimed.
Unwritable media cannot be made durable by control flow. Request-shape calibration
improves estimates but does not promise exact provider tokenizer accounting or the
elimination of all context-window errors. Fake providers, controlled workers and
wire-faithful EventSource fixtures establish the regression claims; real providers,
native browsers, live trading and power-loss tests remain outside this verification.

## Confidentiality follow-up

Removed confidential strategy paths, caches and local reachable objects were checked
again without reading or fetching the source. A background fetch had reintroduced 18
unique old objects; the already-authorized cleanup removed them, and a repository-local
negative fetch rule now excludes `refs/heads/a-share` from default origin fetches.
The remote `a-share` branch was rewritten to the sanitized base using an exact
force-with-lease, then independently verified through the GitHub ref API.

The remote repository is still public pending the requested additional approval:
GitHub does not allow a public fork to change visibility independently. Leaving its
fork network permanently removes associated metadata, so that operation has not been
executed without explicit consent. See GitHub's [fork visibility reference](https://docs.github.com/en/pull-requests/reference/forks)
and [detachment instructions](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/detaching-a-fork).
No claim is made that GitHub cached objects or shared fork-network copies are purged.
Repository visibility is a separate pending action; it does not alter the verified
local upgrade delivery.
