> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

**I1 — Reject cancelled readers at actual dispatch — ADDRESSED.** `agent/src/runtime/agent/tool_execution.py:171` checks revocation and the captured stop predicate inside the copied context before emitter installation, `executed=True`, or registry execution (`:174`–`:176`). This closes the reported snapshot and admitted-before-dispatch effect windows. Refusal and predicate exceptions run worker cleanup (`:179`–`:182`); diagnostics preserve the original prepared-call identity (`:149`–`:150`), and predicate failures retain their exception (`:244`–`:251`). Caller cancellation does not release a live producer's lease. The refusal-delivery addition has the separate Important race below.

### New Breakage in the Fix Diff

- **Important — Worker refusal can race past caller observation and become an empty executed result.** `agent/src/runtime/agent/tool_execution.py:194` checks `reader.revoked` only at poll entry. If it is false then, a worker can subsequently evaluate its context-specific stop predicate as true, set revocation, and finish while the caller-side predicate remains false. The completion path (`:233`–`:252`) accepts that refusal as `outcome="executed", executed=False, raw_result=""`; its own revocation write at `:239` also erases any distinction between producer refusal and completion fencing. Recheck producer refusal at the completion acceptance boundary before setting the completion fence, or preserve refusal as separate terminal state. Add a barrier-controlled regression that lets refusal occur after the entry revocation read and before completion acceptance. The new context-only refusal test covers a worker that has already refused when polling begins, but does not enforce this interleaving.

### Out-of-Scope Observations

- None newly observed. The earlier ccxt_loader NumPy warnings and Task 6/Task 7 integration gates remain previously recorded, non-blocking follow-ups for this scoped review.

### Checks and Evidence

- Read the supplied re-review instructions, Task 5 brief, I1 review, appended fix report, and complete fix diff for `83a84ec5..9d4ed87677a834ff70243432b5be38b5d51753f8` once. The combined output truncated an earlier report section; the fix report and fix diff were visible in full. Read only the named invoker constructor/diagnostic/start/poll/invoke boundary to recover surrounding completion logic and exact line references.
- Read the saved final covering output: **414 passed in 7.46s**, with no warnings shown. Read the saved controlled RED output: the original three regressions failed on actual effects; the fourth mutation regression failed on refusal outcome. No covering suite was rerun. The four added tests match the report's named behaviors, copied-context checks, and barrier-release/worker-join cleanup. The fix diff changes only the invoker and lifecycle tests; public Tool schema and grounding code are unchanged.
- Ran one bounded local fake-operation probe for the specific completion race left unanswered by those tests: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=agent .venv/bin/python -`. The worker stop predicate waited behind an Event; the second caller predicate check released that worker and awaited `reader.done`, placing refusal after poll's initial revocation read. Observed `{'outcome': 'executed', 'executed': False, 'effects': [], 'prepared_identity': True, 'active': 0, 'abandoned': 0}`. The expected-cancellation assertion failed, **exit 1**. Finally released the barrier, joined every recorded producer, asserted actual exit and zero active/abandoned private capacity. No network, live tools, accounts, credentials, production/test edits, Git commands, index/HEAD changes, or suite rerun.

### Verdict

**Fix round: Findings remain open — new Important worker-refusal completion race.** I1's actual registry-dispatch defect is addressed; the fix cannot pass while worker-only refusal can still be reported as executed.
