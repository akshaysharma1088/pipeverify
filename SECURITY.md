# Security policy

## What pipeverify touches

pipeverify reads the data you point it at and runs locally. Snowflake and dbt sources run one
read-only `SELECT` per snapshot, tagged `pipeverify` in query history, and the MCP server only
exposes read-only tools. Nothing is sent over the network unless you pass `--explain`, and then only
the findings (check names, counts, percentages and column names), with example values removed.

## Supported versions

Security fixes go into the latest release on PyPI. Please upgrade before reporting.

## Reporting a vulnerability

Please email akshay.sharma1088@gmail.com rather than opening a public issue. Include what you ran,
what happened, and a small example that reproduces it if you can.

You will get a first reply within 7 days. Once a fix is ready it is released on PyPI, noted in
`CHANGELOG.md`, and credited to you unless you prefer otherwise.
