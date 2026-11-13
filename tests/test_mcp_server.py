import json

import pandas as pd
import pytest

pytest.importorskip("mcp")  # the MCP server is optional and needs Python 3.10+

from pipeverify import mcp_server as M  # noqa: E402


@pytest.fixture
def snaps(tmp_path):
    before = pd.DataFrame({"order_id": range(1, 101), "amount": [10.0] * 100, "status": ["open"] * 100})
    after = before.copy()
    after.loc[after.order_id <= 30, "amount"] = 1000.0       # a unit change nobody asked for
    paths = {}
    for name, df in (("before", before), ("after", after), ("same", before)):
        paths[name] = str(tmp_path / f"{name}.csv")
        df.to_csv(paths[name], index=False)
    raw = pd.concat([before, pd.DataFrame({"order_id": [5], "amount": [99.0], "status": ["open"]})])
    paths["raw"] = str(tmp_path / "raw.csv")
    raw.to_csv(paths["raw"], index=False)
    return paths


def test_compare_blocks_a_harmful_change_and_leaves_files_alone(snaps):
    original = open(snaps["after"], "rb").read()
    r = M.compare_tables(snaps["before"], snaps["after"], key=["order_id"], name="gold.orders")
    assert r["verdict"] == "block" and r["headline"].startswith("BLOCK:") and "gold.orders" in r["headline"]
    assert any(f["column"] == "amount" and f["severity"] == "block" for f in r["tables"][0]["findings"])
    json.dumps(r)  # an MCP result must be JSON serializable
    assert open(snaps["after"], "rb").read() == original


def test_compare_passes_and_respects_expect_change(snaps):
    assert M.compare_tables(snaps["before"], snaps["same"], key=["order_id"])["headline"].startswith("PASS")
    r = M.compare_tables(snaps["before"], snaps["after"], key=["order_id"], expect_change=["amount"])
    assert r["verdict"] != "block" or not any(f["check"].startswith("changed") for f in r["tables"][0]["findings"])


def test_source_can_be_an_object(snaps):
    r = M.compare_tables({"path": snaps["before"]}, {"path": snaps["same"]}, key=["order_id"])
    assert r["verdict"] == "pass"


def test_check_against_source_flags_a_conflict(snaps):
    r = M.check_against_source(snaps["same"], snaps["raw"], key=["order_id"], columns={"amount": "amount"})
    assert r["verdict"] in ("review", "block")
    assert any(f["check"] == "source_conflict" for f in r["tables"][0]["findings"])


def test_run_config(snaps, tmp_path):
    cfg = tmp_path / "pipeverify.yml"
    cfg.write_text("tables:\n  - name: orders\n    before: before.csv\n    after: after.csv\n    key: order_id\n"
                   "  - name: same\n    before: before.csv\n    after: same.csv\n    key: order_id\n")
    r = M.run_config(str(cfg))
    assert r["verdict"] == "block" and [t["verdict"] for t in r["tables"]] == ["block", "pass"]
    assert M.run_config(str(cfg), tables=["same"])["verdict"] == "pass"
    with pytest.raises(ValueError, match="not found"):
        M.run_config(str(tmp_path / "nope.yml"))
    with pytest.raises(ValueError, match="matched"):
        M.run_config(str(cfg), tables=["other"])


def test_server_tools_are_described_and_read_only(snaps):
    import asyncio

    server = M.build_server()
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == {"compare_tables", "check_against_source", "run_config"}
    for t in tools.values():
        d = t.model_dump(by_alias=True)  # SDK 1.x and 2.x name the attributes differently; the wire names are the same
        assert d["annotations"]["readOnlyHint"] and not d["annotations"]["destructiveHint"] and d["annotations"]["title"]
        assert d["outputSchema"] and "verdict" in d["outputSchema"]["properties"]
        assert all(p.get("description") for p in d["inputSchema"]["properties"].values()), t.name
        assert len(t.description) > 400


def test_errors_reach_the_agent_as_text(snaps):
    import asyncio

    server = M.build_server()
    with pytest.raises(Exception) as e:
        asyncio.run(server.call_tool("compare_tables", {"before": "missing.csv", "after": snaps["after"]}))
    assert "missing.csv" in str(e.value)
    out = asyncio.run(server.call_tool("compare_tables", {"before": snaps["before"], "after": snaps["after"], "key": ["order_id"]}))
    assert "block" in json.dumps(out, default=str)
