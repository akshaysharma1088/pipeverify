"""Snowflake and dbt sources, tested without a Snowflake account or a dbt install."""
import json
import os

import duckdb
import pandas as pd
import pytest

import pipeverify
from pipeverify import warehouse as W
from pipeverify.sources import SourceError, parse_source


class FakeCursor:
    def __init__(self, con, log):
        self.con, self.log, self.res = con, log, None

    def execute(self, sql):
        self.log.append(sql)
        self.res = self.con.execute(sql)

    @property
    def description(self):
        return [(d[0].upper(),) for d in self.res.description]  # Snowflake reports unquoted names in capitals

    def fetchall(self):
        return self.res.fetchall()

    def close(self):
        pass


class FakeSnowflake:
    """Stands in for a Snowflake connection, backed by an in-memory DuckDB."""

    def __init__(self):
        self.con, self.log, self.closed = duckdb.connect(), [], False

    def cursor(self):
        return FakeCursor(self.con, self.log)

    def close(self):
        self.closed = True


@pytest.fixture
def snow(monkeypatch):
    fake = FakeSnowflake()
    fake.con.execute("create schema gold")
    fake.con.execute("create table gold.orders as select i as order_id, (i * 1.5)::decimal(10,2) as amount, "
                     "'x' as status from range(1, 101) t(i)")
    fake.con.execute("create table gold.orders_new as select order_id, amount, status from gold.orders where order_id <= 80")
    seen = []
    monkeypatch.setattr(W, "snowflake_connect", lambda params: (seen.append(params), fake)[1])
    fake.params = seen
    return fake


def test_relation_refuses_anything_but_identifiers():
    assert W.relation('DB.SCHEMA."My Table"') == 'DB.SCHEMA."My Table"'
    for bad in ("orders; drop table x", "a b", "", "x.", "t where 1=1"):
        with pytest.raises(W.WarehouseError):
            W.relation(bad)


def test_select_is_one_statement():
    assert W.select("GOLD.ORDERS", "d >= '2026-01-01'") == "SELECT * FROM GOLD.ORDERS WHERE d >= '2026-01-01'"
    assert W.select("select 1;") == "select 1"
    with pytest.raises(W.WarehouseError, match="single SELECT"):
        W.select("select 1; delete from t")
    with pytest.raises(W.WarehouseError, match="not both"):
        W.select("select 1", "x = 1")


def test_snowflake_params_from_environment():
    env = {"SNOWFLAKE_ACCOUNT": "acme", "SNOWFLAKE_USER": "ci", "SNOWFLAKE_PRIVATE_KEY_PATH": "/k.p8", "SNOWFLAKE_ROLE": "R"}
    assert W.snowflake_params(env=env) == {"account": "acme", "user": "ci", "private_key_file": "/k.p8", "role": "R"}
    assert W.snowflake_params("prod", env=env) == {"connection_name": "prod"}
    assert W.snowflake_params(env={}) == {}
    with pytest.raises(W.WarehouseError, match="SNOWFLAKE_ACCOUNT"):
        W.snowflake_params(env={"SNOWFLAKE_USER": "ci"})


def test_compare_two_snowflake_tables(snow):
    res = pipeverify.verify("snowflake:gold.orders", {"snowflake": "gold.orders_new"}, key="order_id")
    assert res.verdict == "block"                      # 20 of 100 rows are gone
    assert res.stats["rows_before"] == 100 and res.stats["rows_after"] == 80
    assert res.before == "snowflake:gold.orders"
    assert snow.log == ["SELECT * FROM gold.orders_new", "SELECT * FROM gold.orders"] and snow.closed
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in snow.log)


def test_snowflake_matches_a_file_snapshot(snow, tmp_path):
    """Capital column names and Decimal amounts from Snowflake line up with a plain CSV of the same table."""
    path = tmp_path / "orders.csv"
    pd.DataFrame({"order_id": range(1, 101), "amount": [i * 1.5 for i in range(1, 101)], "status": "x"}).to_csv(path, index=False)
    res = pipeverify.verify(str(path), "snowflake:gold.orders", key="order_id")
    assert res.verdict == "pass", [f.message for f in res.findings]


def test_snowflake_where_and_query_and_connection(snow):
    res = pipeverify.verify({"snowflake": "gold.orders", "where": "order_id <= 80", "connection": "prod"},
                            {"snowflake": "select * from gold.orders_new"}, key="order_id")
    assert res.verdict == "pass"
    assert {"connection_name": "prod"} in snow.params
    assert "SELECT * FROM gold.orders WHERE order_id <= 80" in snow.log


def test_snowflake_errors_are_readable(snow, monkeypatch):
    with pytest.raises(SourceError, match="not valid"):
        parse_source("snowflake:orders; drop table x")
    with pytest.raises(Exception, match="Snowflake query failed"):
        pipeverify.verify("snowflake:gold.nope", "snowflake:gold.orders", key="order_id")

    def refuse(params):
        raise RuntimeError("250001: Incorrect username or password\nmore detail")
    monkeypatch.setattr(W, "snowflake_connect", refuse)
    with pytest.raises(Exception, match="Could not connect to Snowflake: 250001: Incorrect username or password$"):
        pipeverify.verify("snowflake:gold.orders", "snowflake:gold.orders", key="order_id")


def test_missing_connector_says_what_to_install(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("snowflake"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(W.WarehouseError, match=r'pip install "pipeverify\[snowflake\]"'):
        W.snowflake_connect({})


# --------------------------------------------------------------------------- dbt

def _dbt_project(tmp_path, fanout=True):
    """A built dbt project on DuckDB, written by hand: prod and ci databases plus one manifest per run."""
    proj = tmp_path / "analytics"
    (proj / "target").mkdir(parents=True)
    (proj / "target-prod").mkdir()
    (proj / "dbt_project.yml").write_text("name: demo\nprofile: demo\n")
    (proj / "profiles.yml").write_text(
        "demo:\n  target: ci\n  outputs:\n"
        "    prod: {type: duckdb, path: prod.duckdb}\n"
        "    ci: {type: duckdb, path: \"{{ env_var('PV_CI_DB', 'ci.duckdb') }}\"}\n")
    for db, schema, factor in (("prod.duckdb", "analytics", 1), ("ci.duckdb", "pr_42", 2 if fanout else 1)):
        con = duckdb.connect(str(proj / db))
        con.execute(f"create schema {schema}")
        con.execute(f"create table {schema}.fct_sales as select i as store_id, "
                    f"case when i = 3 then 100.0 * {factor} else 100.0 end as revenue from range(1, 11) t(i)")
        con.close()

    def manifest(schema):
        return {"nodes": {
            "model.demo.fct_sales": {"name": "fct_sales", "resource_type": "model", "database": "x", "schema": schema,
                                     "alias": "fct_sales", "config": {"materialized": "table"},
                                     "relation_name": f'"x"."{schema}"."fct_sales"'},
            "model.demo.tmp": {"name": "tmp", "resource_type": "model", "schema": schema, "config": {"materialized": "ephemeral"}},
            "test.demo.t": {"name": "fct_sales", "resource_type": "test"}}, "sources": {}}
    (proj / "target" / "manifest.json").write_text(json.dumps(manifest("pr_42")))
    (proj / "target-prod" / "manifest.json").write_text(json.dumps(manifest("analytics")))
    return proj


def test_dbt_pr_against_production(tmp_path):
    proj = _dbt_project(tmp_path)
    cfg = proj / "pipeverify.yml"
    cfg.write_text("tables:\n  - name: fct_sales\n"
                   "    before: {dbt: fct_sales, target: prod, manifest: target-prod/manifest.json}\n"
                   "    after: {dbt: fct_sales}\n    key: store_id\n")
    result = pipeverify.run_config(str(cfg))
    t = result.tables[0]
    assert t.error is None and t.verdict == "block"
    assert t.before == "dbt:fct_sales (prod)" and t.after == "dbt:fct_sales (ci)"
    assert any(f.column == "revenue" for f in t.findings)


def test_dbt_clean_pr_passes_and_where_filters(tmp_path):
    proj = _dbt_project(tmp_path, fanout=False)
    before = {"dbt": "fct_sales", "project_dir": str(proj), "target": "prod", "manifest": str(proj / "target-prod" / "manifest.json")}
    after = {"dbt": "fct_sales", "project_dir": str(proj), "where": "store_id <= 5"}
    assert pipeverify.verify(before, {**after, "where": None}, key="store_id").verdict == "pass"
    res = pipeverify.verify({**before, "where": "store_id <= 5"}, after, key="store_id")
    assert res.verdict == "pass" and res.stats["rows_after"] == 5


def test_dbt_string_form_uses_the_current_project(tmp_path, monkeypatch):
    proj = _dbt_project(tmp_path, fanout=False)
    monkeypatch.chdir(proj)
    assert pipeverify.verify("dbt:fct_sales", "dbt:fct_sales", key="store_id").verdict == "pass"


def test_dbt_errors_are_readable(tmp_path):
    proj = _dbt_project(tmp_path)
    base = {"project_dir": str(proj)}
    with pytest.raises(SourceError, match="Models and seeds: fct_sales, tmp"):
        parse_source({"dbt": "nope", **base})
    with pytest.raises(SourceError, match="ephemeral"):
        parse_source({"dbt": "tmp", **base})
    with pytest.raises(SourceError, match="targets: prod, ci"):
        parse_source({"dbt": "fct_sales", "target": "staging", **base})
    with pytest.raises(SourceError, match="Run `dbt build`"):
        parse_source({"dbt": "fct_sales", "manifest": "missing.json", **base})
    with pytest.raises(SourceError, match="dbt_project.yml"):
        parse_source({"dbt": "fct_sales", "project_dir": str(tmp_path / "nowhere"), "manifest": str(proj / "target" / "manifest.json")})
    (proj / "profiles.yml").write_text("demo:\n  target: ci\n  outputs:\n    ci: {type: bigquery}\n")
    with pytest.raises(SourceError, match="'bigquery' is not supported yet"):
        parse_source({"dbt": "fct_sales", **base})


def test_dbt_snowflake_target_reads_through_the_profile(tmp_path, snow, monkeypatch):
    proj = tmp_path / "sf"
    (proj / "target").mkdir(parents=True)
    (proj / "dbt_project.yml").write_text("name: demo\nprofile: demo\n")
    monkeypatch.setenv("SF_PASSWORD", "secret")
    (proj / "profiles.yml").write_text(
        "demo:\n  target: prod\n  outputs:\n    prod:\n      type: snowflake\n      account: acme\n      user: ci\n"
        "      password: \"{{ env_var('SF_PASSWORD') }}\"\n      role: R\n      warehouse: WH\n      database: ANALYTICS\n"
        "      schema: gold\n      private_key_path: /k.p8\n      threads: 4\n")
    (proj / "target" / "manifest.json").write_text(json.dumps({"nodes": {"model.demo.orders": {
        "name": "orders", "resource_type": "model", "database": "ANALYTICS", "schema": "gold", "alias": "orders",
        "config": {"materialized": "table"}, "relation_name": "gold.orders"}}, "sources": {}}))
    src = parse_source({"dbt": "orders", "project_dir": str(proj)})
    assert src.kind == "snowflake" and src.query == "SELECT * FROM gold.orders"
    assert src.params == {"account": "acme", "user": "ci", "password": "secret", "role": "R", "warehouse": "WH",
                          "database": "ANALYTICS", "schema": "gold", "private_key_file": "/k.p8"}
    res = pipeverify.verify(src_spec := {"dbt": "orders", "project_dir": str(proj)}, src_spec, key="order_id")
    assert res.verdict == "pass" and res.after == "dbt:orders (prod)"


def test_dbt_profile_env_var_missing(tmp_path, monkeypatch):
    proj = _dbt_project(tmp_path)
    (proj / "profiles.yml").write_text("demo:\n  target: ci\n  outputs:\n    ci: {type: duckdb, path: \"{{ env_var('PV_NOT_SET') }}\"}\n")
    monkeypatch.delenv("PV_NOT_SET", raising=False)
    with pytest.raises(SourceError, match="PV_NOT_SET is not set"):
        parse_source({"dbt": "fct_sales", "project_dir": str(proj)})
