# Changelog

## 0.2.3 (2026-11-11)

- Read Excel (`.xlsx`) files as sources. (#13, thanks @adeebaimam)
- `--max-examples` shows more than one example row per finding in the text report. (#15, thanks @painbaba)
- Tests for the `pipeverify init` command. (#14, thanks @painbaba)
- README: PyPI downloads badge.

## 0.2.2 (2026-10-23)

- Read compressed CSV and JSON files (`.csv.gz`, `.json.gz`, `.jsonl.gz`, `.zst`). Compressed
  Parquet gets a clear error, since Parquet is already compressed internally. (#8, thanks @merwin14)
- Docs: Postgres, Snowflake and BigQuery source examples in `docs/sources.md`. (#7, thanks @merwin14)

## 0.2.1 (2026-10-21)

- Fix: works with DuckDB 2.0, which no longer allows a new query while an earlier
  result is still open. Source checks on DataFrames failed with a ResourceInUseException.
- CI: the demo job now sets up Python before installing packages.

## 0.2.0 (2026-10-21)

- Source checks: compare an output table with the raw source it was built from.
  `source_conflict` (default) flags keys where the source sent conflicting values and the
  output silently kept one. `source_mismatch`, `source_missing` and `source_orphan` are opt-in.
- `before` is now optional, so source checks can run when no earlier snapshot exists
  (`pipeverify compare - after.parquet --source raw.parquet ...`).
- New thresholds for source mismatch and missing keys.

## 0.1.0 (2026-10-21)

First release.

- `pipeverify compare` and `pipeverify run` with PASS / REVIEW / BLOCK verdicts
- Checks: schema, row count, missing and new keys, duplicate keys, null rate, changed values,
  column totals, category values
- Sources: CSV, Parquet, JSON, DuckDB tables and queries, SQLAlchemy databases, pandas DataFrames
- Reports: terminal, Markdown (PR comments, job summary) and JSON
- GitHub Action with PR comment and verdict output
- Optional LLM explanation of findings (off by default; data values not sent)
- Retail demo with five realistic scenarios
