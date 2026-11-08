"""Checks that compare a table before and after a change.

Each check looks for a kind of silent harm: the job succeeds and tests stay green,
but the data is no longer right. Checks return Findings; the policy turns them
into a verdict.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import duckdb

PASS, INFO, REVIEW, BLOCK = "pass", "info", "review", "block"
RANK = {PASS: 0, INFO: 0, REVIEW: 1, BLOCK: 2}

NUMERIC_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
                 "UINTEGER", "UBIGINT", "FLOAT", "REAL", "DOUBLE", "DECIMAL")
TEXT_TYPES = ("VARCHAR", "TEXT", "STRING", "CHAR", "ENUM")


@dataclass
class Thresholds:
    """When a difference becomes worth a human look (review) or should stop the change (block).

    Percentages are shares of rows, from 0 to 100.
    """
    skip_id_like_totals: bool=True

    row_change_review_pct: float = 1.0
    row_change_block_pct: float = 10.0
    missing_keys_review_pct: float = 0.0     # any lost key gets a review
    missing_keys_block_pct: float = 5.0
    null_rate_review_pts: float = 1.0
    null_rate_block_pts: float = 10.0
    changed_values_review_pct: float = 0.0   # any unexpected changed value gets a review
    changed_values_block_pct: float = 10.0
    sum_shift_review_pct: float = 0.5
    sum_shift_block_pct: float = 5.0
    category_max_distinct: int = 50          # treat text columns with few values as categories
    float_tolerance: float = 1e-9
    source_mismatch_review_pct: float = 0.0
    source_mismatch_block_pct: float = 5.0
    source_missing_review_pct: float = 0.0
    source_missing_block_pct: float = 5.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Thresholds":
        if not data:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown threshold(s): {', '.join(sorted(unknown))}")
        return cls(**data)


@dataclass
class Finding:
    check: str
    severity: str
    message: str
    column: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _grade(value: float, review: float, block: float) -> str:
    if value >= block and value > 0:
        return BLOCK
    if value > review or (review == 0 and value > 0):
        return REVIEW
    return PASS


def _schema(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    rows = con.execute(f"DESCRIBE {q(table)}").fetchall()
    return {r[0]: str(r[1]).upper() for r in rows}

def _is_numeric(dtype: str) -> bool:
    return dtype.startswith(NUMERIC_TYPES)


def _is_text(dtype: str) -> bool:
    return dtype.startswith(TEXT_TYPES)

def _looks_like_id(name: str,dtype: str) -> bool:
    name_lower=str(name).lower()
    is_int="int" in str(dtype).lower()
    return is_int and (name_lower=="id" or name_lower.endswith("_id"))

def run_checks(
    con: duckdb.DuckDBPyConnection,
    before: str,
    after: str,
    key: list[str] | None = None,
    expect_change: list[str] | None = None,
    ignore: list[str] | None = None,
    expect_row_change: bool = False,
    thresholds: Thresholds | None = None,
) -> tuple[list[Finding], dict[str, Any]]:
    """Run every check on two tables already loaded in `con`."""
    t = thresholds or Thresholds()
    key = list(key or [])
    expect = set(expect_change or [])
    ignore = set(ignore or [])
    findings: list[Finding] = []

    sb, sa = _schema(con, before), _schema(con, after)
    for k in key:
        if k not in sb or k not in sa:
            raise ValueError(f"Key column '{k}' is missing from the before or after table")

    n_before = con.execute(f"SELECT count(*) FROM {q(before)}").fetchall()[0][0]
    n_after = con.execute(f"SELECT count(*) FROM {q(after)}").fetchall()[0][0]
    stats: dict[str, Any] = {"rows_before": n_before, "rows_after": n_after,
                             "columns_before": len(sb), "columns_after": len(sa)}

    # 1. schema
    removed = [c for c in sb if c not in sa and c not in ignore]
    added = [c for c in sa if c not in sb and c not in ignore]
    retyped = [c for c in sb if c in sa and c not in ignore and sb[c] != sa[c]]
    for c in removed:
        findings.append(Finding("schema", BLOCK, f"Column '{c}' was removed", c, {"type": sb[c]}))
    for c in added:
        findings.append(Finding("schema", INFO, f"Column '{c}' was added", c, {"type": sa[c]}))
    for c in retyped:
        findings.append(Finding("schema", REVIEW, f"Column '{c}' changed type {sb[c]} -> {sa[c]}",
                                c, {"before": sb[c], "after": sa[c]}))

    # 2. row count
    if n_before == 0 and n_after == 0:
        findings.append(Finding("row_count", INFO, "Both tables are empty"))
    else:
        base = max(n_before, 1)
        pct = abs(n_after - n_before) / base * 100
        sev = INFO if expect_row_change else _grade(pct, t.row_change_review_pct, t.row_change_block_pct)
        if n_before > 0 and n_after == 0:
            sev = BLOCK
        if n_after != n_before:
            direction = "lost" if n_after < n_before else "gained"
            findings.append(Finding("row_count", sev,
                                    f"Table {direction} {abs(n_after - n_before):,} rows ({pct:.2f}%)",
                                    None, {"before": n_before, "after": n_after, "pct": round(pct, 4)}))

    common = [c for c in sb if c in sa and c not in ignore]

    # 3. keys
    unique_keys_join = None
    if key:
        kcols = ", ".join(q(k) for k in key)
        dup_before = con.execute(
            f"SELECT count(*) FROM (SELECT {kcols} FROM {q(before)} GROUP BY ALL HAVING count(*) > 1)").fetchall()[0][0]
        dup_after = con.execute(
            f"SELECT count(*) FROM (SELECT {kcols} FROM {q(after)} GROUP BY ALL HAVING count(*) > 1)").fetchall()[0][0]
        stats.update(duplicate_keys_before=dup_before, duplicate_keys_after=dup_after)
        if dup_after > dup_before:
            findings.append(Finding("duplicate_keys", BLOCK,
                                    f"{dup_after - dup_before:,} key value(s) now appear more than once",
                                    None, {"before": dup_before, "after": dup_after}))
        elif dup_after < dup_before:
            findings.append(Finding("duplicate_keys", INFO,
                                    f"Duplicate keys went down from {dup_before:,} to {dup_after:,}",
                                    None, {"before": dup_before, "after": dup_after}))
        on = " AND ".join(f"b.{q(k)} IS NOT DISTINCT FROM a.{q(k)}" for k in key)
        missing = con.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT {kcols} FROM {q(before)}) b "
            f"WHERE NOT EXISTS (SELECT 1 FROM {q(after)} a WHERE {on})").fetchall()[0][0]
        new = con.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT {kcols} FROM {q(after)}) a "
            f"WHERE NOT EXISTS (SELECT 1 FROM {q(before)} b WHERE {on})").fetchall()[0][0]
        distinct_before = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {kcols} FROM {q(before)})").fetchall()[0][0]
        stats.update(missing_keys=missing, new_keys=new)
        if missing:
            pct = missing / max(distinct_before, 1) * 100
            sev = INFO if expect_row_change else _grade(pct, t.missing_keys_review_pct, t.missing_keys_block_pct)
            findings.append(Finding("missing_keys", sev,
                                    f"{missing:,} key(s) present before are gone after ({pct:.2f}%)",
                                    None, {"count": missing, "pct": round(pct, 4)}))
        if new:
            findings.append(Finding("new_keys", INFO if expect_row_change else REVIEW,
                                    f"{new:,} new key(s) appeared", None, {"count": new}))
        # rows whose key is unique on both sides, used for value comparison
        con.execute(f"CREATE OR REPLACE TEMP VIEW _pv_ub AS SELECT * FROM {q(before)} "
                    f"QUALIFY count(*) OVER (PARTITION BY {kcols}) = 1")
        con.execute(f"CREATE OR REPLACE TEMP VIEW _pv_ua AS SELECT * FROM {q(after)} "
                    f"QUALIFY count(*) OVER (PARTITION BY {kcols}) = 1")
        unique_keys_join = " AND ".join(f"b.{q(k)} IS NOT DISTINCT FROM a.{q(k)}" for k in key)

    # 4. null rates
    for c in common:
        nb, na = con.execute(
            f"SELECT (SELECT avg(CASE WHEN {q(c)} IS NULL THEN 1.0 ELSE 0 END) FROM {q(before)}), "
            f"(SELECT avg(CASE WHEN {q(c)} IS NULL THEN 1.0 ELSE 0 END) FROM {q(after)})").fetchall()[0]
        nb, na = float(nb or 0) * 100, float(na or 0) * 100
        rise = na - nb
        if rise > 0:
            sev = _grade(rise, t.null_rate_review_pts, t.null_rate_block_pts)
            if c in expect and sev != PASS:
                sev = INFO
            if sev != PASS:
                findings.append(Finding("null_rate", sev,
                                        f"Nulls in '{c}' rose from {nb:.2f}% to {na:.2f}%",
                                        c, {"before_pct": round(nb, 4), "after_pct": round(na, 4)}))

    # 5. changed values on matched keys
    if unique_keys_join is not None:
        matched = con.execute(f"SELECT count(*) FROM _pv_ub b JOIN _pv_ua a ON {unique_keys_join}").fetchall()[0][0]
        stats["matched_rows"] = matched
        for c in common:
            if c in key:
                continue
            if _is_numeric(sb[c]) and _is_numeric(sa[c]):
                diff = (f"(b.{q(c)} IS NULL) <> (a.{q(c)} IS NULL) OR "
                        f"abs(CAST(a.{q(c)} AS DOUBLE) - CAST(b.{q(c)} AS DOUBLE)) > "
                        f"{t.float_tolerance} * greatest(1.0, abs(CAST(b.{q(c)} AS DOUBLE)))")
            else:
                diff = f"CAST(b.{q(c)} AS VARCHAR) IS DISTINCT FROM CAST(a.{q(c)} AS VARCHAR)"
            changed = con.execute(
                f"SELECT count(*) FROM _pv_ub b JOIN _pv_ua a ON {unique_keys_join} WHERE {diff}").fetchall()[0][0]
            if not changed:
                continue
            pct = changed / max(matched, 1) * 100
            if c in expect:
                sev = INFO
            else:
                sev = _grade(pct, t.changed_values_review_pct, t.changed_values_block_pct)
            sample = con.execute(
                f"SELECT {', '.join('b.' + q(k) for k in key)}, b.{q(c)} AS before_value, a.{q(c)} AS after_value "
                f"FROM _pv_ub b JOIN _pv_ua a ON {unique_keys_join} WHERE {diff} LIMIT 3").fetchall()
            findings.append(Finding(
                "changed_values", sev,
                f"{changed:,} row(s) changed '{c}' ({pct:.2f}% of matched rows)"
                + ("" if c not in expect else ", as expected"),
                c, {"count": changed, "pct": round(pct, 4),
                    "examples": [[str(v) for v in row] for row in sample]}))

    # 6. totals of numeric columns
    for c in common:
        if c in key or not (_is_numeric(sb[c]) and _is_numeric(sa[c])):
            continue
        if t.skip_id_like_totals and _looks_like_id(c, sb[c]):
            continue
        tb, ta = con.execute(
            f"SELECT (SELECT sum(CAST({q(c)} AS DOUBLE)) FROM {q(before)}), "
            f"(SELECT sum(CAST({q(c)} AS DOUBLE)) FROM {q(after)})").fetchall()[0]
        if tb is None and ta is None:
            continue
        tb, ta = float(tb or 0), float(ta or 0)
        if tb == ta:
            continue
        pct = abs(ta - tb) / max(abs(tb), 1e-12) * 100
        if c in expect or expect_row_change:
            sev = INFO
        else:
            sev = _grade(pct, t.sum_shift_review_pct, t.sum_shift_block_pct)
        if sev != PASS:
            findings.append(Finding("total_shift", sev,
                                    f"Total of '{c}' moved {pct:.2f}% ({tb:,.2f} -> {ta:,.2f})",
                                    c, {"before": tb, "after": ta, "pct": round(pct, 4)}))

    # 7. category values
    for c in common:
        if c in key or not (_is_text(sb[c]) and _is_text(sa[c])):
            continue
        d = con.execute(f"SELECT count(DISTINCT {q(c)}) FROM {q(before)}").fetchall()[0][0]
        if d == 0 or d > t.category_max_distinct:
            continue
        new_vals = [r[0] for r in con.execute(
            f"SELECT DISTINCT {q(c)} FROM {q(after)} WHERE {q(c)} IS NOT NULL EXCEPT "
            f"SELECT DISTINCT {q(c)} FROM {q(before)} ORDER BY 1 LIMIT 10").fetchall()]
        gone_vals = [r[0] for r in con.execute(
            f"SELECT DISTINCT {q(c)} FROM {q(before)} WHERE {q(c)} IS NOT NULL EXCEPT "
            f"SELECT DISTINCT {q(c)} FROM {q(after)} ORDER BY 1 LIMIT 10").fetchall()]
        if new_vals:
            findings.append(Finding("category_values", INFO if c in expect else REVIEW,
                                    f"'{c}' has new value(s): {', '.join(map(str, new_vals))}",
                                    c, {"new": new_vals}))
        if gone_vals:
            findings.append(Finding("category_values", INFO,
                                    f"'{c}' no longer has value(s): {', '.join(map(str, gone_vals))}",
                                    c, {"gone": gone_vals}))

    return findings, stats


def verdict(findings: list[Finding]) -> str:
    worst = max((RANK[f.severity] for f in findings), default=0)
    return {0: PASS, 1: REVIEW, 2: BLOCK}[worst]
