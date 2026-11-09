"""Real stdio handshake; no native ADS or license checkout."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def check(config):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "ads_mcp", "--config", str(config.resolve()), "serve"], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = (await session.list_tools()).tools
            names = {tool.name for tool in tools}
            assert "ads_doctor" in names and "ads_simulate" in names and "ads_run_momentum" in names
            result = await session.call_tool("ads_doctor", {})
            assert not result.isError, result
            # Untrusted clients cannot opt in to arbitrary Python at tool-call time.
            print(json.dumps({"initialize": True, "tools": len(tools), "doctor_roundtrip": True,
                              "native_ADS_started": False, "server": init.serverInfo.name}, ensure_ascii=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.config:
        asyncio.run(check(args.config))
    else:
        with tempfile.TemporaryDirectory(prefix="ads-mcp-protocol-") as folder:
            path = Path(folder)
            config = path / "check.toml"
            config.write_text("[ads_mcp]\nproject_root=" + json.dumps(str(path).replace("\\", "/")) +
                              "\nstate_dir=" + json.dumps(str(path / "state").replace("\\", "/")) + "\n",
                              encoding="utf-8")
            asyncio.run(check(config))
