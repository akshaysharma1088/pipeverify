# Contributing

Thanks for helping. pipeverify aims to stay small, dependable and easy to run in CI.

## Set up

```bash
git clone https://github.com/akshaysharma1088/pipeverify && cd pipeverify
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" ruff
ruff check .
python examples/retail/make_demo.py
pytest -q
```

## Good first contributions

Issues labelled [good first issue](https://github.com/akshaysharma1088/pipeverify/labels/good%20first%20issue)
are small and well described. Comment on one to claim it. For questions or ideas, use
[Discussions](https://github.com/akshaysharma1088/pipeverify/discussions).

Other welcome contributions:

- A new check for a kind of silent data harm you have seen in production. Add it to
  `pipeverify/checks.py`, add a test, and add a demo scenario if it helps explain it.
- A source adapter (for example a native Snowflake or BigQuery reader that pushes work down).
- Integrations: a dbt or Airflow helper that builds the before/after snapshots.

## Guidelines

- Every check needs a test with a small DataFrame that shows the problem.
- Code must pass `ruff check .` (the rules are in `pyproject.toml`); CI fails on new warnings.
- Verdicts must not depend on an LLM. AI features stay optional and off by default.
- Never send raw data to an external service without an explicit flag.
- Keep messages plain: say what changed and by how much.

## Reporting a problem

Open an issue with the command you ran, the output, and if possible two tiny CSV files that
reproduce it.

## Security and conduct

Security problems go through the private process in [SECURITY.md](SECURITY.md), not a public issue.
By taking part you agree to the [code of conduct](CODE_OF_CONDUCT.md).

## Using pipeverify?

Add your team to [ADOPTERS.md](ADOPTERS.md) or
[share how you use it](https://github.com/akshaysharma1088/pipeverify/issues/new?template=share_usage.yml).
