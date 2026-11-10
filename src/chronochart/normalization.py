"""Provider-independent OHLCV normalization boundary (Prompt 0 scaffold).

Future normalization defines timestamps, timezone, column schema, ordering,
duplicates, and missing-data policy before data reaches the aggregation engine.
No schema or cleaning policy is silently selected by this scaffold.
"""
