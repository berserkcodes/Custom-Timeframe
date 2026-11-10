# ChronoChart

## 1. Project Goal

Build an independent financial charting application that lets users create **custom candle timeframes** not readily available in standard charting platforms, such as:

- 3 weeks per candle
- 10 trading days per candle
- 45 minutes per candle
- 2 months per candle

ChronoChart will fetch market data, convert it into custom OHLCV candles, and display the results in a chart.

## 2. Core Architecture

```text
Market-data providers
        ↓
Data normalizer (OHLCV)
        ↓
Custom timeframe engine
        ↓
Optional indicators
        ↓
Chart renderer
        ↓
User interface
```

Keep the data provider, aggregation engine, and chart renderer independent. This will make it easier to add providers and change the charting library later.

## 3. Technology Choices

| Component | Initial choice | Purpose |
|---|---|---|
| Language | Python | Data processing and timeframe logic |
| Data handling | pandas | Tabular data, timestamps, grouping |
| Initial stock data | yfinance | Prototype historical stock data |
| Additional data source | Binance API | Crypto data and 24/7-market testing |
| Initial UI | Streamlit (candidate) | Fast prototype interface |
| Chart rendering | TradingView Lightweight Charts (candidate) | Interactive candlestick charts |

**Open-source projects to evaluate:** OpenCharts, OpenTerminal, and OpenTrader. Treat these as possible references or UI foundations, not dependencies. Check their current maintenance, license, data sources, and integration effort before adopting any of them.

## 4. Custom Candle Logic

For a group of source candles:

```text
Open   = first candle's Open
High   = maximum High in the group
Low    = minimum Low in the group
Close  = last candle's Close
Volume = sum of Volume in the group
```

The timeframe engine will group source candles and apply these rules to each group.

**Important:** aggregation can create larger candles from sufficiently granular data, but it cannot reconstruct missing detail. Daily data can produce multi-day or multi-week candles; it cannot produce accurate hourly candles.

## 5. Timeframe Rules to Define

“3 weeks” needs an explicit meaning:

- **Trading-session based:** for a typical US stock prototype, 15 trading sessions per candle.
- **Calendar based:** fixed calendar windows, with market holidays and missing sessions handled.

These definitions produce different candle boundaries. The first prototype can use 15-session groups, then add calendar-aware grouping. Also define how to handle an incomplete final candle and which timezone/calendar applies.

## 6. Build Roadmap

1. **Project setup:** create the project folder, Python virtual environment, and install the initial dependencies.
2. **Market data:** connect the first provider, download historical AAPL data, inspect the OHLCV structure, and save a local sample.
3. **Aggregation:** implement and test candle aggregation using a small example, then apply it to real market data.
4. **Timeframe engine:** support configurable amounts and units, beginning with days and weeks.
5. **Correctness:** test missing data, duplicate timestamps, ordering, partial groups, and timeframe boundaries.
6. **Chart UI:** display generated candles using a simple interface and a chart renderer.
7. **More providers:** add Binance and later other providers through a common OHLCV format.
8. **Advanced features:** indicators, caching, live updates, multiple symbols, and optional browser integration.

## 7. Development Principles

- Correct candle data before visual polish.
- Keep provider, timeframe, and chart responsibilities separate.
- Make timeframe boundaries and market calendars explicit.
- Use libraries for productivity, but understand the operations they perform.
- Avoid premature optimization and avoid building a full TradingView clone.

## 8. Learning and Explanation Format

For important code and design decisions, explain:

1. **What:** what the function, library, or operation does.
2. **Why:** why it is needed and why this approach was chosen.
3. **Underneath:** the underlying concept or algorithm.
4. **Complexity:** time and space complexity where relevant.
5. **Project connection:** how it contributes to ChronoChart.

The assistant can write the code; the goal is to understand the system without requiring the user to write every step manually.

## 9. First Development Steps

Start with a clean project setup:

1. Create a `ChronoChart` folder and open it in VS Code.
2. Create and activate a Python virtual environment.
3. Install the initial dependencies: `yfinance` and `pandas`.
4. Create a small market-data script to download daily AAPL history.
5. Inspect the returned timestamps and OHLCV columns before saving a local CSV sample.
6. Build `aggregate_candles()` with a small, known input example, then use it to create custom candles from the downloaded data.

Verify the generated OHLCV values before moving on to the full timeframe engine or chart UI.

## 10. Backend Execution Agreement

- Repository: https://github.com/berserkcodes/Custom-Timeframe
- Confirmed local folder: `C:\chrono chart`; confirmed branch: `main`.
- Before implementation, verify authenticated repository write access locally. Never share credentials in chat.
- Keep `main.py` thin; provider, normalization, aggregation, and timeframe logic go into separate modules.
- Document virtual-environment setup and local execution in `python main.py --help`.
- After each backend checkpoint, update this plan and generate/update local `BACKEND_SESSION_BACKUP.md`. The backup is Git-ignored: do not commit or push it. Commit and push the plan and implementation to `main` after each milestone.

## 11. Backend Milestone Progress

### Milestone 1 — Project setup

- Implementation: thin entry point, independent CLI module, dependency metadata,
  PowerShell setup documentation, ignore rules, and four offline CLI tests.
- Verification: created `.venv` using Python 3.13.4; editable installation succeeded
  with pandas 3.1.1 and yfinance 0.2.66. Both packages import successfully;
  `pip check` reports no broken requirements. All four CLI tests pass both before
  dependency installation and within the virtual environment. `main.py --help`
  displays setup and execution instructions. The local backup is confirmed Git-ignored.
- Delivery: verified and ready for the milestone commit/push to `main`;
  `BACKEND_SESSION_BACKUP.md` is excluded.
- Next milestone: provider adapter and OHLCV normalization, then inspect and save
  daily AAPL data. No provider, candle aggregation, or UI implementation is claimed yet.

### Setup design notes

- **What:** `main.py` delegates to `chronochart.cli.main()`; `argparse` provides help and version output.
- **Why:** keep entry-point concerns separate from future market-data processing, and make setup help available before third-party dependencies are installed.
- **Underneath:** Python imports the CLI module, parses arguments, then dispatches; currently it prints help without fetching data.
- **Complexity:** argument parsing is linear in argument input size; setup help is fixed-size output. No OHLCV data is allocated.
- **Project connection:** pandas and yfinance are declared now so subsequent provider and aggregation milestones can share a consistent environment.
