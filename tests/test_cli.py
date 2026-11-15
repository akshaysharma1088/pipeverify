"""Tests for the pipeverify CLI, including the --quiet option."""
from pathlib import Path

from click.testing import CliRunner

from pipeverify.cli import main


def test_quiet_prints_only_verdict(tmp_path: Path):
    """--quiet prints exactly the verdict word and preserves the exit code."""
    before = tmp_path / "before.csv"
    after = tmp_path / "after.csv"
    before.write_text("id,value\n1,10\n", encoding="utf-8")
    after.write_text("id,value\n1,10\n", encoding="utf-8")  # identical -> PASS

    runner = CliRunner()

    # without quiet
    res_full = runner.invoke(main, ["compare", str(before), str(after), "--key", "id"])
    assert res_full.exit_code == 0
    assert "PASS" in res_full.output
    assert len(res_full.output.strip().splitlines()) > 1  # full report

    # with quiet
    res_quiet = runner.invoke(main, ["compare", str(before), str(after), "--key", "id", "--quiet"])
    assert res_quiet.exit_code == 0
    assert res_quiet.output.strip() == "PASS"

    # exit code same for a failing case
    after_bad = tmp_path / "after_bad.csv"
    after_bad.write_text("id,value\n1,20\n", encoding="utf-8")  # value changed -> likely REVIEW or BLOCK
    res_fail = runner.invoke(main, ["compare", str(before), str(after_bad), "--key", "id", "--quiet"])
    assert res_fail.exit_code != 0
    assert res_fail.output.strip() in ("REVIEW", "BLOCK")
