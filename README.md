# ChronoChart / Custom-Timeframe

An independent financial charting project for custom OHLCV candle timeframes.
The first backend milestone provides project setup and a command-line shell;
market-data downloading and candle processing are not implemented yet.

## Local setup (Windows PowerShell)

Requires Python 3.11 or newer. From the repository folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python main.py --help
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` instead of
`python` for installation and execution. Changing execution policy is not required.
The `--help` output also contains setup and local-execution instructions and works
before pandas or yfinance are installed.

## Verify locally

```powershell
python main.py --version
python -m unittest discover -s tests -v
```

## Structure

- `main.py`: thin entry point.
- `chronochart/cli.py`: argument parsing, help, and future command dispatch.
- `chronochart/`: future provider, normalization, aggregation, and timeframe modules
  will remain separate from the entry point and each other.
- `tests/`: offline correctness checks.
- `ChronoChart_Brief.md`: main project plan and milestone progress.
- `BACKEND_SESSION_BACKUP.md`: local-only backend handoff and verification record
  (generated at checkpoints; Git-ignored, never committed or pushed).

Dependencies are declared in `pyproject.toml` with compatible ranges, not an exact
lockfile. yfinance is intended for prototype historical data; validate its data and
usage terms before relying on it in production. Local data and virtual environments
are excluded from Git.
