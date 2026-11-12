import json
import os
import subprocess
import sys

import pandas as pd
import pytest

import pipeverify
from pipeverify.checks import Thresholds

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEMO = os.path.join(ROOT, "examples", "retail")


def base(n=100):
    return pd.DataFrame({
        "id": range(1, n + 1),
        "region": ["North", "South"] * (n // 2),
        "amount": [float(i) for i in range(1, n + 1)],
    })


def messages(res):
    return [(f.check, f.severity) for f in res.findings]


def test_identical_tables_pass():
    res = pipeverify.verify(base(), base(), key="id")
    assert res.verdict == "pass"
    assert not [f for f in res.findings if f.severity in ("review", "block")]


def test_lost_rows_block():
    res = pipeverify.verify(base(), base().iloc[:80], key="id")
    assert res.verdict == "block"
    assert ("missing_keys", "block") in messages(res)
    assert ("row_count", "block") in messages(res)


def test_expected_row_change_is_info():
    res = pipeverify.verify(base(), base().iloc[:80], key="id", expect_row_change=True)
    assert res.verdict == "pass"


def test_duplicate_keys_block():
    after = pd.concat([base(), base().iloc[:1]])
    res = pipeverify.verify(base(), after, key="id")
    assert ("duplicate_keys", "block") in messages(res)


def test_unexpected_value_change_reviews_and_expected_passes():
    after = base()
    after.loc[0, "amount"] = 999.0
    th = {"sum_shift_review_pct": 50, "sum_shift_block_pct": 90}  # isolate the changed-values check
    assert pipeverify.verify(base(), after, key="id", thresholds=th).verdict == "review"
    assert pipeverify.verify(base(), after, key="id", expect_change=["amount"]).verdict == "pass"


def test_float_noise_is_ignored():
    after = base()
    after["amount"] = after["amount"] + 1e-12
    assert pipeverify.verify(base(), after, key="id").verdict == "pass"


def test_null_creep_and_new_category():
    after = base()
    after.loc[:4, "region"] = None
    after.loc[10, "region"] = "Mars"
    res = pipeverify.verify(base(), after, key="id")
    kinds = messages(res)
    assert ("null_rate", "review") in kinds
    assert ("category_values", "review") in kinds


def test_schema_changes():
    after = base().drop(columns=["amount"]).assign(extra=1)
    res = pipeverify.verify(base(), after, key="id")
    assert ("schema", "block") in messages(res)  # removed column
    assert ("schema", "info") in messages(res)   # added column


def test_ignore_column():
    after = base().drop(columns=["amount"])
    assert pipeverify.verify(base(), after, key="id", ignore=["amount"]).verdict == "pass"


def test_no_key_still_checks_totals():
    after = base()
    after["amount"] = after["amount"] * 100
    res = pipeverify.verify(base(), after)
    assert res.verdict == "block"
    assert any(f.check == "keys" for f in res.findings)


def test_bad_threshold_name():
    with pytest.raises(ValueError):
        Thresholds.from_dict({"nope": 1})


def test_missing_key_column():
    with pytest.raises(ValueError):
        pipeverify.verify(base(), base(), key="missing")


@pytest.mark.parametrize("scenario,expected", [
    ("1_safe_fix", "pass"),
    ("2_dedupe_drops_rows", "block"),
    ("3_join_fanout", "block"),
    ("4_unit_change", "block"),
    ("5_null_creep", "review"),
])
def test_demo_scenarios(scenario, expected):
    cfg = os.path.join(DEMO, "scenarios", scenario, "pipeverify.yml")
    if not os.path.exists(cfg):
        subprocess.run([sys.executable, os.path.join(DEMO, "make_demo.py")], check=True)
    assert pipeverify.run_config(cfg).verdict == expected


def test_cli_json_and_exit_codes(tmp_path):
    b, a = tmp_path / "b.csv", tmp_path / "a.csv"
    base().to_csv(b, index=False)
    base().iloc[:50].to_csv(a, index=False)
    out = subprocess.run([sys.executable, "-m", "pipeverify.cli", "compare", str(b), str(a),
                          "--key", "id", "--format", "json"], capture_output=True, text=True)
    assert out.returncode == 1
    assert json.loads(out.stdout)["verdict"] == "block"
    out = subprocess.run([sys.executable, "-m", "pipeverify.cli", "compare", str(b), str(a),
                          "--key", "id", "--fail-on", "never"], capture_output=True, text=True)
    assert out.returncode == 0

def test_skip_id_like_totals_default():
    before = pd.DataFrame({"order_id": [1, 2], "amount": [10.0, 20.0]})
    after = pd.DataFrame({"order_id": [1, 2, 3], "amount": [10.0, 20.0, 30.0]})
    res = pipeverify.verify(before, after, expect_row_change=True)
    shift_cols = [f.column for f in res.findings if f.check == "total_shift"]
    assert "amount" in shift_cols
    assert "order_id" not in shift_cols

def test_skip_id_like_totals_off():
    before = pd.DataFrame({"order_id": [1, 2], "amount": [10.0, 20.0]})
    after = pd.DataFrame({"order_id": [1, 2, 3], "amount": [10.0, 20.0, 30.0]})
    res = pipeverify.verify(before, after, expect_row_change=True, thresholds={"skip_id_like_totals": False})
    shift_cols = [f.column for f in res.findings if f.check == "total_shift"]
    assert "amount" in shift_cols
    assert "order_id" in shift_cols