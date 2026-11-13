"""pipeverify as an MCP server, so an AI agent can check its own data change before anyone trusts it.

Run it with ``pipeverify mcp`` (or ``uvx pipeverify mcp``) and point any MCP
client at that command: Claude Desktop, Claude Code, Cursor, VS Code or your own agent.

Every tool is read-only. Snapshots are opened read-only, the checks run in an in-memory DuckDB, and
nothing is written to your files, database or warehouse. The agent gets a verdict (pass, review or
block) with the evidence behind it; it cannot change data through this server.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, Union

from pydantic import Field
from typing_extensions import TypedDict  # pydantic needs this TypedDict on Python < 3.12

SOURCE_FORMS = (
    "A file path (.csv, .parquet, .json, .xlsx; CSV and JSON may be .gz or .zst), "
    "'duckdb:<file.db>:<table or SELECT>', 'snowflake:<DB.SCHEMA.TABLE or SELECT>', 'dbt:<model>', "
    "or an object such as {\"dbt\": \"fct_orders\", \"project_dir\": \"analytics\", \"target\": \"prod\", "
    "\"manifest\": \"prod-manifest.json\"}, {\"snowflake\": \"DB.SCHEMA.T\", \"connection\": \"name\", \"where\": \"...\"} "
    "or {\"url\": \"<SQLAlchemy URL>\", \"table\": \"schema.table\"}. Relative paths are relative to the server's working folder."
)
Source = Union[str, Dict[str, Any]]
Key = Annotated[Optional[List[str]], Field(description=(
    "Column or columns that together identify one row, for example [\"order_id\"] or [\"store_id\", \"sale_date\"]. "
    "Without a key only table-level checks run (row counts, schema, null rates, totals), so give one whenever the table has it."))]


class FindingOut(TypedDict):
    check: str
    severity: str
    message: str
    column: Optional[str]
    metrics: Dict[str, Any]


class TableOut(TypedDict):
    name: str
    verdict: str
    before: str
    after: str
    stats: Dict[str, Any]
    error: Optional[str]
    findings: List[FindingOut]


class Report(TypedDict):
    verdict: str
    headline: str
    tables: List[TableOut]
    seconds: float
    version: str


INSTRUCTIONS = (
    "pipeverify checks that a change to a data pipeline left the data right, not just the job green. It compares "
    "a table before and after the change (files, DuckDB, Snowflake, dbt models or any SQL database) and can compare "
    "an output table with the raw source it was built from. The verdict is 'pass' (safe), 'review' (a person "
    "should look) or 'block' (the change harmed the data). Call compare_tables after you change a transformation, "
    "query, dbt model or load, before you report the work as done. Report every review and block finding to the "
    "user in plain words and do not work around them. These tools only read; they never change data."
)


def _headline(result) -> str:
    counts = {s: sum(f.severity == s for t in result.tables for f in t.findings) for s in ("block", "review")}
    names = ", ".join(t.name for t in result.tables)
    if result.verdict == "pass":
        return f"PASS: no harmful differences found in {names}."
    parts = [f"{n} {s} finding{'s' if n != 1 else ''}" for s, n in counts.items() if n]
    action = "do not apply or merge this change until it is fixed" if result.verdict == "block" \
        else "ask a person to confirm these differences are intended"
    return f"{result.verdict.upper()}: {' and '.join(parts)} in {names}; {action}."


def _report(result) -> Report:
    d = result.to_dict()
    return {"verdict": d["verdict"], "headline": _headline(result), "tables": d["tables"],
            "seconds": d["seconds"], "version": d["version"]}


def _one(table_result, seconds: float) -> Report:
    from .core import RunResult

    return _report(RunResult(tables=[table_result], seconds=seconds))


def compare_tables(
        before: Annotated[Source, Field(description="The table as it was before the change (for example production, or yesterday's snapshot). " + SOURCE_FORMS)],
        after: Annotated[Source, Field(description="The same table after the change (for example the pull request's build). Same forms as 'before'.")],
        key: Key = None,
        expect_change: Annotated[Optional[List[str]], Field(description=(
            "Columns this change is meant to modify. Differences in them are reported as expected instead of flagged. "
            "Omit when the change should not alter any existing value."))] = None,
        ignore: Annotated[Optional[List[str]], Field(description="Columns to skip because they always differ, such as load timestamps or run ids.")] = None,
        expect_row_change: Annotated[bool, Field(description="Set true when the change is meant to add or remove rows, so a row-count change alone is not flagged.")] = False,
        name: Annotated[Optional[str], Field(description="Name to show for the table in the report, for example gold.daily_sales. Defaults to 'table'.")] = None,
        thresholds: Annotated[Optional[Dict[str, Any]], Field(description=(
            "Optional overrides for when a difference becomes review or block, in percent of rows, for example "
            "{\"row_change_block_pct\": 20, \"sum_shift_review_pct\": 1}. Omit to use the defaults."))] = None,
) -> Report:
    """Compare one table before and after a pipeline change and say whether the change harmed the data.

    It looks for the damage that a green job and passing tests miss: rows lost or duplicated (a join that
    fans out, a dedupe that drops too much), keys that disappeared, values that changed in columns the
    change was not meant to touch, a jump in nulls, a shifted total (a unit or currency change), new or
    vanished categories, and schema or type changes.

    Use this after you edit a SQL query, dbt model, Spark job or load, or after an agent applies a fix,
    and before you report the work as done, merge it or publish the table. Use check_against_source
    instead when there is no earlier snapshot but you have the raw input the table was built from, and
    run_config when the project already has a pipeverify.yml listing its tables.

    Behavior: read-only and repeatable. Both snapshots are read (files are opened read-only; Snowflake,
    dbt and SQL sources run one SELECT each, with credentials already configured on this machine, never
    passed as arguments) and loaded into a temporary in-memory DuckDB where the checks run. Nothing is
    written anywhere. Each snapshot is loaded whole, so for a large warehouse table pass a SELECT or a
    'where' filter covering the slice you changed. Usually takes seconds. Fails with a readable error
    if a file, table, dbt model, key column or login is wrong.

    Returns verdict ("pass", "review" or "block"), headline (one sentence you can show the user), and
    tables: one entry with stats (row counts and more) and findings, each with check, severity, message,
    column and metrics. On "review" or "block", tell the user every finding and do not apply the change.
    """
    import time

    from .core import verify

    start = time.perf_counter()
    res = verify(before, after, name=name or "table", key=key, expect_change=expect_change, ignore=ignore,
                 expect_row_change=expect_row_change, thresholds=thresholds)
    return _one(res, time.perf_counter() - start)


def check_against_source(
        table: Annotated[Source, Field(description="The output table to check (for example a silver or gold table). " + SOURCE_FORMS)],
        source: Annotated[Source, Field(description="The raw input the output was built from (for example the bronze table or landing file). Same forms as 'table'.")],
        key: Annotated[List[str], Field(description="Column or columns that identify one row in the output, for example [\"txn_id\"].")],
        source_key: Annotated[Optional[List[str]], Field(description="The key column or columns in the source, in the same order as 'key', when they are named differently. Omit if the names match.")] = None,
        columns: Annotated[Optional[Dict[str, str]], Field(description=(
            "Columns to compare, as {output column: source column}, for example {\"amount\": \"amt\"}. "
            "Needed for the conflict and mismatch checks; omit to check only keys."))] = None,
        where: Annotated[Optional[str], Field(description="SQL filter for the source rows that should reach the output, for example \"status <> 'test'\". Omit when every source row should.")] = None,
        checks: Annotated[Optional[List[str]], Field(description=(
            "Which checks to run, from: conflict (the source has conflicting values for a key and the output silently kept one), "
            "mismatch (an output value differs from its source value), missing (a source key is absent from the output), "
            "orphan (an output key is absent from the source). Defaults to [\"conflict\"]."))] = None,
        name: Annotated[Optional[str], Field(description="Name to show for the table in the report. Defaults to 'table'.")] = None,
) -> Report:
    """Check an output table against the raw source it was built from, when there is no "before" to compare.

    It catches a transformation that looks clean but decided something it had no basis for: the source
    sent two different amounts for one key and the output kept one without saying so, values that no
    longer match the source, source rows that never arrived, or output rows with no source at all.

    Use this for a new table, a first load, or a dedupe or repair step, where compare_tables has nothing
    to compare with. Use compare_tables when you do have the table from before the change; you can run
    both for the strongest check.

    Behavior: read-only and repeatable. The table and the source are each read once (files are opened
    read-only; Snowflake, dbt and SQL sources run one SELECT each, with credentials already configured
    on this machine) and loaded into a temporary in-memory DuckDB. Nothing is written anywhere. Usually
    takes seconds. Fails with a readable error if a file, table, key or column does not exist.

    Returns verdict ("pass", "review" or "block"), headline (one sentence you can show the user), and
    tables: one entry with stats and findings, each with check, severity, message, column and metrics.
    A "review" here usually means the source itself is ambiguous, so a person must choose; say so.
    """
    import time

    from .core import verify

    start = time.perf_counter()
    spec = {"data": source, "key": source_key, "columns": columns or {}, "where": where, "checks": checks}
    res = verify(None, table, name=name or "table", key=key, source=spec)
    return _one(res, time.perf_counter() - start)


def run_config(
        config: Annotated[str, Field(description="Path to a pipeverify config file, usually pipeverify.yml in the project root. It lists each table with its before, after, key and options.")] = "pipeverify.yml",
        tables: Annotated[Optional[List[str]], Field(description="Only check the tables with these names (the 'name' entries in the config). Omit to check every table.")] = None,
) -> Report:
    """Check every table listed in a project's pipeverify.yml and return one overall verdict.

    Use this when the project already has a pipeverify config, which is the same check its CI runs, so
    you see what the pull request check will say before you push. Use compare_tables for a one-off
    comparison of two snapshots without a config file.

    Behavior: read-only and repeatable. Reads the config, then reads each table's snapshots (files,
    DuckDB, Snowflake, dbt models or SQL databases, using credentials already configured on this
    machine) into a temporary in-memory DuckDB. Nothing is written anywhere. Time grows with the number
    and size of the tables. A table that cannot be read is reported as "block" with the reason in its
    error field, and the remaining tables are still checked. Fails with a readable error if the config
    file is missing, is not valid, or no table matches.

    Returns verdict (the worst across tables: "pass", "review" or "block"), headline, and tables: one
    entry per table with its own verdict, stats, error and findings. Tell the user every review and
    block finding.
    """
    import os

    from .core import run_config as run

    if not os.path.exists(config):
        raise ValueError(f"{config} not found. Give the path to a pipeverify config file, or use compare_tables.")
    result = run(config, only=tables or None)
    if not result.tables:
        raise ValueError(f"No tables in {config} matched {tables}")
    return _report(result)


TOOLS = (compare_tables, check_against_source, run_config)
TITLES = {
    "compare_tables": "Compare a table before and after a change",
    "check_against_source": "Check a table against its raw source",
    "run_config": "Run the project's pipeverify checks",
}


def build_server():
    """Create the MCP server. Works with the MCP Python SDK 1.x (FastMCP) and 2.x (MCPServer)."""
    import functools

    try:
        from mcp.server.mcpserver import MCPServer as Server
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server
            from mcp.server.fastmcp.exceptions import ToolError
        except ImportError:
            raise SystemExit("The MCP server needs Python 3.10 or newer and the mcp package: pip install mcp") from None
    from mcp.types import ToolAnnotations

    def visible_errors(fn):
        # Input problems (missing file, wrong column, refused login) go back to the agent as a readable message.
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (ValueError, FileNotFoundError) as e:
                raise ToolError(str(e)) from None
            except Exception as e:  # duckdb errors such as a missing table or column
                raise ToolError(f"{type(e).__name__}: {e}") from None
        return wrapper

    server = Server(name="pipeverify", instructions=INSTRUCTIONS)
    for fn in TOOLS:
        # Every tool only reads, and the same inputs give the same answer. Sources can be a remote warehouse.
        hints = ToolAnnotations.model_validate({"title": TITLES[fn.__name__], "readOnlyHint": True, "destructiveHint": False,
                                                "idempotentHint": True, "openWorldHint": True})
        server.tool(annotations=hints)(visible_errors(fn))
    return server


def main() -> None:
    build_server().run()
