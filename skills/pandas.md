---
name: pandas
when: [data, etl]
keywords: [pandas, dataframe, csv]
---
# pandas
- Vectorize; avoid `iterrows`. Use `groupby(...).agg(...)` with named aggregations.
- Parse types at load: `read_csv(..., parse_dates=[...], dtype={...})`.
- Never chain-assign (`df[a][b] = x`); use `.loc`. Don't mutate the caller's frame; return a new one.
- Handle missing values explicitly (`dropna`/`fillna`) and say which in a docstring.
- Tests build a tiny DataFrame inline and compare with `pandas.testing.assert_frame_equal`.
