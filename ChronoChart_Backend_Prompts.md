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

## Prompt 3 — Aggregation core

The user selected Prompt 3 directly; provider/normalization/timeframe milestones
remain unimplemented. Prompt 0 packaging, installation, and checkpoint rules apply.

- [x] Add pure, provider-independent `aggregate_candles` in its own module.
- [x] Keep group definition separate: explicit members, output label, expected count.
- [x] Implement first Open, max High, min Low, last Close, summed Volume.
- [x] Define timestamps, empty input/groups, strict missing/invalid-value handling,
  count-based completeness metadata, and retain/drop policies for any partial group.
- [x] Test hand-calculated output, single rows, sorting, invalid/empty/partial input,
  timezone preservation, deterministic calls, and input/output non-aliasing.
- [x] Demonstrate fixture-only retain/drop aggregation before any downloaded data.
- [x] Explain validation/reduction and sorting complexity separately in
  `docs/aggregation.md`; README and CLI document exact local commands.
- [x] Reverify editable/non-editable install, help, all tests, and dependency checks.
- [x] Generate local-only checkpoint backup and prepare milestone GitHub delivery
  (resulting commit ID/push outcome recorded locally after delivery).

### Decisions and learning notes

- **What:** `CandleGroup` specifies membership and expected count;
  `AggregationResult` returns separate OHLCV and metadata DataFrames.
- **Why:** external group definitions keep all calendar/session/provider policy
  out of reduction. Metadata prevents retained partial candles from looking complete.
- **Underneath:** validate a unique partition of source rows, copy/sort chronologically,
  map rows to explicit owners, then reduce each group's values once. Volume uses
  `math.fsum`; finite float64 values and valid OHLC ranges are required.
- **Complexity:** sorting O(n log n) time / O(n) space; validation and reduction
  expected O(n + g) time / O(n + g) space with timestamp hash maps. Total space
  O(n + g); no repeated full-frame scan for each group.
- **Project connection:** a future timeframe engine can supply the same groups to
  the reducer for stock or crypto data without changing OHLCV arithmetic.

Completeness is **observed count == caller's expected count**, not calendar or
live-candle closure verification. Empty groups produce metadata but never synthetic
prices. Float64 output is not arbitrary-precision money/volume accounting.

### Checkpoint verification

- Editable environment: 36 tests pass with warnings treated as errors.
- Fixture retain output: `[10,16,9,15,600]` and `[15,19,14,18,900]`, with the
  second group marked incomplete (2/3 observed).
- Fixture drop output: only the first candle; metadata for the second group remains
  with `is_complete=False, retained=False`.
- Reinstalled editable `.venv` and non-editable `.venv-check`; all 36 tests pass in
  both with warnings treated as errors. CLI help and `pip check` pass in both.
- Non-editable import confirmed under `.venv-check/Lib/site-packages`; all 26
  dependency/tool versions still match pins. Module/console help works outside
  the checkout. No dependency snapshot changes were necessary.
- `main.py`, `ChronoChart_Brief.md`, and dependency snapshots remain unchanged.
  Backup and both environments remain Git-ignored; diff whitespace checks pass.
- Delivery commit: `Implement provider-independent aggregation core and fixture demo`
  on confirmed `main`; local backup records commit ID and push outcome. The backup
  is excluded from commit/push. No live-provider/downloaded-data result is claimed.

## Prompt 4 — Trading-session timeframes

Prompt 0 foundation/workflow remains in force. Requirements brief stays unchanged.

- [x] Configurable session counts, including 10, 15, and explicit prototype alias
  `3 trading weeks = 15 sessions`; reject ambiguous calendar units.
- [x] Use maintained `exchange-calendars` 4.13.2 for selected exchange XNYS.
- [x] Stable session-ordinal anchor at 2000-01-03, independent of requested start.
- [x] Diagnose missing closed requested sessions separately from holidays,
  request-edge truncation, and unfinished full windows.
- [x] Independent leading/trailing/unfinished retain/drop policies and explicit
  missing raise/retain/drop. Missing rows cannot shift following windows.
- [x] Compose group plans with unchanged Prompt 3 reduction in `pipeline.py`.
- [x] Document UTC session-label schema, inclusive bounds, calendar/version
  provenance, close instants, supported horizon, and completeness semantics.
- [x] Test weekends/holidays/special closures, DST/early closes, truncated/overlapping
  queries, invalid inputs, partial groups, policy drops, and non-mutation.
- [x] Offline demo deliberately omits January 10; next group still starts January 18.
- [x] Final editable/fresh non-editable installation, CLI help, pins, tests, and Git checks.
- [x] Generate local-only delivery checkpoint and prepare milestone commit/push on
  confirmed `main` (commit ID/push outcome recorded locally after delivery).

### Decisions and learning notes

- **What:** `TradingSessionTimeframe`, `build_trading_session_groups`, and composition
  helper `aggregate_trading_sessions` add equity-session boundaries without changing
  the OHLCV arithmetic. Every intersecting window remains visible in metadata.
- **Why:** grouping observed rows shifts boundaries after a missing session;
  enumerating the exchange's scheduled sessions from a fixed anchor does not.
- **Underneath:** group ID = session ordinal // N. Request bounds select scheduled
  windows; explicit source membership supplies observations. An explicit aware
  `as_of` compared to exchange close instants determines elapsed expectations.
- **Complexity:** one-time library schedule construction/cache O(S) storage;
  membership/request/window work expected O(S+n+q+g+N) with at most q+2N lookaround.
  Reduction remains separate: sorting O(n log n), then arithmetic O(n+g).
- **Project connection:** supports 10/15-session candles and the 3-trading-week
  prototype without pretending that these equal calendar-day/week timeframes.

`coverage_complete` means no absent **closed requested** sessions; `count_complete`
means N observations; session `is_complete` additionally requires `window_closed`.
Calendar revisions can change ordinals, so metadata attrs record exchange/version/
anchor and dependencies are pinned. Requests support 2000-01-03..2099-12-31 with
full-window lookahead through 2101; oversized windows raise instead of truncating.

### Verification checkpoint

- 62 tests pass with `-W error` in editable `.venv`.
- Offline fixture with missing 2000-01-10 returns counts 9/10 then 10/10 at labels
  2000-01-03 and 2000-01-18. The second candle's boundary is unchanged.
- Pinned maintained calendar 4.13.2 (PyPI release 2026-03-10); added pyluach,
  toolz, and korean-lunar-calendar runtime pins.
- Compatibility finding: pandas 3.1.1 emits an upstream calendar timezone-values
  deprecation. Use verified pandas 3.0.6 and `<3.1` instead of suppressing warnings.
- Editable `.venv` reinstalled; fresh `.venv-p4-check` installed from pinned tools/
  runtime versions with a non-editable wheel. All 62 tests pass in both with
  warnings treated as errors; CLI help and `pip check` pass in both.
- All 30 dependency/tool pins match the fresh environment. Non-editable import
  confirmed inside `.venv-p4-check/Lib/site-packages`. Tests cover module/console
  entry points outside the checkout. No provider or downloaded-data tests claimed.
- Requirements brief, thin `main.py`, and Prompt 3 reducer remain unchanged.
  Backup and environments confirmed Git-ignored; diff whitespace checks pass.
- Delivery commit: `Implement anchored XNYS trading-session timeframes` on `main`.
  Local backup records the resulting commit ID and push outcome; it is excluded
  from GitHub delivery.

## Remaining milestones (not implemented)

Provider adapter and explicit OHLCV normalization: inspect daily AAPL timestamps
and columns, specify adjustment/timezone/duplicate/missing/order policies, save an
ignored local sample, and validate provider data before applying existing grouping
and reduction. Calendar-day/week and intraday timeframes remain separate future work.
