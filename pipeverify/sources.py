"""Load a table snapshot from a file, a DuckDB database, any SQL database or a DataFrame.

Every source ends up as a table inside one DuckDB connection, where the checks run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import duckdb

FILE_READERS = {
    ".csv": "read_csv_auto",
    ".tsv": "read_csv_auto",
    ".parquet": "read_parquet",
    ".pq": "read_parquet",
    ".json": "read_json_auto",
    ".jsonl": "read_json_auto",
    ".ndjson": "read_json_auto",
    ".xlsx": "read_xlsx",
}


class SourceError(ValueError):
    """Raised when a source spec cannot be understood or loaded."""


@dataclass
class Source:
    """A normalized description of where a table snapshot lives."""

    kind: str  # file | duckdb | sql | dataframe
    path: str | None = None
    table: str | None = None
    query: str | None = None
    url: str | None = None
    frame: Any = None

    def describe(self) -> str:
        if self.kind == "file":
            return self.path or ""
        if self.kind == "duckdb":
            return f"duckdb:{self.path}:{self.table or 'query'}"
        if self.kind == "sql":
            return f"sql:{_redact(self.url or '')}:{self.table or 'query'}"
        return "dataframe"


def _redact(url: str) -> str:
    # hide passwords in connection strings that end up in reports
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return url


def parse_source(spec: Any, base_dir: str = ".") -> Source:
    """Turn a CLI string, a config mapping or a DataFrame into a Source.

    String forms:
      data/orders.parquet               a file (csv, parquet, json)
      duckdb:warehouse.db:gold.orders   a table in a DuckDB file
      duckdb:warehouse.db:SELECT ...    a query against a DuckDB file
    Mapping forms (config file):
      {path: ...} | {duckdb: ..., table|query: ...} | {url: ..., table|query: ...}
    """
    if hasattr(spec, "columns") and hasattr(spec, "dtypes"):
        return Source(kind="dataframe", frame=spec)
    if isinstance(spec, str):
        if spec.startswith("duckdb:"):
            parts = spec.split(":", 2)
            if len(parts) != 3:
                raise SourceError("DuckDB sources look like duckdb:<file.db>:<table or query>")
            _, path, target = parts
            path = _resolve(path, base_dir)
            if target.strip().lower().startswith(("select", "with")):
                return Source(kind="duckdb", path=path, query=target)
            return Source(kind="duckdb", path=path, table=target)
        return Source(kind="file", path=_resolve(spec, base_dir))
    if isinstance(spec, Mapping):
        if "path" in spec:
            return Source(kind="file", path=_resolve(str(spec["path"]), base_dir))
        if "duckdb" in spec:
            return Source(kind="duckdb", path=_resolve(str(spec["duckdb"]), base_dir),
                          table=spec.get("table"), query=spec.get("query"))
        if "url" in spec:
            url = os.path.expandvars(str(spec["url"]))
            return Source(kind="sql", url=url, table=spec.get("table"), query=spec.get("query"))
    raise SourceError(f"Unrecognized source: {spec!r}")


def _resolve(path: str, base_dir: str) -> str:
    path = os.path.expanduser(os.path.expandvars(path))
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(base_dir, path))


COMPRESSION_SUFFIXES = (".gz", ".zst")


def _split_extension(path: str) -> tuple[str, str]:
    """Return (file extension, compression suffix), e.g. 'orders.csv.gz' -> ('.csv', '.gz')."""
    base, ext = os.path.splitext(path.lower())
    if ext in COMPRESSION_SUFFIXES:
        return os.path.splitext(base)[1], ext
    return ext, ""


def load(con: duckdb.DuckDBPyConnection, source: Source, name: str) -> None:
    """Materialize a source as table `name` in the DuckDB connection."""
    if source.kind == "dataframe":
        con.register(f"{name}_df", source.frame)
        con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM "{name}_df"')
        con.unregister(f"{name}_df")
        return
    if source.kind == "file":
        path = source.path or ""
        if not os.path.exists(path):
            raise SourceError(f"File not found: {path}")
        ext, compression = _split_extension(path)
        reader = FILE_READERS.get(ext)
        if reader is None:
            raise SourceError(
                f"Unsupported file type '{ext}{compression}'. "
                "Use csv, parquet or json, optionally compressed with .gz or .zst.")
        if compression and reader == "read_parquet":
            raise SourceError(
                f"Compressed Parquet ('{ext}{compression}') is not supported. Parquet is already "
                f"compressed internally, so use the plain {ext} file.")
        if ext == ".xlsx":
            try:
                con.execute("INSTALL excel")
                con.execute("LOAD excel")
            except duckdb.Error as e:
                raise SourceError(
                    f"Excel extension could not be installed or loaded: {e} "
                ) from e
        con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM {reader}(?)', [path])
        return
    if source.kind == "duckdb":
        if not source.path or not os.path.exists(source.path):
            raise SourceError(f"DuckDB file not found: {source.path}")
        alias = f"src_{name}"
        lit = source.path.replace("'", "''")
        con.execute(f"ATTACH '{lit}' AS {alias} (READ_ONLY)")
        try:
            if source.query:
                con.execute(f"USE {alias}")
                try:
                    con.execute(f'CREATE OR REPLACE TABLE memory.main."{name}" AS {source.query}')
                finally:
                    con.execute("USE memory")
            elif source.table:
                con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM {alias}.{source.table}')
            else:
                raise SourceError("A DuckDB source needs a table or a query")
        finally:
            con.execute(f"DETACH {alias}")
        return
    if source.kind == "sql":
        try:
            import pandas as pd
            import sqlalchemy
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise SourceError("SQL sources need the sql extra: pip install 'pipeverify[sql]'") from exc
        engine = sqlalchemy.create_engine(source.url)
        query = source.query or f"SELECT * FROM {source.table}"
        with engine.connect() as conn:
            frame = pd.read_sql(sqlalchemy.text(query), conn)
        load(con, Source(kind="dataframe", frame=frame), name)
        return
    raise SourceError(f"Unknown source kind {source.kind}")
