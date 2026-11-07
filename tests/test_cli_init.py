"""Tests for `pipeverify init`, which writes the starter config file."""
import yaml
from click.testing import CliRunner

from pipeverify.cli import main


def test_init_writes_config_and_prints_message(tmp_path):
    runner = CliRunner()
    path = tmp_path / "pipeverify.yml"
    res = runner.invoke(main, ["init", "--path", str(path)])
    assert res.exit_code == 0, res.output
    assert path.exists()
    assert f"Wrote {path}" in res.output


def test_init_refuses_to_overwrite(tmp_path):
    runner = CliRunner()
    path = tmp_path / "pipeverify.yml"
    assert runner.invoke(main, ["init", "--path", str(path)]).exit_code == 0
    path.write_text("# my own edits\n", encoding="utf-8")
    res = runner.invoke(main, ["init", "--path", str(path)])
    assert res.exit_code != 0
    assert "already exists" in res.output
    assert path.read_text(encoding="utf-8") == "# my own edits\n"


def test_written_config_is_valid_yaml_with_tables(tmp_path):
    runner = CliRunner()
    path = tmp_path / "pipeverify.yml"
    assert runner.invoke(main, ["init", "--path", str(path)]).exit_code == 0
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(cfg, dict)
    assert isinstance(cfg.get("tables"), list) and cfg["tables"]
