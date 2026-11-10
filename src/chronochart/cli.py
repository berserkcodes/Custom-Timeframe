"""Command-line parsing, help, and application dispatch only.

Processing belongs in the separate providers, normalization, aggregation, and
timeframes modules; those modules are scaffolds until their respective milestones.
"""

import argparse
from collections.abc import Sequence

from chronochart import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI without loading optional market-data dependencies."""
    return argparse.ArgumentParser(
        description="ChronoChart: custom-timeframe OHLCV candles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Windows PowerShell setup (reproduced on Python 3.13.4, 64-bit):
  cd 'C:\\chrono chart'
  python -m venv .venv
  .\\.venv\\Scripts\\Activate.ps1
  python -m pip install -r requirements-build.txt
  python -m pip install -r requirements.lock.txt
  python -m pip install -e . --no-deps --no-build-isolation

Local execution:
  python main.py --help
  python -m chronochart --help
  chronochart --help
  python main.py --version
  python -m unittest discover -s tests -v

If activation is blocked, no execution-policy change is necessary:
  .\\.venv\\Scripts\\python.exe -m pip install -r requirements-build.txt
  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.lock.txt
  .\\.venv\\Scripts\\python.exe -m pip install -e . --no-deps --no-build-isolation
  .\\.venv\\Scripts\\python.exe main.py --help

Status:
  Prompt 0 scaffold only: no market data is fetched or aggregated.
  Requirements: ChronoChart_Brief.md (preserved unchanged).
  Progress tracker: ChronoChart_Backend_Prompts.md.
""",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and show usage until processing commands are added."""
    parser = build_parser()
    parser.add_argument("--version", action="version", version=f"ChronoChart {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
