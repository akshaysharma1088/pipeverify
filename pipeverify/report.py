"""Render results for a terminal, a pull request comment (Markdown) or machines (JSON)."""
from __future__ import annotations

import json
import os
import sys

from .core import RunResult

ICON = {"pass": "✅", "review": "🟡", "block": "⛔", "info": "ℹ️"}
LABEL = {"pass": "PASS", "review": "REVIEW", "block": "BLOCK", "info": "INFO"}
HEADLINE = {
    "pass": "Data looks the same where it should. Safe to merge.",
    "review": "Something changed that tests would not catch. A person should look before merging.",
    "block": "This change damages the data. Do not merge as is.",
}


def _headline(result: RunResult) -> str:
    if any(t.error for t in result.tables):
        return "Some tables could not be checked. Fix the errors below and run again."
    return HEADLINE[result.verdict]


def _color(text: str, sev: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    code = {"pass": "32", "review": "33", "block": "31", "info": "36"}.get(sev, "0")
    return f"\033[{code}m{text}\033[0m"


def to_text(result: RunResult, show_info: bool = False, max_examples: int = 1) -> str:
    lines = []
    for t in result.tables:
        lines.append(_color(f"{LABEL[t.verdict]:<7}", t.verdict) + f" {t.name}")
        s = t.stats
        if s:
            extra = f", matched {s['matched_rows']:,}" if "matched_rows" in s else ""
            if "rows_before" in s:
                lines.append(f"        rows {s['rows_before']:,} -> {s.get('rows_after', 0):,}{extra}")
            else:
                lines.append(f"        rows {s.get('rows_after', 0):,} (checked against source)")
        for f in t.findings:
            if f.severity == "info" and not show_info:
                continue
            lines.append("        " + _color(f"{LABEL[f.severity]:<6}", f.severity) + f" {f.message}")
            ex = f.metrics.get("examples") if f.metrics else None
            if ex:
                for row in ex[:max(0, max_examples)]:
                    lines.append(f"               e.g. {row}")
    lines.append("")
    lines.append(_color(f"Verdict: {LABEL[result.verdict]}", result.verdict) + f"  {_headline(result)}")
    if result.explanation:
        lines += ["", "Explanation:", result.explanation]
    return "\n".join(lines)


def to_markdown(result: RunResult) -> str:
    out = [f"## {ICON[result.verdict]} pipeverify: {LABEL[result.verdict]}", "", _headline(result), ""]
    out += ["| Table | Verdict | Rows before → after | Issues |", "|---|---|---|---|"]
    for t in result.tables:
        issues = sum(1 for f in t.findings if f.severity in ("review", "block"))
        s = t.stats or {}
        if "rows_before" in s:
            rows = f"{s['rows_before']:,} → {s.get('rows_after', 0):,}"
        elif "rows_after" in s:
            rows = f"{s['rows_after']:,} (source check)"
        else:
            rows = "n/a"
        out.append(f"| `{t.name}` | {ICON[t.verdict]} {LABEL[t.verdict]} | {rows} | {issues} |")
    for t in result.tables:
        notable = [f for f in t.findings if f.severity in ("review", "block")]
        info = [f for f in t.findings if f.severity == "info"]
        if not notable and not info:
            continue
        out += ["", f"<details{' open' if notable else ''}><summary><b>{t.name}</b></summary>", ""]
        for f in notable + info:
            out.append(f"- {ICON[f.severity]} **{LABEL[f.severity]}** {f.message}")
            ex = f.metrics.get("examples") if f.metrics else None
            if ex:
                out.append(f"  - example (key…, before, after): `{ex[0]}`")
        out += ["", "</details>"]
    if result.explanation:
        out += ["", "### Explanation", "", result.explanation]
    out += ["", f"<sub>Checked in {result.seconds:.2f}s by pipeverify. Verdicts compare data, not job status.</sub>"]
    return "\n".join(out) + "\n"


def to_json(result: RunResult) -> str:
    return json.dumps(result.to_dict(), indent=2, default=str)
