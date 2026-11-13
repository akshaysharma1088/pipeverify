# Warehouse and SQL sources

pipeverify can read a table, or the result of a query, from any database SQLAlchemy supports.
Install the SQL extra plus the driver for your database:

```bash
pip install "pipeverify[sql]"
```

A SQL source is a mapping with a `url` and either a `table` or a `query`:

```yaml
before: {url: "<sqlalchemy url>", table: schema.table}
after:  {url: "<sqlalchemy url>", query: "SELECT ... FROM ..."}
```

## Tables and queries

`table` is inserted into `SELECT * FROM <table>` exactly as written, so use whatever name your
database expects and quote it yourself if needed:

```yaml
before: {url: "${WAREHOUSE_URL}", table: '"MixedCase"."Orders"'}   # Postgres: quote mixed-case names
```

The whole result is loaded into memory. For large tables, use `query` to compare only what you
need, for example a date range:

```yaml
after: {url: "${WAREHOUSE_URL}", query: "SELECT * FROM public.orders WHERE order_date >= '2026-01-01'"}
```

## Credentials

Keep passwords out of `pipeverify.yml`. Any `${VAR}` in a `url` is replaced with the value of
that environment variable when the config is loaded.

```bash
export PGUSER=analytics
export PGPASSWORD='...'
```

- **Percent-encode special characters.** The value is inserted into the URL as is, so a password
  containing `@`, `/`, `:` or `#` must be encoded first:

```bash
  export PGPASSWORD="$(python -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=''))" 'p@ss/w:rd')"
```

- **Or keep the whole URL in one variable**, as in the README: `url: "${WAREHOUSE_URL}"`.
- Do not commit real credentials. In CI, use your platform's secrets.

## Postgres

```bash
pip install "pipeverify[sql]" psycopg2-binary
```

URL format: `postgresql+psycopg2://USER:PASSWORD@HOST:5432/DATABASE`

```yaml
tables:
  - name: public.orders
    before: {url: "postgresql+psycopg2://${PGUSER}:${PGPASSWORD}@db.example.com:5432/analytics", table: public.orders_before}
    after:  {url: "postgresql+psycopg2://${PGUSER}:${PGPASSWORD}@db.example.com:5432/analytics", table: public.orders_after}
    key: [order_id]
```

## Snowflake

The native Snowflake source is the simplest route (0.3.0 and later). It needs no URL and supports
key-pair, token and SSO logins:

```bash
pip install "pipeverify[snowflake]"
export SNOWFLAKE_ACCOUNT=myorg-myaccount SNOWFLAKE_USER=ci_user SNOWFLAKE_PRIVATE_KEY_PATH=~/.ssh/snowflake.p8
export SNOWFLAKE_ROLE=REPORTER SNOWFLAKE_WAREHOUSE=COMPUTE_WH
```

```yaml
tables:
  - name: gold.orders
    before: {snowflake: ANALYTICS.GOLD.ORDERS, where: "order_date >= '2026-10-01'"}
    after:  {snowflake: ANALYTICS_DEV.GOLD.ORDERS, where: "order_date >= '2026-10-01'"}
    key: [order_id]
```

| Setting | Meaning |
|---|---|
| `snowflake` | A table (`DB.SCHEMA.TABLE`) or a `SELECT` |
| `where` | Filter for a table, so only the slice you changed is loaded |
| `connection` | A named connection from `~/.snowflake/connections.toml`, instead of the environment variables |

Variables read: `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_PRIVATE_KEY_PATH`,
`SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`, `SNOWFLAKE_TOKEN`, `SNOWFLAKE_AUTHENTICATOR`, `SNOWFLAKE_PASSCODE`,
`SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`. For a login with an
authenticator app, set `SNOWFLAKE_AUTHENTICATOR=username_password_mfa` and `SNOWFLAKE_PASSCODE` to the
current code. `externalbrowser` works only when your account uses single sign-on.

Column names that Snowflake returns in capitals are lower-cased, so a Snowflake snapshot can be
compared with a file or DuckDB snapshot of the same table.

The SQLAlchemy route below also still works:

```bash
pip install "pipeverify[sql]" snowflake-sqlalchemy
```

URL format: `snowflake://USER:PASSWORD@ACCOUNT/DATABASE/SCHEMA?warehouse=WAREHOUSE&role=ROLE`

`ACCOUNT` is the account identifier (for example `myorg-myaccount`), not the full `snowflakecomputing.com` URL.

```yaml
tables:
  - name: gold.orders
    before: {url: "snowflake://${SF_USER}:${SF_PASSWORD}@${SF_ACCOUNT}/ANALYTICS/GOLD?warehouse=COMPUTE_WH&role=ANALYST", table: orders_before}
    after:  {url: "snowflake://${SF_USER}:${SF_PASSWORD}@${SF_ACCOUNT}/ANALYTICS/GOLD?warehouse=COMPUTE_WH&role=ANALYST", table: orders_after}
    key: [order_id]
```

## BigQuery

```bash
pip install "pipeverify[sql]" sqlalchemy-bigquery
```

URL format: `bigquery://PROJECT` (optionally `bigquery://PROJECT/DATASET`)

Authentication uses Google application credentials. Point `GOOGLE_APPLICATION_CREDENTIALS` at a
service-account key file, or use `gcloud auth application-default login` locally. Never put the
key contents in the config.

```yaml
tables:
  - name: analytics.orders
    before: {url: "bigquery://${BQ_PROJECT}", table: analytics.orders_before}
    after:  {url: "bigquery://${BQ_PROJECT}", table: analytics.orders_after}
    key: [order_id]
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Error mentions a literal `${PGUSER}` | The variable is not exported in the shell or CI job running pipeverify. |
| `could not translate host name "..."` with a fragment of your password | The password has `@`, `/` or `:` in it. Percent-encode it (see Credentials). |
| `No module named ...` or `Can't load plugin: sqlalchemy.dialects:...` | The database driver is not installed. Install the package listed for your database above. |
| `DefaultCredentialsError: Your default credentials were not found` (BigQuery) | Google credentials are not configured. Set `GOOGLE_APPLICATION_CREDENTIALS` to a service-account key file, or run `gcloud auth application-default login`. |

Errors can include parts of the connection URL. Check them before pasting into a public issue.
## dbt

```yaml
tables:
  - name: fct_orders
    before: {dbt: fct_orders, target: prod, manifest: prod-state/manifest.json}
    after:  {dbt: fct_orders, target: ci}
    key: [order_id]
```

| Setting | Meaning | Default |
|---|---|---|
| `dbt` | Model, seed or snapshot name, or a source as `source_name.table_name` | |
| `target` | Target in `profiles.yml` to connect with | the profile's default |
| `manifest` | `manifest.json` of the run to compare | `<project_dir>/target/manifest.json` |
| `project_dir` | Folder that holds `dbt_project.yml` | the config file's folder |
| `profiles_dir` | Folder that holds `profiles.yml` | `DBT_PROFILES_DIR`, the project folder, then `~/.dbt` |
| `where` | Filter, so only part of the model is loaded | |

pipeverify reads the manifest and the profile and runs one `SELECT` per side. It does not run dbt.
`{{ env_var('NAME') }}` in `profiles.yml` is filled in from the environment. DuckDB targets need
nothing extra; Snowflake targets need `pip install "pipeverify[snowflake]"`. For other warehouses, use
a `url` source for now. A full example is in [examples/dbt_pr](../examples/dbt_pr).
