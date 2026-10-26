# Research workbench M0 implementation

Spec: `docs/decisions/0003-external-agent-research-workbench.md`.

## Global constraints

- Preserve the current uncommitted program-backtest and loss-analysis work.
- Reuse existing Runner, engines, run cards, report readers and local storage.
- The new default entry points must work without importing internal Agent, providers, sessions or Swarm.
- Local Python execution is limited to the workspace explicitly selected by the operator. Bind the workbench to loopback; retain request authentication and origin checks.
- Old experiment data remains readable. Legacy assistant internals are removed from default entry points in this slice; reverse-dependent modules are not deleted blindly.
- M0 is the current delivery milestone. M1–M3 remain subsequent milestones in ADR-003.

## Task 1: Persistent experiment service

Implement source/input snapshots, stable run IDs, terminal status persistence, a bounded task owner, cancellation and restart reconciliation. Support program and built-in backtest execution through the existing Runner. Retain parent-run and project metadata. Validate empty trades and finite metrics.

Verification: real temporary programs prove execution, source immutability, zero-trade success, failure, cancellation, restart state, path boundaries and preservation of existing runs.

## Task 2: Independent API, CLI and MCP

Reuse report reading with explicit dependencies. Add loopback workbench API and CLI/MCP clients that submit to the service and read persisted results. Switch installed default commands to these entry points. Preserve read access to historical reports and source snapshots.

Verification: API submits and reports a real temporary experiment; protected writes reject foreign origins/paths; clean subprocess imports forbid internal Agent modules; CLI JSON and MCP use the same service contract.

## Task 3: Research-first frontend

Replace the conversation sidebar and default route with a workbench that lists project experiments, monitors task status and opens existing reports/comparison. Show run configuration/provenance and external-agent instructions. Replace model settings with workbench connection settings. Remove assistant-specific default navigation and route chunks.

Verification: render real workbench fixtures, navigate to results, cancel active work, surface failures and empty state; no session fetch. Run frontend suite and production build.

## Task 4: Documentation and integration

Provide a deterministic offline sample experiment and document serve/submit/status/results/cancel, MCP configuration, snapshot coverage, old-report compatibility and remaining migration boundaries. Validate the sample end-to-end without LLM credentials.

## Task 5: Verification and independent review

Run targeted backend regressions and frontend checks. Review the complete working-tree change against ADR-003, including existing modifications touched by integration. Fix material findings and record remaining limitations.

## Review focus

Snapshot and path isolation; process ownership during cancel/shutdown/restart; data preservation; finite/zero-trade outputs; consistent Web/API/CLI/MCP identities; no assistant initialization; no misleading completed/verified status; legacy report compatibility.

## Delivery evidence (2026-10-26)

M0 is implemented. Relevant backend regressions: **264 passed**. Real HTTP owner + CLI subprocess + MCP client integration: **1 passed**. Full frontend suite: **785 passed**, production build successful. Independent review found process cleanup and stale-log issues; both were reproduced and fixed. Browser validation also identified and fixed missing boot assets and negative-drawdown comparison.

The local environment's console entry points have been updated through an offline editable install. The preview at `http://127.0.0.1:18899` uses two synthetic experiments stored in `/tmp/vibe-workbench-preview-m0`; it does not modify the normal experiment storage.

The implementation preserves existing user changes and remains uncommitted. Historical assistant implementations/dependencies and old deployment launchers are retained outside the default entry points. Trusted-program execution, explicit-file-only snapshots and unknown orphan outcomes after a crash are documented limitations; M1–M3 remain pending.

## Follow-up: frontend and identity cleanup (2026-10-26)

The user requested removal of both obsolete assistant features and Vibe-Trading
branding. The UI now uses **Quant Research Workbench / 量化研究工作台**, with a
research icon, neutral browser metadata and no old promotional landing page.

- Deleted 98 obsolete frontend source/test files and the old public logo:
  chat, internal Agent store and streaming, Swarm, model/channel settings,
  scheduled assistants and live-execution controls.
- Moved `MetricsCard` and its tests from chat into `components/run`; report,
  backtest, factor, options and read-only portfolio resources remain available
  in source. Existing user modifications were preserved.
- Removed the corresponding client API methods/types, 17 obsolete translation
  namespaces across nine locales, chat-only styles and five direct npm
  dependencies (41 installed packages including transitive dependencies).
- Added and installed `quant-workbench` / `quant-workbench-mcp`; kept the old
  command aliases, storage paths, API-key identifier and original MIT attribution.
  Replaced stale translated promotional READMEs with links to maintained docs.
- Backend assistant/session/Swarm code, Python dependencies and old deployment
  launchers are still outside this deletion batch. Their shared research imports
  must be separated before removal; no user experiment or session data was deleted.

Verification: **378 current frontend tests passed**, production build passed,
**6 workbench API/CLI/MCP integration tests passed**, both new console entry
points and the legacy CLI alias resolved, static translation references had no
missing keys. Removed feature-specific tests account for the lower test count.
