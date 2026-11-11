"""Run-subcommand arguments and dispatch only; all business rules are application-owned."""


def add_run_command(parser) -> None:
    run = parser.add_subparsers(dest="command").add_parser(
        "run", help="fetch, normalize, aggregate, and export a versioned JSON bundle",
        description="Explicit provider workflow. fixture-* is offline/synthetic; yfinance/binance are opt-in live providers.",
    )
    run.add_argument("--provider", required=True, choices=("fixture-equity", "fixture-crypto", "yfinance", "binance"))
    run.add_argument("--symbol", required=True)
    run.add_argument("--start", required=True, help="inclusive ISO date or aware timestamp; equity1d uses date labels")
    run.add_argument("--end", required=True, help="exclusive ISO date or aware timestamp")
    run.add_argument("--as-of", required=True, help="explicit aware maturity cutoff, not point-in-time replay")
    run.add_argument("--source-interval", required=True, help="1d or supported minute/hour interval, e.g.15m")
    run.add_argument("--target-mode", required=True, choices=("trading-sessions", "calendar", "intraday"))
    run.add_argument("--target-amount", required=True, type=int)
    run.add_argument("--target-unit", required=True, choices=("sessions", "weeks", "months", "minutes"))
    run.add_argument("--calendar", choices=("XNYS", "24/7"), help="market default; incompatible calendars are rejected")
    run.add_argument("--timezone", choices=("America/New_York", "UTC"), help="market boundary timezone; output labels always UTC")
    run.add_argument("--anchor", help="session date, Monday/month-first midnight, UTC crypto instant, or session-open")
    run.add_argument("--partial-policy", choices=("retain", "drop"), default="retain", help="query-leading/trailing and unfinished target policy")
    run.add_argument("--missing", choices=("raise", "retain", "drop"), default="raise")
    run.add_argument("--forming", choices=("exclude", "include", "raise"), default="exclude")
    run.add_argument("--short-session", choices=("retain", "drop"), default="retain")
    run.add_argument("--output", required=True, help="single JSON bundle: OHLCV + all target/source metadata + provenance")
    run.add_argument("--overwrite", action="store_true", help="explicitly replace an existing export")
    run.add_argument("--cache-dir", help="optional raw-source cache directory (recommended:data/cache)")
    run.add_argument("--cache-ttl", type=float, default=3600, help="seconds, >0 up to30days; never extends a stored expiry")
    run.add_argument("--cache-refresh", action="store_true", help="invalidate before fetching; never fall back after failed refresh")
    run.add_argument("--cache-corrupt", choices=("raise", "refetch"), default="raise")


def run_from_args(parser, args) -> int:
    from chronochart.application import JobSpec, execute_job
    from chronochart.cache import CacheError
    from chronochart.providers import ProviderError

    if any(getattr(args, name, False) for name in ("demo_aggregation", "demo_trading_sessions", "demo_time_windows", "demo_providers", "live_binance_check")):
        parser.error("Do not combine run with a legacy demo/live-check flag")
    try:
        result = execute_job(JobSpec(**{name: getattr(args, name) for name in JobSpec.__dataclass_fields__}))
    except (ValueError, ProviderError, CacheError, OSError) as exc:
        parser.error(str(exc))
    synthetic = "SYNTHETIC OFFLINE FIXTURE" if result.provenance["synthetic"] else "provider data"
    print(f"Exported {len(result.result.candles)} candles and {len(result.result.metadata)} window metadata rows to {args.output}")
    print(f"Source: {synthetic}; cache: {result.provenance['cache']['status']}")
    return 0
