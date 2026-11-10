# ChronoChart / Custom-Timeframe

An independent financial charting project for custom OHLCV candle timeframes.
**Prompt 0 provides packaging and module scaffolds only.** Market-data downloading,
normalization, candle aggregation, timeframe rules, and UI are not implemented yet.

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
```

`python main.py --help` also documents setup and execution. Install the package
first: the `src` layout intentionally does not modify `sys.path` to bypass installation.
Help does not import pandas/yfinance or access the network.

### Fresh, non-editable installation check

This separate environment verifies installation rather than imports accidentally
resolving from the checkout. It is safe to retain locally and is Git-ignored.

```powershell
python -m venv .venv-check
.\.venv-check\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-check\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv-check\Scripts\python.exe -m pip install . --no-deps --no-build-isolation
.\.venv-check\Scripts\python.exe -m pip check
.\.venv-check\Scripts\python.exe main.py --help
.\.venv-check\Scripts\python.exe -m unittest discover -s tests -v
```

## Dependency reproducibility

- `pyproject.toml`: supported direct dependency ranges, exact build requirements,
  `src` package discovery, and the `chronochart` console script.
- `requirements-build.txt`: pinned pip, setuptools, and wheel.
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
  aggregation.py                Future grouped OHLCV reductions
  timeframes.py                 Future boundary/session/calendar logic
tests/test_cli.py               Offline entry-point and package checks
ChronoChart_Brief.md             Requirements document; preserved unchanged
ChronoChart_Backend_Prompts.md   Main backend progress tracker
BACKEND_SESSION_BACKUP.md       Local-only checkpoint handoff (Git-ignored)
```

The four processing modules are documented boundaries, not working algorithms.
Keeping them independent prevents CLI or provider code from silently defining
candle semantics. Module separation adds no market-data processing overhead yet;
argument parsing is linear in the argument input size.

## Milestone workflow

Use the confirmed `main` branch of
<https://github.com/berserkcodes/Custom-Timeframe>. Verify locally, update
`ChronoChart_Backend_Prompts.md`, generate/update the local backup after each
checkpoint, then commit and push the implementation and tracker. **Never commit
or push `BACKEND_SESSION_BACKUP.md`.** Keep virtual environments, credentials,
and downloaded market data out of Git. Do not edit the brief for progress updates.
