# Check a dbt pull request against production

A small dbt project (DuckDB, so it runs anywhere) with a model, `fct_daily_sales`, and a change that
breaks it without failing: joining to a history table that has two rows for one store. dbt builds the
model, the `not_null` tests pass, and store 3's revenue doubles.

```bash
pip install pipeverify dbt-duckdb
cd examples/dbt_pr

# "production": the model as it is on main
dbt build --target prod --target-path target-prod

# "the pull request": the same model with the change
dbt build --target ci --vars '{bug: true}'

pipeverify run
```

pipeverify reports a BLOCK: orders and revenue changed on 14 rows, all for store 3, and both totals
moved by about 13%. Build the pull request side without `--vars '{bug: true}'` and it reports PASS.

## How the config works

```yaml
tables:
  - name: fct_daily_sales
    before: {dbt: fct_daily_sales, target: prod, manifest: target-prod/manifest.json}
    after: {dbt: fct_daily_sales, target: ci}
    key: [store_id, sale_date]
```

`dbt: <model>` looks the model up in dbt's `manifest.json` to find its table, and connects the way the
project's `profiles.yml` says for that target. DuckDB and Snowflake targets are supported.

- `target` picks the profile target. Omit it for the profile's default.
- `manifest` picks which run's manifest to use. Omit it for `target/manifest.json`. For the production
  side, point it at the manifest saved from the production run (the same file dbt's `--state` and
  `--defer` use), because the model lands in a different schema there.
- `project_dir` is the folder with `dbt_project.yml`. Omit it when the config sits in the project folder.
- `where` filters the rows, for example `where: "sale_date >= '2026-10-01'"`, so a large model is
  compared on the slice that matters.

pipeverify reads `manifest.json`, `dbt_project.yml` and `profiles.yml` and runs one `SELECT` per side.
It does not run dbt and does not write to the warehouse.

## In a pull request

```yaml
- run: dbt build --target ci
- uses: akshaysharma1088/pipeverify@v0.3.0
  with:
    config: pipeverify.yml
    extras: snowflake        # the drivers your dbt target needs; leave out for DuckDB
  env:
    SNOWFLAKE_ACCOUNT: ${{ secrets.SNOWFLAKE_ACCOUNT }}
```
