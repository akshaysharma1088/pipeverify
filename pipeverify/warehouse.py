"""Native warehouse sources: Snowflake tables, and the tables behind dbt models.

Both are read-only. pipeverify runs one SELECT per snapshot and loads the result into its own
in-memory DuckDB, where the checks run. Nothing is written to the warehouse or the dbt project.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

_SIMPLE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_QUOTED = re.compile(r'"[^"]+"')
_ENV_VAR = re.compile(r"""\{\{\s*env_var\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]*)['"]\s*)?\)\s*(?:\|[^}]*)?\}\}""")

# Environment variable -> Snowflake connector argument. The same names DataRanger and the Snowflake CLI use.
SNOWFLAKE_ENV = {
    "SNOWFLAKE_ACCOUNT": "account",
    "SNOWFLAKE_USER": "user",
    "SNOWFLAKE_PASSWORD": "password",
    "SNOWFLAKE_AUTHENTICATOR": "authenticator",
    "SNOWFLAKE_PASSCODE": "passcode",  # the current code from an authenticator app, with username_password_mfa
    "SNOWFLAKE_TOKEN": "token",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "private_key_file",
    "SNOWFLAKE_PRIVATE_KEY_FILE": "private_key_file",
    "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE": "private_key_file_pwd",
    "SNOWFLAKE_ROLE": "role",
    "SNOWFLAKE_WAREHOUSE": "warehouse",
    "SNOWFLAKE_DATABASE": "database",
    "SNOWFLAKE_SCHEMA": "schema",
}
# dbt-snowflake profile key -> Snowflake connector argument
DBT_SNOWFLAKE_KEYS = {
    "account": "account", "user": "user", "password": "password", "role": "role", "warehouse": "warehouse",
    "database": "database", "schema": "schema", "authenticator": "authenticator", "token": "token",
    "private_key_path": "private_key_file", "private_key_passphrase": "private_key_file_pwd",
}


class WarehouseError(ValueError):
    """Raised when a warehouse source cannot be understood, reached or read."""


def is_query(text: str) -> bool:
    return text.strip().lower().startswith(("select", "with"))


def relation(name: str) -> str:
    """Check a table name (db.schema.table, parts may be "quoted") and return it unchanged.

    The name goes straight into SQL, so anything that is not a plain or quoted identifier is refused.
    """
    parts = re.findall(r'"[^"]*"|[^.]+', name or "")
    if not parts or ".".join(parts) != name or not all(_SIMPLE.fullmatch(p) or _QUOTED.fullmatch(p) for p in parts):
        raise WarehouseError(f"Table name {name!r} is not valid: use letters, digits and underscores, optionally "
                             'qualified as database.schema.table, with "quotes" around unusual parts')
    return name


def select(target: str, where: str | None = None) -> str:
    """The single read-only statement for a snapshot: a table (with an optional filter) or the user's own query."""
    if is_query(target):
        if where:
            raise WarehouseError("Use 'where' with a table name, or put the filter inside your query, not both")
        query = target.strip().rstrip(";")
    else:
        query = f"SELECT * FROM {relation(target)}" + (f" WHERE {where}" if where else "")
    if ";" in query:
        raise WarehouseError("Only a single SELECT statement is allowed")
    return query


# --------------------------------------------------------------------------- Snowflake

def snowflake_params(connection: str | None = None, env=None) -> dict:
    """Connector arguments for a named connection, or for the SNOWFLAKE_* environment variables."""
    if connection:
        return {"connection_name": connection}
    env = os.environ if env is None else env
    params = {arg: env[var] for var, arg in SNOWFLAKE_ENV.items() if env.get(var)}
    if params and "account" not in params:
        raise WarehouseError("SNOWFLAKE_ACCOUNT is not set. Set SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER plus a password, "
                             "key or authenticator, or name a connection from ~/.snowflake/connections.toml.")
    return params  # empty: the connector falls back to its default connection


def snowflake_connect(params: dict):
    """Open a Snowflake session tagged 'pipeverify', so its queries are easy to find in query history."""
    try:
        import snowflake.connector
    except ImportError:
        raise WarehouseError('Snowflake sources need the Snowflake connector: pip install "pipeverify[snowflake]"') from None
    import warnings

    params = dict(params)
    params.setdefault("session_parameters", {"QUERY_TAG": "pipeverify"})
    params.setdefault("application", "pipeverify")
    with warnings.catch_warnings():  # the connector's notices about its own future defaults are not for our users
        warnings.simplefilter("ignore", FutureWarning)
        return snowflake.connector.connect(**params)


def _first_line(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0]


def read_snowflake(query: str, params: dict, opener=None):
    """Run one SELECT on Snowflake and return a DataFrame with tidy column names and numbers."""
    try:
        import pandas as pd
    except ImportError:
        raise WarehouseError('Snowflake sources need pandas: pip install "pipeverify[snowflake]"') from None
    try:
        conn = (opener or snowflake_connect)(params)
    except WarehouseError:
        raise
    except Exception as e:
        raise WarehouseError(f"Could not connect to Snowflake: {_first_line(e)}") from None
    try:
        cur = conn.cursor()
        try:
            cur.execute(query)
            frame = None
            if hasattr(cur, "fetch_pandas_all"):
                try:
                    frame = cur.fetch_pandas_all()
                except Exception:  # connector installed without pyarrow: read the rows directly
                    cur.execute(query)
            if frame is None:
                frame = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
        finally:
            cur.close()
    except WarehouseError:
        raise
    except Exception as e:
        raise WarehouseError(f"Snowflake query failed: {_first_line(e)}") from None
    finally:
        conn.close()
    return tidy(frame)


def tidy(frame):
    """Snowflake returns ORDER_ID for order_id, and NUMBER columns can arrive as Decimal objects.

    Lower-case the all-capitals names so a Snowflake snapshot lines up with a file or DuckDB snapshot of
    the same table, and turn Decimal columns into plain numbers.
    """
    from decimal import Decimal

    frame = frame.rename(columns={c: c.lower() for c in frame.columns if isinstance(c, str) and c.isupper()})
    for col in frame.columns:
        if frame[col].dtype == object:
            sample = frame[col].dropna()
            if len(sample) and isinstance(sample.iloc[0], Decimal):
                whole = all(v == v.to_integral_value() for v in sample)
                frame[col] = frame[col].astype("Int64" if whole else float)
    return frame


# --------------------------------------------------------------------------- dbt

def _yaml(path: str) -> dict:
    import yaml

    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    def env(match):
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise WarehouseError(f"{path}: environment variable {name} is not set")

    return yaml.safe_load(_ENV_VAR.sub(env, text)) or {}


def dbt_node(manifest_path: str, model: str) -> dict:
    """A model, seed or snapshot by name, or a source written as source_name.table_name."""
    if not os.path.exists(manifest_path):
        raise WarehouseError(f"{manifest_path} not found. Run `dbt build` (or `dbt parse`) first, "
                             "or point 'manifest' at the manifest.json of the run you want to compare.")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    kinds = ("model", "seed", "snapshot")
    nodes = [n for n in manifest.get("nodes", {}).values() if n.get("name") == model and n.get("resource_type") in kinds]
    nodes += [s for s in manifest.get("sources", {}).values() if f"{s.get('source_name')}.{s.get('name')}" == model]
    if not nodes:
        known = sorted(n["name"] for n in manifest.get("nodes", {}).values() if n.get("resource_type") in kinds)
        raise WarehouseError(f"dbt model {model!r} is not in {manifest_path}. "
                             f"Models and seeds: {', '.join(known) or 'none'}")
    if len(nodes) > 1:
        raise WarehouseError(f"dbt model name {model!r} matches {len(nodes)} nodes in {manifest_path}")
    node = nodes[0]
    if node.get("config", {}).get("materialized") == "ephemeral":
        raise WarehouseError(f"dbt model {model!r} is ephemeral, so there is no table to compare")
    return node


def dbt_target(project_dir: str, target: str | None = None, profiles_dir: str | None = None) -> dict:
    """The connection details dbt would use: the project's profile and its chosen (or default) target."""
    project_file = os.path.join(project_dir, "dbt_project.yml")
    if not os.path.exists(project_file):
        raise WarehouseError(f"{project_file} not found: point 'project_dir' at the folder that holds dbt_project.yml")
    profile_name = _yaml(project_file).get("profile")
    candidates = [p for p in (profiles_dir, os.environ.get("DBT_PROFILES_DIR"), project_dir,
                              os.path.join(os.path.expanduser("~"), ".dbt")) if p]
    profiles_file = next((os.path.join(d, "profiles.yml") for d in candidates
                          if os.path.exists(os.path.join(d, "profiles.yml"))), None)
    if profiles_file is None:
        raise WarehouseError("profiles.yml not found in " + ", ".join(candidates))
    profile = _yaml(profiles_file).get(profile_name)
    if not profile:
        raise WarehouseError(f"profile {profile_name!r} (from dbt_project.yml) is not in {profiles_file}")
    target = target or profile.get("target")
    output = (profile.get("outputs") or {}).get(target)
    if not output:
        raise WarehouseError(f"dbt target {target!r} is not in profile {profile_name!r}; "
                             f"targets: {', '.join(profile.get('outputs') or {}) or 'none'}")
    return {**output, "_target": target}


def resolve_dbt(model: str, project_dir: str = ".", target: str | None = None, manifest: str | None = None,
                profiles_dir: str | None = None) -> dict[str, Any]:
    """Where a dbt model's table lives and how to reach it.

    `manifest` is the manifest.json of the run to compare. It defaults to the project's target/manifest.json.
    For the production side of a pull request check, point it at the manifest saved from the production run,
    because the same model lands in a different schema there.

    Returns {"kind": "duckdb", "path", "table"} or {"kind": "snowflake", "table", "params"}.
    """
    manifest_path = manifest or os.path.join(project_dir, "target", "manifest.json")
    node = dbt_node(manifest_path, model)
    out = dbt_target(project_dir, target, profiles_dir)
    kind = out.get("type")
    if kind == "duckdb":
        path = out.get("path") or ":memory:"
        if path == ":memory:" or str(path).startswith("md:"):
            raise WarehouseError("the dbt DuckDB target must be a database file for pipeverify to read it")
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(project_dir, path))
        # inside pipeverify the file is attached under another name, so leave the database part out
        table = ".".join('"' + str(p).replace('"', '""') + '"' for p in (node.get("schema"), node.get("alias") or node["name"]) if p)
        return {"kind": "duckdb", "path": path, "table": table, "target": out["_target"]}
    if kind == "snowflake":
        name = node.get("relation_name") or ".".join(
            p for p in (node.get("database"), node.get("schema"), node.get("alias") or node["name"]) if p)
        params = {arg: out[key] for key, arg in DBT_SNOWFLAKE_KEYS.items() if out.get(key)}
        return {"kind": "snowflake", "table": relation(name), "params": params, "target": out["_target"]}
    raise WarehouseError(f"dbt target type {kind!r} is not supported yet (DuckDB and Snowflake are). "
                         "Use a {url: ..., table: ...} source for other warehouses.")
