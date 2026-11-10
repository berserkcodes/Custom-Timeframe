# Trading-session timeframes — Prompt 4

## What, why, and selected exchange

`TradingSessionTimeframe(10)` and `TradingSessionTimeframe(15)` count **NYSE
(`XNYS`) exchange sessions**, not calendar days or calendar weeks. Any positive
int64 count is configurable, subject to the complete window fitting the supported
calendar horizon. `TradingSessionTimeframe.parse("3 trading weeks")` is a
**prototype alias for exactly 15 sessions**. Holidays can make those sessions
span more than three calendar weeks. Ambiguous units like `3 weeks` or `10 days`
are rejected; calendar timeframes are not implemented.

Schedules come from the maintained [exchange-calendars project](https://github.com/gerrymanoim/exchange_calendars),
not pandas business-day arithmetic or a homegrown holiday list. PyPI verified
release **4.13.2** was published 2026-03-10; it is pinned with its transitive
dependencies in `requirements.lock.txt`. See [PyPI](https://pypi.org/project/exchange-calendars/4.13.2/).
The verified setup uses pandas **3.0.6** (`<3.1`): pandas 3.1 introduced a timezone
`.values` deprecation inside this calendar version during strict tests. No warnings
are globally suppressed. Reevaluate the cap when upgrading the calendar library.

## Stable grouping anchor

The first session is **2000-01-03**; its ordinal is zero. For N sessions per
candle, session ordinal `i` belongs to group `i // N`. That group starts at
ordinal `(i // N) * N` and ends at ordinal `((i // N) + 1) * N - 1`.

- Labels are the **full window's first scheduled session date**, UTC midnight.
  A truncated query can legitimately return a label earlier than its start date.
- Ordinals come from the full exchange schedule, never observed row positions or
  the query's start date. Missing source rows cannot shift subsequent boundaries.
- Query date bounds are inclusive and must lie between 2000-01-03 and 2099-12-31.
  A fixed schedule through 2101-12-31 supplies full-window lookahead. Oversized
  windows that exceed that horizon raise rather than guessing expected counts.
- Calendar release changes can revise historical/emergency holidays and therefore
  ordinals. Stability is **within the pinned calendar revision**, not across
  arbitrary dependency upgrades. Metadata attrs record exchange, calendar version,
  and anchor. Review/recompute affected data deliberately when upgrading.
- Future calendar dates are forecasts under that version's rules, not guarantees
  against newly announced closures. No live exchange notices are fetched.

Hand-checked anchor examples:

| Size | First window | Next window starts |
|---|---|---|
| 10 sessions | 2000-01-03 through 2000-01-14 | 2000-01-18 |
| 15 sessions / 3 trading weeks | 2000-01-03 through 2000-01-24 | 2000-01-25 |

Weekends and MLK Day (2000-01-17) are excluded by XNYS, not counted as missing data.

## API and source-label contract

```python
import pandas as pd
from chronochart.timeframes import TradingSessionTimeframe, build_trading_session_groups
from chronochart.pipeline import aggregate_trading_sessions

timeframe = TradingSessionTimeframe.parse("3 trading weeks")
options = dict(
    start="2025-01-02", end="2025-03-31",
    as_of=pd.Timestamp("2025-04-01T12:00:00Z"),
    leading="retain", trailing="retain", unfinished="retain", missing="raise",
)
# candles must first be supplied by the caller in the OHLCV/session-label schema.
plan = build_trading_session_groups(candles.index, timeframe, **options)
result = aggregate_trading_sessions(candles, timeframe, **options)
```

- Exactly one source OHLCV row per observed session: unique, non-NaT **UTC
  midnight date labels**. These denote exchange dates, not actual market-open
  instants. Raw intraday bars, local-midnight labels, weekends, holidays, and
  out-of-request rows are rejected, not silently normalized or discarded.
- `start`/`end` are exact ISO dates or midnight naive/UTC pandas Timestamps;
  naive request dates are explicitly interpreted as UTC **date labels**.
- `as_of` is a required timezone-aware pandas Timestamp (converted to UTC), not
  an implicit current clock. Repeated calls with the same inputs are deterministic.
- Source rows for sessions that have not opened at `as_of` are invalid. An opened
  but still-forming session may be present; it never proves a completed window.
- Actual exchange close instants determine maturity, including DST and early
  closes. For example, 2025-07-03 closes at **17:00 UTC**, not the usual 20:00 UTC.
- The planner never reduces prices. Its groups partition every source row, even
  for windows marked not retained. The composition layer validates/reduces every
  row with the Prompt 3 core, then applies policies without hiding invalid OHLCV.

## Holidays, request truncation, missing data, and unfinished windows

These are independent conditions; retain/drop behavior is explicit:

| Condition | Meaning | Default / alternatives |
|---|---|---|
| Holiday/weekend/known closure | No scheduled exchange session | Not expected, not missing |
| Leading partial | Full window starts before request `start` | `leading="retain"` or `"drop"` |
| Trailing partial | Full window ends after request `end` | `trailing="retain"` or `"drop"` |
| Unfinished window | Full window's final scheduled close is after `as_of` | `unfinished="retain"` or `"drop"` |
| Missing source coverage | Closed **requested** session absent from source | `missing="raise"`, `"retain"`, or `"drop"` |

- Missing coverage raises `MissingSessionsError`, with a `.sessions` tuple of
  expected-but-absent session labels. Retain/drop must be chosen explicitly to
  proceed; these policies never regroup subsequent rows.
- Sessions outside the request are **not** diagnosed as missing. Leading/trailing
  flags make that truncation visible. This avoids falsely accusing the provider
  of missing rows it was never asked to return.
- Requested sessions whose close is still in the future are **pending**, not
  missing. They remain in the full expected count. An unfinished window can also
  have missing coverage from an earlier, already-closed requested session.
- Retention requires at least one observed row and passing **all** policy gates.
  Leading, trailing, unfinished, and missing policies are independent; a window
  can have more than one reason to be omitted. Metadata remains for all windows.
- Empty windows never synthesize OHLCV. A weekend/holiday-only request yields typed
  empty outputs. An empty request over closed expected sessions raises by default;
  explicit `missing="retain"` returns empty candles plus missing-session metadata.
- Valid rows are never forward-filled or shifted to hide gaps.

## Metadata and completeness

`SessionGroupPlan.metadata` and the composition result include:

- `window_start`, `window_end`: full expected first/last session labels.
- `window_close`: full window's last exchange close instant (UTC).
- `expected_count`: full configured N, independent of query truncation/data gaps.
- `requested_count`: scheduled sessions within the inclusive request/window overlap.
- `source_count`: observed members within that overlap.
- `closed_requested_count`: requested sessions with exchange close <= `as_of`.
- `missing_sessions`: absent closed requested session labels, as a tuple.
- `pending_sessions`: requested session labels whose close is > `as_of`, whether
  already represented by a still-forming source row or not.
- `leading_partial`, `trailing_partial`, `window_closed` as defined above.
- `coverage_complete`: no missing **closed requested** sessions. This can be true
  for a truncated/unfinished window; it does not mean all N rows exist.
- `count_complete`: `source_count == expected_count` (the core reducer's definition).
- `is_complete`: `count_complete and window_closed`; the window has all scheduled
  rows and its final close has elapsed. This still assumes supplied closed-session
  rows are finalized/correct; providers may publish late corrections.
- `retained`: whether the policy permits a nonempty output candle.

Do not treat `coverage_complete` alone as a finished candle. Likewise, count N
including today's forming row is not complete until the final scheduled close.
Metadata attrs record `exchange`, `calendar_version`, and `anchor` for provenance.

## Offline fixture before market data

```powershell
.\.venv\Scripts\python.exe main.py --demo-trading-sessions
.\.venv\Scripts\python.exe main.py --demo-trading-sessions --session-timeframe "15 trading sessions"
.\.venv\Scripts\python.exe main.py --demo-trading-sessions --session-timeframe "3 trading weeks"
```

The fixture has the 20 scheduled sessions from 2000-01-03 through 2000-01-31,
with **2000-01-10 deliberately absent**. It explicitly opts into `missing="retain"`.
The first 10-session window has 9 observed rows and missing coverage. The second
still starts **2000-01-18**, has 10 rows, and is complete. January 17 is a holiday,
not an error. No provider/downloaded data or network is used by the demo.

## Underneath, complexity, and project connection

The calendar schedule is built/cached once for the fixed horizon. Let **S** be
the schedule length, **n** observed rows, **q** requested sessions, **g** windows,
and **N** the configured size. The intersecting full windows contain at most
q + 2N sessions (edge lookaround).

- Calendar construction is library-defined and paid on the first call; retained
  schedule storage is O(S). No claim that computing holiday rules is constant time.
- Index/source validation uses calendar membership/lookups, bounded by O(S+n)
  expected work with pandas timestamp hashing; request-bound searches are O(log S).
- Building windows/metadata takes O(q + g + N) expected work, with O(n+q+g) output
  and temporary storage plus the cached O(S) schedule. No source sorting is needed
  to define membership: calendar order supplies chronological members.
- OHLCV sorting is **separate** in the core: O(n log n) worst-case time/O(n) space.
  Reduction is O(n+g) time/space, not repeated full-frame scans per window.

This extends the Prompt 3 boundary without modifying its provider-independent
arithmetic. Providers and normalization are still separate scaffolds; calendar
days/weeks, intraday timeframes, and other exchanges remain future milestones.
