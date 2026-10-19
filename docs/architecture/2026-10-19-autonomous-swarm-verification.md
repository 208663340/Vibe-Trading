# Autonomous swarm verification

Date: 2026-10-19. Feature branch: `codex/autonomous-swarm`.
Base revision: `31784006bc755d99d12264b231525d88fe5e9925`.

## Delivered

Opt-in autonomous preset execution with capability matching, transactional claims,
expiring leases, monotonic fencing epochs, attempt-isolated artifacts, bounded real
worker admission, durable cancellation, ordered events and same-run recovery.
Existing DAG mode stays the default. Added a usable research-only preset, CLI
inspection, authenticated recovery API, operator guide and ADR-002. No new package
or service dependencies. Preset inventory metadata now consistently advertises 31.

## Checks

| Check | Result |
| --- | --- |
| Initial DAG/replay/store baseline | 29 passed |
| New autonomous tests plus existing REST/SSE contracts, after review fixes | 48 passed |
| Distribution manifest and all README count checks | 122 passed |
| Full backend run before correcting stale baseline test path | 18,120 passed; 84 skipped; 1 pre-existing test-path failure |
| Targeted baseline test after path correction | 1 passed |
| Final complete backend suite (`.venv/bin/python -m pytest -q`) | **18,121 passed; 84 skipped; 0 failures**; 2,097 warnings; 285.51s |
| Frontend tests (`npm run test:run`) | 80 files, 788 tests passed |
| Frontend production build (`npm run build`) | Passed |
| CLI `--swarm-inspect autonomous_research_team` | Passed; capability requirements rendered |
| Ruff on changed Swarm/API files and new tests | Passed |
| `git diff --check` | Passed |

The backend suite was first run in the normal sandbox. That run exposed nine
metadata assertions caused by adding preset 31 (fixed), 36 socket permission
failures (all resolved when rerun outside the sandbox), and the baseline failure
below. The full run includes the consolidated independent-review fixes.
Existing frontend warnings cover Vite config loading and large bundle chunks;
backend warnings mostly concern existing NumPy/Pandas timedelta deprecations.
Scanning the whole unchanged Swarm directory also found its existing unused `os`
import in `grounding.py`; scoped lint on changed files is clean.

## Corrected pre-existing test path

`agent/tests/test_interactive_message_limit.py::test_frontend_message_bound_matches_the_server_schema`
reads `frontend/src/lib/chatPrompt.ts`, which is absent from the base HEAD. Current
frontend prompt code lives in `frontend/src/features/agent/prompt.ts`.

Confirmed independently by exporting the unchanged HEAD test, conftest and frontend
with `git archive HEAD` into `/tmp/vibe-autonomous-swarm/baseline` and running the
same test there. It fails with the same `FileNotFoundError`. Updated only the test's
path to the current frontend module, preserving its server/frontend limit equality
assertion. The targeted test now passes; no production behavior changed for this
baseline correction. The final complete backend run is green.

## Test evidence and review

Tests exercise separate SQLite connections and subprocesses racing claims,
expired-owner rejection, duplicate completion, dependency failure, retry exhaustion,
deadline immutability, restart takeover, compatible result reuse, artifact isolation,
cooperative cancellation with occupied capacity, retained callback fencing, history
projection, SSE cursor continuation and terminal-tail drainage. Real Worker
integration replaces the provider rather than the Worker itself.

The independent reviewer reported no Critical issues and three Important issues.
Each was reproduced by a failing regression, fixed in one pass and included in the
final full run. No Minor findings were deferred. See the
[review and rulings record](reviews/2026-10-19-autonomous-swarm-review.md).

## Limits

Single-host coordination, preset-defined tasks, capability-based local claiming.
No dynamic task creation, learned routing, exact crash-lost token accounting,
host-wide process termination or certification of scientific/financial validity.
No live provider, market-data or broker experiment was performed. The bundled
preset grants research tools, with no order-placement or shell capability.

The original dirty backtest runner/tool changes, program-contract files, frontend
loss-point work and locale changes were preserved and excluded from this feature.
