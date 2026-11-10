# Aggregation core — Prompt 3

## What and why

`chronochart.aggregation.aggregate_candles(candles, groups, incomplete="retain")`
reduces OHLCV values for **explicitly supplied groups**. It returns an
`AggregationResult` containing `candles` and `metadata` DataFrames. It is pure:
no provider calls, filesystem access, clock dependence, input mutation, or global
state changes. Returned frames do not alias source values.

The reducer deliberately does not decide what “3 weeks” means. A future timeframe
engine must decide group boundaries, label conventions, expected source counts,
and session/calendar policies. Providers and normalization remain separate
scaffolds; this milestone does not claim that they have been implemented.

## Input and grouping contract

- `candles`: pandas DataFrame with a unique, non-NaT `DatetimeIndex` and unique
  columns containing `Open`, `High`, `Low`, `Close`, `Volume`. Additional columns
  are ignored and remain untouched.
- Nonempty OHLCV columns must be real numeric types, not booleans, complex values,
  or numeric strings. Values are copied into **float64**; ordinary floating-point
  rounding applies, including loss of exactness for integers beyond 2^53. This is
  not an arbitrary-precision money/volume API.
- Reject NaN, pandas NA, infinity, missing columns, duplicate timestamps, invalid
  ranges (`Low <= Open, Close <= High`, and `Low <= High`), and negative volume.
  Zero volume and negative prices are valid. No forward fill, row skipping,
  deduplication, string parsing, timezone conversion, or implicit normalization.
- `groups`: sequence of `CandleGroup(timestamp, members, expected_count)`.
  `members` is a tuple of pandas Timestamps; `expected_count` is a positive int64
  integer, not bool, and must be at least the member count.
- Every source timestamp must belong to **exactly one** group. Repeated membership
  within/across groups, unknown timestamps, and unassigned rows raise `ValueError`.
  To aggregate a subset, explicitly slice the source frame first.
- Group labels must be strictly increasing, unique, non-NaT pandas Timestamps.
  Source timestamps, members, and labels must share the same named timezone,
  or all be timezone-naive. The timezone is preserved, not inferred or converted.
- Members may be unordered or non-contiguous: the reducer does not validate
  calendar/session coverage. Counts cannot detect wrong membership, stale source
  data, absent expected sessions, or a source candle that is still forming.

## Reduction and timestamps

Source rows are sorted chronologically on a private copy. The order of the input
rows and of a group's member tuple cannot change Open/Close.

| Field | Reduction |
|---|---|
| Open | First chronological source Open |
| High | Maximum source High |
| Low | Minimum source Low |
| Close | Last chronological source Close |
| Volume | Sum of source Volume using `math.fsum` |

Reject non-finite/overflowing retained volume sums instead of returning infinity.
Output candles have float64 OHLCV columns and a `DatetimeIndex` named `Timestamp`.
Each output timestamp is **exactly the caller-supplied group label**, not
automatically the first/last source timestamp or a newly inferred boundary.
Output follows the supplied, strictly increasing group-label order.

## Completeness and empty input

`metadata` has the same labeled/timezoned index and includes **every** defined
group, even dropped or empty groups:

| Column | Type | Meaning |
|---|---|---|
| source_count | int64 | Number of source members |
| expected_count | int64 | Caller-supplied expected number of candles |
| is_complete | bool | `source_count == expected_count` |
| retained | bool | Whether an OHLCV row appears in the result |

- `incomplete="retain"` (default): retain nonempty incomplete groups.
- `incomplete="drop"`: omit **any** incomplete group, not only the final group.
  Its completeness metadata is still returned. No silent alternative policy.
- Empty groups are incomplete and always omitted from OHLCV output under either
  policy; no artificial prices or zero-volume candles are synthesized.
- A schema-valid empty input plus no groups returns typed empty frames, preserving
  the index timezone. Empty input may use empty object-typed OHLCV columns.
  Explicit empty groups still produce metadata. A bare DataFrame with no OHLCV
  schema/DatetimeIndex is invalid even when empty.
- Validation applies to **all** input rows before dropping groups. Dropping an
  incomplete group does not hide malformed source data.

`is_complete` is a **count-based** statement, not proof of market-calendar
completeness or that an in-progress candle has closed. The caller must derive an
honest expected count; defaulting it to observed count would hide missing data.

## Fixture demonstration — before downloaded data

Run from the repository in PowerShell:

```powershell
.\.venv\Scripts\python.exe main.py --demo-aggregation
.\.venv\Scripts\python.exe main.py --demo-aggregation --incomplete drop
```

The fixture lives in `src/chronochart/demo.py` and uses no network or local CSV.
Dates are UTC source labels; gaps are intentional. This is explicit grouping,
not a claim about a particular exchange calendar or timeframe.

| Source timestamp | Open | High | Low | Close | Volume |
|---|---:|---:|---:|---:|---:|
| 2025-01-02 | 10 | 13 | 9 | 12 | 100 |
| 2025-01-03 | 12 | 15 | 11 | 14 | 200 |
| 2025-01-06 | 14 | 16 | 13 | 15 | 300 |
| 2025-01-07 | 15 | 18 | 14 | 17 | 400 |
| 2025-01-08 | 17 | 19 | 16 | 18 | 500 |

The caller defines two groups, both expecting three rows:

| Output label | Members | Open | High | Low | Close | Volume | Complete |
|---|---|---:|---:|---:|---:|---:|---|
| 2025-01-02 | First three dates | 10 | 16 | 9 | 15 | 600 | True |
| 2025-01-07 | Last two dates | 15 | 19 | 14 | 18 | 900 | False |

For the first group, Open=10, High=max(13,15,16)=16, Low=min(9,11,13)=9,
Close=15, Volume=100+200+300=600. The final group contains two of three expected
rows, so retain includes it and drop excludes its candle but keeps its metadata.

## Underneath and complexity

Let **n** be source rows and **g** explicit groups, with five fixed OHLCV columns:

1. **Validation/membership:** expected O(n + g) time using timestamp hash maps,
   assuming ordinary constant-time hash lookup. Malformed membership is rejected.
2. **Sorting:** stable chronological sort on the private frame is
   **O(n log n)** worst-case time and **O(n)** auxiliary space. This is separate
   from reduction and is done once, not separately for every group.
3. **Aggregation after sorting:** one chronological scan assigns row positions
   to the supplied group owners, then each group's rows are reduced once.
   Total **O(n + g)** time and **O(n + g)** auxiliary space, including metadata.
   There is no repeated full-frame scan per group. `fsum` improves floating-point
   volume summation but does not provide decimal/arbitrary-precision semantics.

Total worst-case time is O(n log n + g); total auxiliary/output space is O(n + g).
Output-label order is required, so it needs validation O(g), not an extra sort.
This design lets a later timeframe engine supply groups without changing the
reduction rules or adding provider dependencies to the candle core.
