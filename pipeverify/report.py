"""Render results for a terminal, a pull request comment (Markdown), JSON or JUnit XML."""
from __future__ import annotations

import json
import os
import re
import sys
from xml.etree import ElementTree as ET

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


def _xml_text(value: str) -> str:
    # Replace code points prohibited by XML 1.0 before serialization.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "\ufffd", value)


def to_junit(result: RunResult) -> str:
    """Render each table as a JUnit suite, with review findings marked as skipped."""
    root = ET.Element("testsuites", name="pipeverify")
    count_paths = {
        "tests": "testcase",
        "failures": "testcase/failure",
        "skipped": "testcase/skipped",
        "errors": "testcase/error",
    }
    totals = dict.fromkeys(count_paths, 0)
    status_tags = {"block": "failure", "review": "skipped"}
    for table in result.tables:
        suite_name = _xml_text(table.name)
        suite = ET.SubElement(root, "testsuite", name=suite_name)
        if table.verdict == "pass":
            ET.SubElement(suite, "testcase", name="PASS", classname=suite_name)
        else:
            for index, finding in enumerate(table.findings, 1):
                name = f"{index}. {finding.check}"
                if finding.column is not None:
                    name += f" ({finding.column})"
                case = ET.SubElement(suite, "testcase", name=_xml_text(name), classname=suite_name)
                message = _xml_text(finding.message)
                status = status_tags.get(finding.severity)
                if status:
                    ET.SubElement(case, status, message=message).text = message
                else:
                    ET.SubElement(case, "system-out").text = message
        for key, path in count_paths.items():
            count = len(suite.findall(path))
            suite.set(key, str(count))
            totals[key] += count
    root.attrib.update({key: str(value) for key, value in totals.items()})
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
