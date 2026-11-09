"""Disposable native OA smoke check. Does NOT run circuit/EM simulations."""
import argparse
import asyncio
import json
from uuid import uuid4
from ads_mcp.config import load_settings
from ads_mcp.runtime import run_operation

async def main(config):
    settings = load_settings(config)
    token = uuid4().hex[:10]
    workspace = "smoke_" + token + "_wrk"
    library = "smoke_" + token
    cellview = library + ":empty:schematic"
    results = []
    for operation, args in [
        ("create_workspace", {"workspace": workspace, "library": library, "layout_units": "micron", "pdk_libraries": {}}),
        ("create_design", {"workspace": workspace, "cellview": cellview, "kind": "schematic"}),
        ("inspect_design", {"workspace": workspace, "cellview": cellview}),
    ]:
        result = await run_operation(settings, operation, args)
        results.append({"operation": operation, "result": result})
        if result.get("ok") is False:
            raise RuntimeError(result)
    print(json.dumps({"fresh_sessions": True, "workspace": workspace, "results": results,
                      "simulation_started": False, "physical_qualification": False}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.config))
