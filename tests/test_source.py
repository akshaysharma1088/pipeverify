import subprocess
import sys
from openpyxl import Workbook

import pandas as pd
import pytest

import pipeverify

import duckdb
from pipeverify.sources import Source, SourceError, load


def raw():
    return pd.DataFrame({"txn_id": ["1", "2", "3", "4", "4"], "amt": [10.0, 20.0, 30.0, 40.0, 44.0],
                         "status": ["OK", "OK", "OK", "VOID", "OK"]})


def out(rows):
    return pd.DataFrame(rows, columns=["txn_id", "amount"])


SRC = {"key": ["txn_id"], "columns": {"amount": "amt"}}


def kinds(res):
    return {(f.check, f.severity) for f in res.findings}


def test_source_only_clean_output_passes():
    o = out([(1, 10.0), (2, 20.0), (3, 30.0)])
    s = pd.DataFrame({"txn_id": ["1", "2", "3"], "amt": [10.0, 20.0, 30.0]})
    res = pipeverify.verify(None, o, key="txn_id", source={**SRC, "data": s})
    assert res.verdict == "pass"


def test_conflicting_source_values_need_review():
    o = out([(1, 10.0), (2, 20.0), (3, 30.0), (4, 40.0)])
    res = pipeverify.verify(None, o, key="txn_id", source={**SRC, "data": raw(), "checks": ["conflict"]})
    assert ("source_conflict", "review") in kinds(res)


def test_value_not_in_source_is_flagged():
    o = out([(1, 10.0), (2, 2000.0), (3, 30.0), (4, 44.0)])
    res = pipeverify.verify(None, o, key="txn_id", source={**SRC, "data": raw(), "checks": ["mismatch"]})
    assert ("source_mismatch", "block") in kinds(res)  # 25% of rows


def test_missing_and_orphan_keys_with_filter():
    o = out([(1, 10.0), (2, 20.0), (9, 1.0)])
    res = pipeverify.verify(None, o, key="txn_id",
                            source={**SRC, "data": raw(), "where": "status = 'OK'", "checks": ["missing", "orphan"]})
    k = kinds(res)
    assert ("source_missing", "block") in k   # keys 3 and 4 never arrived
    assert ("source_orphan", "review") in k    # key 9 is not in the source


def test_source_and_diff_together():
    before = out([(1, 10.0), (2, 20.0)])
    after = out([(1, 10.0), (2, 20.0), (3, 30.0)])
    s = pd.DataFrame({"txn_id": ["1", "2", "3"], "amt": [10.0, 20.0, 30.0]})
    res = pipeverify.verify(before, after, key="txn_id", expect_row_change=True, source={**SRC, "data": s})
    assert res.verdict == "pass"


def test_needs_before_or_source():
    with pytest.raises(ValueError):
        pipeverify.verify(None, out([(1, 1.0)]), key="txn_id")


def test_cli_source_only(tmp_path):
    o, s = tmp_path / "o.csv", tmp_path / "s.csv"
    out([(1, 10.0), (4, 40.0)]).to_csv(o, index=False)
    raw().to_csv(s, index=False)
    r = subprocess.run([sys.executable, "-m", "pipeverify.cli", "compare", "-", str(o), "-k", "txn_id",
                        "--source", str(s), "--source-column", "amount=amt", "--fail-on", "review"],
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "conflicting" in r.stdout

def test_excel_source(tmp_path):
    wb = Workbook()
    excel_path = tmp_path / "sample.xlsx"
    ws = wb.active
    ws.append(["txn_id", "amt"])
    ws.append(["1", 10.0])
    wb.save(excel_path)
    con = duckdb.connect()

    try:
        load(con, Source(kind="file", path=str(excel_path)), "test_data")
    except SourceError as e:
        pytest.skip(f"Excel extension unavailable: {e}")

    rows = con.execute("SELECT * FROM test_data").fetchall()
    assert rows == [("1", 10.0)]

