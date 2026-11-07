"""Command line interface: pipeverify compare | run | init."""
from __future__ import annotations

import os
import sys

import click

from . import __version__
from .checks import RANK
from .core import RunResult, run_config, verify
from .report import to_json, to_markdown, to_text

SAMPLE_CONFIG = """\
# pipeverify config: one entry per table you want to check after a change.
# before/after can be a file (csv, parquet, json), duckdb:<file.db>:<table>,
# or a mapping such as {url: postgresql://..., table: schema.table}.
thresholds:
  row_change_block_pct: 10
tables:
  - name: gold.daily_sales
    before: snapshots/before/daily_sales.parquet
    after: snapshots/after/daily_sales.parquet
    key: [store_id, sale_date]
    expect_change: []        # columns this change is meant to modify
    ignore: [loaded_at]      # columns that always change, such as load timestamps
"""


def _emit(result: RunResult, fmt: str, output: str | None, summary: str | None, show_info: bool,
          json_out: str | None = None, markdown_out: str | None = None, max_examples: int = 1) -> None:
    for path, render in ((json_out, to_json), (markdown_out, to_markdown)):
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                body = render(result)
                fh.write(body if body.endswith("\n") else body + "\n")
    text = {"text": lambda: to_text(result, show_info, max_examples), "markdown": lambda: to_markdown(result),
            "json": lambda: to_json(result)}[fmt]()
    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
        if fmt != "text":
            click.echo(to_text(result, show_info, max_examples))
    else:
        click.echo(text)
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(to_markdown(result))


def _maybe_explain(result: RunResult, explain: bool) -> None:
    if not explain:
        return
    from .explain import ExplainError, explain as do_explain
    try:
        result.explanation = do_explain(result)
    except ExplainError as exc:
        click.echo(f"note: explanation skipped: {exc}", err=True)
    except Exception as exc:  # network or provider errors must not hide the verdict
        click.echo(f"note: explanation failed: {exc}", err=True)


def _exit(result: RunResult, fail_on: str) -> None:
    if fail_on == "never":
        sys.exit(0)
    threshold = RANK[fail_on]
    sys.exit(1 if RANK[result.verdict] >= threshold else 0)


output_options = [
    click.option("--format", "fmt", type=click.Choice(["text", "markdown", "json"]), default="text",
                 show_default=True, help="Report format."),
    click.option("--output", "-o", type=click.Path(dir_okay=False), help="Write the report to a file."),
    click.option("--summary", type=click.Path(dir_okay=False), envvar="GITHUB_STEP_SUMMARY",
                 help="Append a Markdown report here (set automatically in GitHub Actions)."),
    click.option("--fail-on", type=click.Choice(["review", "block", "never"]), default="block",
                 show_default=True, help="Exit with code 1 at this verdict or worse."),
    click.option("--explain", is_flag=True, help="Add a plain-English explanation from an LLM (needs an API key)."),
    click.option("--show-info", is_flag=True, help="Also print informational findings."),
    click.option("--json-out", type=click.Path(dir_okay=False), help="Also write a JSON report here."),
    click.option("--markdown-out", type=click.Path(dir_okay=False), help="Also write a Markdown report here."),
    click.option("--max-examples", type=int, default=1, show_default=True,
                 help="Example rows to show per finding."),
]


def with_output_options(fn):
    for opt in reversed(output_options):
        fn = opt(fn)
    return fn


@click.group()
@click.version_option(__version__, prog_name="pipeverify")
def main() -> None:
    """Verify that a pipeline change left the data right, not just the job green."""


@main.command()
@click.argument("before")
@click.argument("after")
@click.option("--source", "source_data", help="Raw source table the output was built from (enables source checks).")
@click.option("--source-key", help="Key column(s) in the source, if named differently from --key.")
@click.option("--source-column", "source_columns", multiple=True,
              help="Column to check against the source, as out=src or just name. Repeatable.")
@click.option("--source-where", help="SQL filter for source rows that should reach the output.")
@click.option("--key", "-k", help="Comma-separated key column(s) that identify a row.")
@click.option("--expect-change", "-e", multiple=True, help="Column this change is meant to modify. Repeatable.")
@click.option("--ignore", "-i", multiple=True, help="Column to skip, such as a load timestamp. Repeatable.")
@click.option("--expect-row-change", is_flag=True, help="Rows are expected to be added or removed.")
@click.option("--name", default=None, help="Name to show for the table.")
@with_output_options
def compare(before, after, source_data, source_key, source_columns, source_where,
            key, expect_change, ignore, expect_row_change, name,
            fmt, output, summary, fail_on, explain, show_info, json_out, markdown_out, max_examples):
    """Compare one table BEFORE and AFTER a change.

    Use - as BEFORE to run only the source checks.

    \b
    Examples:
      pipeverify compare before.parquet after.parquet --key order_id
      pipeverify compare - silver.parquet -k txn_id --source raw.parquet --source-column amount
      pipeverify compare duckdb:prod.db:gold.sales duckdb:dev.db:gold.sales -k id
    """
    import time
    start = time.perf_counter()
    source = None
    if source_data:
        cols = dict((c.split("=", 1) if "=" in c else (c, c)) for c in source_columns)
        source = {"data": source_data, "key": source_key, "columns": cols, "where": source_where}
    try:
        res = verify(None if before == "-" else before, after, name=name or os.path.basename(str(after)), key=key,
                     expect_change=list(expect_change), ignore=list(ignore),
                     expect_row_change=expect_row_change, source=source)
    except Exception as exc:
        raise click.ClickException(str(exc))
    result = RunResult(tables=[res], seconds=time.perf_counter() - start)
    _maybe_explain(result, explain)
    _emit(result, fmt, output, summary, show_info, json_out, markdown_out, max_examples)
    _exit(result, fail_on)


@main.command()
@click.option("--config", "-c", default="pipeverify.yml", show_default=True,
              type=click.Path(exists=True, dir_okay=False), help="Config file listing the tables.")
@click.option("--table", "-t", multiple=True, help="Only check these table names. Repeatable.")
@with_output_options
def run(config, table, fmt, output, summary, fail_on, explain, show_info, json_out, markdown_out, max_examples):
    """Check every table listed in a config file."""
    try:
        result = run_config(config, only=list(table) or None)
    except Exception as exc:
        raise click.ClickException(str(exc))
    if not result.tables:
        raise click.ClickException("No tables matched")
    _maybe_explain(result, explain)
    _emit(result, fmt, output, summary, show_info, json_out, markdown_out, max_examples)
    _exit(result, fail_on)


@main.command()
@click.option("--path", default="pipeverify.yml", show_default=True)
def init(path):
    """Write a starter config file."""
    if os.path.exists(path):
        raise click.ClickException(f"{path} already exists")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(SAMPLE_CONFIG)
    click.echo(f"Wrote {path}. Edit the table entries, then run: pipeverify run")


if __name__ == "__main__":  # pragma: no cover
    main()
