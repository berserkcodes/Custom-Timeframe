# ChronoChart / Custom-Timeframe

An independent financial charting project for custom OHLCV candle timeframes.
**Prompt 4 adds anchored NYSE trading-session timeframes** on the Prompt 0 package
foundation and Prompt 3 aggregation core. Configurable counts include 10 and 15
sessions; the prototype alias **3 trading weeks = 15 sessions** is not a calendar
duration. Market-data downloading, normalization, calendar-day/week timeframes,
and UI are not implemented yet. All demos are offline fixtures.

## Exact local setup — Windows PowerShell

Verified target: **Windows x64, CPython 3.13.4**. Package metadata permits Python
3.11+, but the dependency snapshot is verified only on this target.

```powershell
cd 'C:\chrono chart'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
.\.venv\Scripts\python.exe -m pip check
```

Activation is optional. If preferred, run `.\.venv\Scripts\Activate.ps1` and use
`python` instead of `.\.venv\Scripts\python.exe`. If activation is blocked, keep
using the explicit interpreter; no execution-policy change is necessary.

## Local execution and verification

```powershell
.\.venv\Scripts\python.exe main.py --help
.\.venv\Scripts\python.exe main.py --version
.\.venv\Scripts\python.exe -m chronochart --help
.\.venv\Scripts\chronochart.exe --help
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --demo-aggregation
.\.venv\Scripts\python.exe main.py --demo-aggregation --incomplete drop
.\.venv\Scripts\python.exe main.py --demo-trading-sessions
.\.venv\Scripts\python.exe main.py --demo-trading-sessions --session-timeframe "3 trading weeks"
```

`python main.py --help` also documents setup and execution. Install the package
first: the `src` layout intentionally does not modify `sys.path` to bypass installation.
Help does not import pandas/yfinance or access the network.

### Fresh, non-editable installation check

This separate environment verifies installation rather than imports accidentally
resolving from the checkout. It is safe to retain locally and is Git-ignored.

```powershell
python -m venv .venv-p4-check
.\.venv-p4-check\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-p4-check\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv-p4-check\Scripts\python.exe -m pip install . --no-deps --no-build-isolation
.\.venv-p4-check\Scripts\python.exe -m pip check
.\.venv-p4-check\Scripts\python.exe main.py --help
.\.venv-p4-check\Scripts\python.exe -W error -m unittest discover -s tests -v
```

## Dependency reproducibility

- `pyproject.toml`: supported direct dependency ranges, exact build requirements,
  `src` package discovery, and the `chronochart` console script.
- `requirements-build.txt`: pinned pip, setuptools, and wheel.
- Calendar: `exchange-calendars==4.13.2` for `XNYS`; pandas is capped `<3.1` and
  pinned to 3.0.6 to avoid upstream calendar/pandas 3.1 deprecation warnings.
- `requirements.lock.txt`: exact direct and transitive runtime versions, excluding
  the local project. This is a version snapshot, not a hash-verified lockfile;
  artifact availability and other platforms are not guaranteed.
- Install the pins first, then install the project with `--no-deps
  --no-build-isolation` to avoid re-resolving dependency/build versions.
- To intentionally refresh, resolve dependencies in a separate clean environment,
  capture `python -m pip freeze --exclude-editable`, separate build tools from
  runtime pins, review the changes, and rerun the fresh-install checks above.

## Structure and responsibilities

```text
main.py                         Thin entry point: delegates to CLI
src/chronochart/
  __init__.py                   Package version
  __main__.py                   python -m chronochart entry point
  cli.py                        Arguments, help, and future dispatch
  providers.py                  Future market-data adapters
  normalization.py              Future common OHLCV schema and validation
  aggregation.py                Pure grouped OHLCV reductions and metadata
  timeframes.py                 Anchored XNYS session plans and policies
  pipeline.py                   Calendar grouping + independent reduction
  demo.py                       Hand-calculated, network-free fixtures
tests/                          Offline CLI, aggregation, and session tests
docs/aggregation.md             API semantics, fixture math, and complexity
docs/trading_sessions.md        Calendar anchor, policy semantics, API examples
ChronoChart_Brief.md             Requirements document; preserved unchanged
ChronoChart_Backend_Prompts.md   Main backend progress tracker
BACKEND_SESSION_BACKUP.md       Local-only checkpoint handoff (Git-ignored)
```

Provider and normalization modules remain documented scaffolds.
Aggregation operates on caller-supplied `CandleGroup` definitions; it never infers
calendar/session boundaries. Missing/invalid OHLCV values are rejected, timestamps
are explicit group labels, and incomplete groups may be retained (default) or
dropped. Both policies expose count-based completeness metadata, including empty
and dropped groups. Inputs are not mutated.

Read [the aggregation contract and hand-calculated example](docs/aggregation.md).
Sorting costs O(n log n); validation and reduction cost O(n + g) separately, with
O(n + g) space for n rows and g explicit groups. Completeness is based on the
caller's expected row count, not proof of calendar coverage or a closed live candle.

Read [the trading-session contract](docs/trading_sessions.md). XNYS groups use the
fixed anchor **2000-01-03**, never the query start or observed-row position. Missing
closed requested sessions raise by default, so data gaps cannot silently shift all
subsequent boundaries. Explicit missing/edge/unfinished retain/drop policies keep
metadata for omitted windows. Scheduled holidays are not missing sessions.

The session composition result distinguishes `coverage_complete`, `count_complete`,
and `window_closed`; `is_complete` requires full observed count **and** the final
scheduled close to have elapsed. An explicit `as_of` instant makes behavior
deterministic. Sources use UTC-midnight exchange-date labels, not raw intraday
timestamps. Calendar days/weeks are deliberately not aliases for these timeframes.

## Milestone workflow

Use the confirmed `main` branch of
<https://github.com/berserkcodes/Custom-Timeframe>. Verify locally, update
`ChronoChart_Backend_Prompts.md`, generate/update the local backup after each
checkpoint, then commit and push the implementation and tracker. **Never commit
or push `BACKEND_SESSION_BACKUP.md`.** Keep virtual environments, credentials,
and downloaded market data out of Git. Do not edit the brief for progress updates.
