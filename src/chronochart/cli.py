"""Command-line parsing, help, and application dispatch only.

Transport, normalization, and aggregation stay in separate modules. Binance is
public-data only; live requests are opt-in and require explicit UTC-aware bounds.
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
  python main.py --demo-time-windows --window-mode "3 calendar weeks" --market equity
  python main.py --demo-time-windows --window-mode "2 calendar months" --market crypto
  python main.py --demo-time-windows --window-mode "45-minute intraday" --market equity
  python main.py --demo-providers
  python main.py run --help
  python main.py run --provider fixture-equity --symbol AAPL --start 2024-01-01 --end 2024-05-01 --as-of 2024-06-01T00:00:00Z --source-interval 1d --target-mode trading-sessions --target-amount 10 --target-unit sessions --output data/exports/aapl10.json
  python main.py run --provider fixture-equity --symbol AAPL --start 2024-01-01 --end 2024-05-01 --as-of 2024-06-01T00:00:00Z --source-interval 1d --target-mode trading-sessions --target-amount 15 --target-unit sessions --output data/exports/aapl15.json
  python main.py run --provider fixture-equity --symbol AAPL --start 2024-01-01 --end 2024-05-01 --as-of 2024-06-01T00:00:00Z --source-interval 1d --target-mode calendar --target-amount 3 --target-unit weeks --output data/exports/aapl3weeks.json
  python main.py run --provider fixture-equity --symbol AAPL --start 2024-01-01 --end 2024-05-01 --as-of 2024-06-01T00:00:00Z --source-interval 1d --target-mode calendar --target-amount 2 --target-unit months --output data/exports/aapl2months.json
  python main.py run --provider fixture-crypto --symbol BTCUSDT --start 2024-02-29 --end 2024-03-01 --as-of 2024-03-02T00:00:00Z --source-interval 15m --target-mode intraday --target-amount 45 --target-unit minutes --output data/exports/crypto45.json
  python main.py --live-binance-check --symbol BTCUSDT --interval 15m --start 2025-07-03T13:30:00Z --end 2025-07-03T17:00:00Z --as-of 2025-07-07T00:00:00Z
  python -m unittest discover -s tests -v

If activation is blocked, no execution-policy change is necessary:
  .\\.venv\\Scripts\\python.exe -m pip install -r requirements-build.txt
  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.lock.txt
  .\\.venv\\Scripts\\python.exe -m pip install -e . --no-deps --no-build-isolation
  .\\.venv\\Scripts\\python.exe main.py --help

Status:
  Aggregation, anchored session/calendar modes, and 45-minute windows implemented.
  Equity: America/New_York + XNYS; crypto: UTC 24/7. Intervals [start,end).
  Output labels: UTC left edges. Daily-to-intraday and straddling bars rejected.
  Short final session window: retain by default, explicitly marked in metadata.
  --demo-providers uses equity fixtures + mocked Binance with the same engine.
  run connects fetch/normalize/aggregate/export; fixture-* examples are synthetic.
  run --provider yfinance/binance and --live-binance-check are explicit live IO.
  Optional cache stores raw sources only; failed refresh never uses stale data.
  Binance availability is regional. HTTP403/451 stop; no restriction bypass.
  Open-time selection [start,end); forming bars excluded by default, never split.
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
    demos.add_argument(
        "--demo-time-windows", action="store_true",
        help="demonstrate explicit calendar or 45-minute windows without downloads",
    )
    demos.add_argument("--demo-providers", action="store_true", help="equity + mocked Binance through one aggregation engine (offline)")
    demos.add_argument("--live-binance-check", action="store_true", help="OPTIONAL public Binance network request; regional availability applies")
    parser.add_argument("--symbol", default="BTCUSDT", help="live Binance uppercase symbol (default: BTCUSDT)")
    parser.add_argument("--interval", default="15m", help="live Binance source interval (default: 15m, not45m)")
    parser.add_argument("--start", help="live check inclusive OPEN-time bound, aware ISO timestamp")
    parser.add_argument("--end", help="live check exclusive OPEN-time bound, aware ISO timestamp")
    parser.add_argument("--as-of", help="explicit aware candle-maturity instant (not point-in-time replay)")
    parser.add_argument("--forming", choices=("exclude", "include", "raise"), default="exclude", help="live forming-candle policy")
    parser.add_argument(
        "--window-mode", default="45-minute intraday",
        choices=("3 calendar weeks", "2 calendar months", "45-minute intraday"),
        help="explicit calendar/intraday fixture mode (not a trading-week alias)",
    )
    parser.add_argument(
        "--market", choices=("equity", "crypto"), default="equity",
        help="time-window fixture market: equity XNYS or crypto UTC 24/7",
    )
    parser.add_argument(
        "--session-timeframe", default="10 trading sessions",
        help="session-demo timeframe, e.g. '15 trading sessions' or '3 trading weeks'",
    )
    parser.add_argument(
        "--incomplete", choices=("retain", "drop"), default="retain",
        help="incomplete fixture-group policy (default: retain)",
    )
    from chronochart.run_cli import add_run_command, run_from_args

    add_run_command(parser)
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_from_args(parser, args)
    if args.demo_providers:
        from chronochart.demo import aggregate_provider_fixtures

        for name, result in aggregate_provider_fixtures().items():
            print(f"\n{name} (offline, same aggregation engine):")
            print(result.candles.to_string())
            print(result.metadata[["expected_count", "is_complete", "short_window"]].to_string())
        return 0
    if args.live_binance_check:
        if not all((args.start, args.end, args.as_of)):
            parser.error("--live-binance-check requires --start, --end, and --as-of")
        import pandas as pd
        from chronochart.binance import BinanceProvider
        from chronochart.providers import HistoricalRequest, ProviderError

        try:
            request = HistoricalRequest(
                args.symbol, args.interval, pd.Timestamp(args.start), pd.Timestamp(args.end),
                pd.Timestamp(args.as_of), forming=args.forming,
            )
            with BinanceProvider() as provider:
                result = provider.fetch_history(request)
        except (ValueError, ProviderError) as exc:
            parser.error(str(exc))
        print(f"Fetched {len(result.candles)} bars (preview: first20):")
        print(result.candles.head(20).to_string())
        print("\nBar maturity metadata:")
        print(result.metadata.head(20).to_string())
        return 0
    if args.demo_time_windows:
        from chronochart.demo import aggregate_window_fixture

        result = aggregate_window_fixture(args.window_mode, args.market)
        print(f"Offline {args.window_mode} fixture ({args.market}):")
        print(result.candles.to_string())
        print("\nWindow coverage metadata:")
        print(result.metadata.to_string())
        return 0
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
