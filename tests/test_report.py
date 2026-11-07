"""Tests for the text report: how many example rows a finding shows."""
import subprocess
import sys

import pandas as pd

import pipeverify
from pipeverify.core import RunResult
from pipeverify.report import to_text


def changed_table():
    before = pd.DataFrame({"id": [1, 2, 3, 4], "amount": [10.0, 20.0, 30.0, 40.0]})
    after = pd.DataFrame({"id": [1, 2, 3, 4], "amount": [11.0, 22.0, 33.0, 44.0]})
    return RunResult(tables=[pipeverify.verify(before, after, key="id")])


def eg_lines(text):
    return [ln for ln in text.splitlines() if "e.g." in ln]


def test_default_output_unchanged_one_example():
    res = changed_table()
    assert len(eg_lines(to_text(res))) == 1
    assert len(eg_lines(to_text(res, max_examples=1))) == 1


def test_max_examples_shows_more_rows():
    res = changed_table()
    text = to_text(res, max_examples=3)
    assert len(eg_lines(text)) == 3


def test_max_examples_zero_shows_none():
    res = changed_table()
    assert len(eg_lines(to_text(res, max_examples=0))) == 0


def test_cli_max_examples_flag(tmp_path):
    before, after = tmp_path / "b.csv", tmp_path / "a.csv"
    pd.DataFrame({"id": [1, 2, 3, 4], "amount": [10.0, 20.0, 30.0, 40.0]}).to_csv(before, index=False)
    pd.DataFrame({"id": [1, 2, 3, 4], "amount": [11.0, 22.0, 33.0, 44.0]}).to_csv(after, index=False)
    r = subprocess.run([sys.executable, "-m", "pipeverify.cli", "compare", str(before), str(after),
                        "-k", "id", "--max-examples", "3"],
                       capture_output=True, text=True)
    assert len([ln for ln in r.stdout.splitlines() if "e.g." in ln]) == 3, r.stdout + r.stderr
    r = subprocess.run([sys.executable, "-m", "pipeverify.cli", "compare", str(before), str(after), "-k", "id"],
                       capture_output=True, text=True)
    assert len([ln for ln in r.stdout.splitlines() if "e.g." in ln]) == 1, r.stdout + r.stderr
