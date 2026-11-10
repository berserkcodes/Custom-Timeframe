# ChronoChart Backend Prompts and Progress

## Authority and workflow

- Requirements: `ChronoChart_Brief.md`; preserve it unchanged. This file replaces
  the brief as the main backend progress tracker.
- Project: `C:\chrono chart`; repository: https://github.com/berserkcodes/Custom-Timeframe;
  confirmed branch: `main`.
- Keep `main.py` thin, with independent provider, normalization, aggregation,
  timeframe, and CLI responsibilities under `src/chronochart`.
- At each checkpoint generate/update local `BACKEND_SESSION_BACKUP.md`.
  It is Git-ignored and must never be committed or pushed.
- At milestone completion: verify installation/tests/help, update this tracker,
  then commit and push to the confirmed branch. Never request credentials in chat.

## Prompt 0 — Backend foundation / src-layout adaptation

Scope is the user's supplied Prompt 0 checklist; no additional prompt text was supplied.

- [x] Adapt existing package to `src/chronochart`.
- [x] Separate providers, normalization, aggregation, timeframes, and CLI modules.
- [x] Keep `main.py` as a thin delegating entry point.
- [x] Configure packaging and console/module entry points in `pyproject.toml`.
- [x] Add pinned runtime and installation/build dependency snapshots.
- [x] Adapt `.gitignore`, README, progress tracker, and local-only backup.
- [x] Preserve `ChronoChart_Brief.md` without changes.
- [x] Verify editable and fresh non-editable installation in virtual environments.
- [x] Verify CLI help, seven offline tests, and dependency consistency.
- [x] Prepare milestone GitHub delivery and generate the local checkpoint record
  (push result and commit ID recorded in the local backup after delivery).

### Design explanation

- **What:** a `src` package with distinct processing boundaries and three CLI entry
  paths (`main.py`, `python -m chronochart`, `chronochart`).
- **Why:** package installation must work independently of the checkout directory;
  provider quirks must not leak into grouping or CLI concerns.
- **Underneath:** setuptools discovers packages only in `src`, installs the package
  and console launcher, and each launcher delegates to the same argument parser.
- **Complexity:** argument parsing is linear in input argument size; help output
  is fixed-size. Processing modules are scaffolds, so no OHLCV complexity is claimed.
- **Project connection:** this establishes testable boundaries for later data,
  normalization, and custom-candle milestones without prematurely defining policies.

### Verification and delivery

Verified on Windows x64 / Python 3.13.4:

- `.venv`: pinned tools/runtime installed; editable `src` package installed;
  `main.py --help` passed, all seven tests passed, and `pip check` passed.
- Fresh `.venv-check`: same pins installed; regular wheel built and installed
  without dependency resolution or build isolation. All seven tests and CLI help
  passed, and `pip check` passed. Import path confirmed inside this environment's
  `Lib/site-packages`, not the source tree.
- Module and console-script help also passed from outside the repository.
- All 26 pinned runtime/tool versions matched the fresh environment.
- `git diff --exit-code -- ChronoChart_Brief.md` passed (requirements unchanged).
- Backup and both virtual environments confirmed Git-ignored.

Exact commands are in README.md and `python main.py --help`. Dependency snapshots
are version-pinned, not hash-verified or guaranteed cross-platform. No live-provider
test or processing implementation is claimed.

Milestone delivery commit: `Apply Prompt 0 src package foundation and pinned setup`
on `main`. The local-only backup records the resulting commit ID and push outcome;
it is excluded from GitHub delivery.

## Prior checkpoint

- `40ff4ee`: initial backend setup pushed to `main`; four CLI tests passed.
- Prompt 0 supersedes that checkpoint's flat package layout and progress tracking
  in the brief, without rewriting the requirements document.

## Next milestone (not implemented)

Provider adapter and explicit OHLCV normalization: inspect daily AAPL timestamps
and columns, specify adjustment/timezone/duplicate/missing/order policies, save an
ignored local sample, and add deterministic tests before aggregation.
