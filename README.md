<p align="center">
  <img src="docs/assets/banner.svg" alt="pipeverify" width="720">
</p>

<h3 align="center">The open-source verification step for data pipeline changes.</h3>

<p align="center">
Check that a change left the data right, not just the job green. pipeverify compares tables
before and after a change, and against the raw source, and returns <b>PASS</b>, <b>REVIEW</b> or <b>BLOCK</b>.
Works with SQL, Spark, dbt, Airflow, or a fix written by an AI agent.
</p>

<p align="center">
  <a href="https://pypi.org/project/pipeverify/"><img src="https://img.shields.io/pypi/v/pipeverify?label=pypi&color=38bdf8" alt="PyPI"></a>
  <a href="https://pepy.tech/projects/pipeverify"><img src="https://static.pepy.tech/personalized-badge/pipeverify?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads" alt="PyPI Downloads"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-4ade80" alt="License"></a>
  <a href="https://github.com/akshaysharma1088/pipeverify/actions/workflows/ci.yml"><img src="https://github.com/akshaysharma1088/pipeverify/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://doi.org/10.5281/zenodo.23542277"><img src="https://zenodo.org/badge/DOI/10.5281/zenodo.23542277.svg" alt="DOI"></a>
  <a href="https://colab.research.google.com/github/akshaysharma1088/pipeverify/blob/main/examples/notebooks/quickstart.ipynb"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open in Colab"></a>
</p>

---


A pipeline change can pass every test, finish on time and keep the schema intact while quietly
dropping rows, duplicating keys or changing what a column means. More of these changes are now
written by AI coding agents, which makes "the job succeeded" an even weaker signal.

pipeverify compares your tables **before and after** a change and gives a verdict:

| Verdict | Meaning | Default CI behaviour |
|---|---|---|
| ✅ **PASS** | The data is the same where it should be. | Merge |
| 🟡 **REVIEW** | Something changed that tests would not catch. A person should look. | Merge allowed, flagged |
| ⛔ **BLOCK** | The change damages the data. | Fail the check |

It works with any tool that produces tables: SQL scripts, Spark jobs, dbt, Airflow tasks or an AI
agent's fix. It needs two snapshots, nothing else.

## Why

In the [PipeMend benchmark](https://github.com/akshaysharma1088/pipemend)
([DOI 10.5281/zenodo.23510784](https://doi.org/10.5281/zenodo.23510784)), LLM agents repaired
64 data pipeline incidents. Production-style data quality monitors approved **every** harmful fix
the agents made. What caught them was comparing the data itself. pipeverify packages that check
so any team can run it on every change.

## Quick start

No install needed to try it: [open the 5-minute notebook in Colab](https://colab.research.google.com/github/akshaysharma1088/pipeverify/blob/main/examples/notebooks/quickstart.ipynb).

```bash
pip install pipeverify

# compare two snapshots of one table
pipeverify compare before/orders.parquet after/orders.parquet --key order_id
```

```
BLOCK   orders.parquet
        rows 1,200 -> 1,000, matched 1,000
        BLOCK  Table lost 200 rows (16.67%)
        BLOCK  200 key(s) present before are gone after (16.67%)
        BLOCK  Total of 'revenue' moved 16.71% (1,383,131.25 -> 1,152,000.00)

Verdict: BLOCK  This change damages the data. Do not merge as is.
```

Try the bundled demo, five realistic changes to a retail sales table:

```bash
git clone https://github.com/akshaysharma1088/pipeverify && cd pipeverify
pip install -e .
python examples/retail/make_demo.py
for s in examples/retail/scenarios/*/; do pipeverify run -c "$s/pipeverify.yml"; done
```

| Scenario | What happened | Verdict |
|---|---|---|
| `1_safe_fix` | Revenue restated for late refunds on one day, as intended | ✅ PASS |
| `2_dedupe_drops_rows` | Duplicates removed on the wrong columns, real days dropped | ⛔ BLOCK |
| `3_join_fanout` | A join to a dimension with a duplicate row doubled one store | ⛔ BLOCK |
| `4_unit_change` | Upstream started sending one region's revenue in cents | ⛔ BLOCK |
| `5_null_creep` | A renamed source field left region empty for new stores | 🟡 REVIEW |

Every one of these runs successfully and keeps the same schema.

## Sources

`before` and `after` can each be:

| Source | CLI form | Config form |
|---|---|---|
| CSV, Parquet, JSON, Excel (.xlsx) file (CSV and JSON can be `.gz` or `.zst` compressed) | `data/orders.parquet` | `path: data/orders.parquet` |
| DuckDB table | `duckdb:warehouse.db:gold.orders` | `{duckdb: warehouse.db, table: gold.orders}` |
| DuckDB query | `"duckdb:warehouse.db:SELECT ..."` | `{duckdb: warehouse.db, query: "SELECT ..."}` |
| Any SQLAlchemy database (Postgres, Snowflake, BigQuery, ...) | | `{url: "postgresql://${PGUSER}@host/db", table: gold.orders}` |
| pandas DataFrame | | Python API |

SQL databases need `pip install "pipeverify[sql]"` plus the driver for your database. Environment
variables in URLs are expanded, so credentials stay out of the config file.

See [SQL source examples](docs/sources.md) for Postgres, Snowflake and BigQuery.

## Config

Check many tables at once with `pipeverify.yml` (`pipeverify init` writes a starter file):

```yaml
thresholds:              # optional, overrides the defaults below for every table
  row_change_block_pct: 10
tables:
  - name: gold.daily_sales
    before: snapshots/main/daily_sales.parquet
    after: snapshots/pr/daily_sales.parquet
    key: [store_id, sale_date]
    expect_change: [revenue]   # columns this change is meant to modify
    ignore: [loaded_at]        # columns that always change
  - name: gold.customers
    before: {url: "${WAREHOUSE_URL}", table: prod.customers}
    after: {url: "${WAREHOUSE_URL}", table: pr_123.customers}
    key: [customer_id]
    expect_row_change: true    # this change is meant to add or remove rows
```

```bash
pipeverify run                     # uses ./pipeverify.yml
pipeverify run --fail-on review    # stricter: fail CI on REVIEW too
pipeverify run --format markdown -o report.md
pipeverify run --json-out report.json
```

Exit code is `1` when the verdict reaches `--fail-on` (default `block`), otherwise `0`.

## What it checks

| Check | Catches | Default |
|---|---|---|
| Schema | Removed, added or retyped columns | removed: BLOCK, retyped: REVIEW |
| Row count | Rows lost or gained | REVIEW > 1%, BLOCK ≥ 10% |
| Missing keys | Rows that existed before and are gone | REVIEW on any, BLOCK ≥ 5% |
| New keys | Rows that did not exist before | REVIEW |
| Duplicate keys | Fan-out from joins, double loads | BLOCK on any new duplicate |
| Null rate | Columns going empty | REVIEW > 1 pt, BLOCK ≥ 10 pts |
| Changed values | Matched rows whose values changed, per column | REVIEW on any, BLOCK ≥ 10% |
| Column totals | Unit changes, partial loads, double counting | REVIEW > 0.5%, BLOCK ≥ 5% |
| Category values | New codes in low-cardinality text columns | REVIEW |

Columns listed in `expect_change` are reported but never raise the verdict. All thresholds can be
changed globally or per table; see [`pipeverify/checks.py`](pipeverify/checks.py) for every option.

## Source checks

A before/after diff only sees damage to rows that already existed. Errors in **new** data need
evidence the pipeline does not control: the raw source. Add a `source` block to a table:

```yaml
tables:
  - name: silver.transactions
    after: snapshots/pr/silver_transactions.parquet
    before: snapshots/main/silver_transactions.parquet   # optional
    key: [txn_id]
    source:
      data: landing/raw_transactions.parquet
      key: [txn_id]
      columns: {amount: amount}      # output column: source column
      checks: [conflict]             # default; also mismatch, missing, orphan
      where: "status = 'POSTED'"     # source rows that should reach the output
```

| Check | Catches | Default |
|---|---|---|
| `conflict` | The source sent several values for one key and the output silently kept one | on, REVIEW |
| `mismatch` | An output value the source never sent (use only for pass-through columns) | opt-in |
| `missing` | Source keys that never reached the output (set `where` for intended filters) | opt-in |
| `orphan` | Output keys that are not in the source | opt-in |

From the command line:

```bash
pipeverify compare - silver.parquet -k txn_id --source raw.parquet --source-column amount
```

In a pilot on the 209 executed fixes in the PipeMend benchmark (25 harmful), production-style
monitors caught 16 harmful fixes, and the source conflict check caught the 9 that the monitors
approved. Together they caught all 25, at the cost of a REVIEW on 21 of 184 correct fixes,
all on incidents where the source data really was ambiguous. This is a small pilot on synthetic
data; a larger evaluation is in progress.

## GitHub Action

```yaml
# .github/workflows/pipeverify.yml
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # ... build the "after" snapshots for this PR here ...
      - uses: akshaysharma1088/pipeverify@v0.2.2
        with:
          config: pipeverify.yml
          fail-on: block      # or review, or never
```

The action posts the report as a PR comment and in the job summary, and exposes the verdict as an
output.

## Python API

```python
import pipeverify

result = pipeverify.verify(before_df, after_df, key="order_id", expect_change=["status"])
print(result.verdict)              # "pass" | "review" | "block"
for f in result.findings:
    print(f.severity, f.message)
```

Useful inside an Airflow task, a notebook, or an AI agent's own loop: let the agent propose a fix,
run `verify`, and only apply the fix on PASS.

## Optional AI explanation

```bash
export ANTHROPIC_API_KEY=...       # or OPENAI_API_KEY with PIPEVERIFY_LLM_MODEL
pip install "pipeverify[ai]"
pipeverify run --explain
```

This adds a short plain-English explanation of what probably happened and what to check next.
It is off by default. Only the findings are sent (check names, counts, percentages, column names);
example values from your data are removed first. The verdict never depends on the LLM.

## Who uses pipeverify

See [ADOPTERS.md](ADOPTERS.md). Using it at work or in a project?
[Tell us how](https://github.com/akshaysharma1088/pipeverify/issues/new?template=share_usage.yml), even
in one line. It helps other teams decide whether to try it and shapes what gets built next.

## Roadmap

- Native adapters for dbt (compare a PR's models against production), Airflow and Spark
- Sampling and pushdown for very large tables, so checks run in the warehouse
- Rules learned from history: what "normal" change looks like for each table
- Agent integration: a guardrail that coding and data agents call before applying a change

Ideas and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[good first issues](https://github.com/akshaysharma1088/pipeverify/labels/good%20first%20issue).
Questions go in [Discussions](https://github.com/akshaysharma1088/pipeverify/discussions).

## Status

Alpha. The checks and verdicts are stable enough to try in CI; options and output formats may
still change before 1.0.

## Citation

If you use pipeverify in research, please cite the PipeMend benchmark that motivated it
(see [CITATION.cff](CITATION.cff)).

## License

Apache-2.0
