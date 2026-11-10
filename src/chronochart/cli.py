"""Command-line parsing, help, and application dispatch only.

Processing belongs in separate modules; providers and normalization remain
scaffolds, while aggregation and trading-session grouping are implemented.
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
  python main.py --demo-aggregation
  python main.py --demo-aggregation --incomplete drop
  python main.py --demo-trading-sessions
  python main.py --demo-trading-sessions --session-timeframe "3 trading weeks"
  python -m unittest discover -s tests -v

If activation is blocked, no execution-policy change is necessary:
  .\\.venv\\Scripts\\python.exe -m pip install -r requirements-build.txt
  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.lock.txt
  .\\.venv\\Scripts\\python.exe -m pip install -e . --no-deps --no-build-isolation
  .\\.venv\\Scripts\\python.exe main.py --help

Status:
  Aggregation and anchored XNYS session groups implemented; offline demos only.
  Requirements: ChronoChart_Brief.md (preserved unchanged).
  Progress tracker: ChronoChart_Backend_Prompts.md.
""",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch the offline demonstration or show usage."""
    parser = build_parser()
    parser.add_argument("--version", action="version", version=f"ChronoChart {__version__}")
    demos = parser.add_mutually_exclusive_group()
    demos.add_argument(
        "--demo-aggregation", action="store_true",
        help="aggregate a hand-calculated fixture without downloading data",
    )
    demos.add_argument(
        "--demo-trading-sessions", action="store_true",
        help="demonstrate stable XNYS boundaries with a deliberately missing session",
    )
    parser.add_argument(
        "--session-timeframe", default="10 trading sessions",
        help="session-demo timeframe, e.g. '15 trading sessions' or '3 trading weeks'",
    )
    parser.add_argument(
        "--incomplete", choices=("retain", "drop"), default="retain",
        help="incomplete fixture-group policy (default: retain)",
    )
    args = parser.parse_args(argv)
    if args.demo_trading_sessions:
        from chronochart.demo import aggregate_session_fixture

        try:
            result = aggregate_session_fixture(args.session_timeframe)
        except ValueError as exc:
            parser.error(str(exc))
        print("Offline XNYS fixture (missing 2000-01-10; holiday 2000-01-17):")
        print(result.candles.to_string())
        print("\nSession-window metadata:")
        print(result.metadata.to_string())
        return 0
    if args.demo_aggregation:
        from chronochart.demo import aggregate_fixture

        result = aggregate_fixture(incomplete=args.incomplete)
        print("Offline fixture candles:")
        print(result.candles.to_string())
        print("\nCompleteness metadata:")
        print(result.metadata.to_string())
        return 0
    parser.print_help()
    return 0
