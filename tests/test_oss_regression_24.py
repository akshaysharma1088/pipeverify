from xml.etree import ElementTree as ET

import pandas as pd
import yaml
from click.testing import CliRunner

from pipeverify.cli import main


def test_junit_reports_for_run_and_compare(tmp_path):
    before = tmp_path / "before.csv"
    after = tmp_path / "after.csv"
    frame = pd.DataFrame({"region": ["north", "north"], "cost <&>": [10, 11]})
    frame.to_csv(before, index=False)
    changed = frame.drop(columns="cost <&>").assign(region=["north", "south & west"])
    changed.to_csv(after, index=False)

    blocked = "blocked <&> sales"
    config = tmp_path / "pipeverify.yml"
    config.write_text(yaml.safe_dump({"tables": [
        {"name": blocked, "before": before.name, "after": after.name},
        {"name": "passing sales", "before": before.name, "after": before.name},
    ]}), encoding="utf-8")

    def counts(element):
        return tuple(int(element.attrib[name]) for name in ("tests", "failures", "skipped"))

    runner = CliRunner()
    run_report = tmp_path / "run.xml"
    result = runner.invoke(main, ["run", "--config", str(config), "--junit-out", str(run_report)])
    assert run_report.is_file(), result.output
    assert result.exit_code == 1
    assert "Verdict: BLOCK" in result.output
    root = ET.parse(run_report).getroot()
    assert root.tag == "testsuites"
    assert counts(root) == (4, 1, 1)

    blocked_suite, passing_suite = root.findall("testsuite")
    assert blocked_suite.attrib["name"] == blocked
    assert counts(blocked_suite) == (3, 1, 1)
    blocked_cases = blocked_suite.findall("testcase")
    assert len(blocked_cases) == 3
    assert [node.attrib["message"] for node in blocked_suite.findall("testcase/failure")] == [
        "Column 'cost <&>' was removed",
    ]
    assert [node.attrib["message"] for node in blocked_suite.findall("testcase/skipped")] == [
        "'region' has new value(s): south & west",
    ]
    assert passing_suite.attrib["name"] == "passing sales"
    assert counts(passing_suite) == (1, 0, 0)
    passing_case, = passing_suite.findall("testcase")
    assert len(passing_case) == 0

    compare_report = tmp_path / "compare.xml"
    result = runner.invoke(main, [
        "compare", str(before), str(after), "--name", blocked, "--junit-out", str(compare_report),
    ])
    assert compare_report.is_file(), result.output
    assert result.exit_code == 1
    compare_root = ET.parse(compare_report).getroot()
    assert counts(compare_root) == (3, 1, 1)
    compare_suite, = compare_root.findall("testsuite")
    assert compare_suite.attrib == blocked_suite.attrib
