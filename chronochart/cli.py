"""Command-line parsing, help, and application dispatch only.

Provider, normalization, aggregation, and timeframe implementations belong in
separate modules as those milestones are implemented.
"""

import argparse
from collections.abc import Sequence

from chronochart import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI without loading optional market-data dependencies."""
    return argparse.ArgumentParser(
        description="ChronoChart: custom-timeframe OHLCV candles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Windows PowerShell setup (Python 3.11 or newer):
  cd 'C:\\chrono chart'
  python -m venv .venv
  .\\.venv\\Scripts\\Activate.ps1
  python -m pip install --upgrade pip
  python -m pip install -e .

Local execution:
  python main.py --help
  python main.py --version
  python -m unittest discover -s tests -v

If activation is blocked, no execution-policy change is necessary:
  .\\.venv\\Scripts\\python.exe -m pip install -e .
  .\\.venv\\Scripts\\python.exe main.py --help

Status:
  Project setup is implemented. Download and aggregation commands will be
  added in later milestones; no market data is fetched by this command.
""",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and show usage until processing commands are added."""
    parser = build_parser()
    parser.add_argument("--version", action="version", version=f"ChronoChart {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
