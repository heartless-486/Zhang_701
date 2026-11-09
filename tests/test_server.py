import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def value(result):
    assert not result.isError
    if result.structuredContent:
        return result.structuredContent
    return json.loads(next(block.text for block in result.content if block.type == "text"))


def test_actual_mcp_stdio_roundtrip_without_ads_or_rf(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sample = "alpha 漢字 café\n" * 4
    (project / "sample.txt").write_text(sample, encoding="utf8", newline="")
    (project / ".hidden.json").write_text('{"private":true}')
    state = tmp_path / "state"
    config = tmp_path / "config.toml"
    config.write_text('[ads_mcp]\nproject_root = ' + json.dumps(str(project)) + '\nstate_dir = ' + json.dumps(str(state)) + '\n', encoding="utf8")
    env = {key: val for key, val in os.environ.items() if not key.startswith("ADS_MCP_") and key not in {"ADS_HOME", "HPEESOF_DIR"}}
    source = str(Path(__file__).resolve().parents[1] / "src")
    env.update(PYTHONPATH=source, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    params = StdioServerParameters(command=sys.executable, args=["-m", "ads_mcp", "--config", str(config), "serve"], env=env)
    async def run():
        with (tmp_path / "mcp-stderr.log").open("w", encoding="utf8") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    names = {tool.name for tool in (await client.list_tools()).tools}
                    assert {"ads_doctor", "ads_simulate", "ads_read_text", "ads_run_momentum"} <= names
                    assert "ads_execute_python" not in names
                    info = value(await client.call_tool("ads_doctor", {}))
                    assert info["simulation_started"] is False
                    assert info["license_status"] == "not_probed"
                    files = value(await client.call_tool("ads_list_files", {}))["files"]
                    assert {item["path"] for item in files} == {"sample.txt"}
                    chunks, offset = [], 0
                    while True:
                        page = value(await client.call_tool("ads_read_text", {"path": "sample.txt", "offset": offset, "limit": 1}))
                        chunks.append(page["text"])
                        if page["next_offset"] is None:
                            break
                        assert page["next_offset"] > offset
                        offset = page["next_offset"]
                    assert "".join(chunks) == sample
                    denied = await client.call_tool("ads_read_text", {"path": ".hidden.json"})
                    assert denied.isError
                    bad_edit = await client.call_tool("ads_edit_design", {"workspace": "missing", "cellview": "lib:cell:layout", "edits": [{"action": "rectangle", "layer": 1, "points": [[0, 0], [1, 1], [2, 2]]}]})
                    assert bad_edit.isError
    asyncio.run(run())
    assert not list(state.rglob("request.json")), "Read-only and schema rejection must not launch native work"
