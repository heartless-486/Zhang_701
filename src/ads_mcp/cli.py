from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path

from .config import load_settings, doctor
from .runtime import discover_official, run_operation

def main(argv=None):
    import sys
    # Machine-readable CLI JSON must remain UTF-8 under Windows console redirection.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Deployable ADS MCP using the installed official Keysight runtime")
    parser.add_argument("--config", type=Path, help="TOML config; defaults to ADS_MCP_CONFIG or ads-mcp.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Serve MCP on stdio")
    commands.add_parser("doctor", help="Read-only environment check; no ADS license checkout")
    commands.add_parser("capabilities", help="Discover installed official MCP schemas")
    call = commands.add_parser("call", help="Run a typed primitive (simulation uses submit instead)")
    call.add_argument("operation", choices=["create_workspace", "create_design", "inspect_design", "edit_design",
                                          "generate_netlist", "read_dataset"])
    call.add_argument("--args-file", type=Path, required=True)
    submit = commands.add_parser("submit", help="Submit an isolated paid solver attempt")
    submit.add_argument("operation", choices=["simulate_design", "run_momentum"])
    submit.add_argument("--args-file", type=Path, required=True)
    status = commands.add_parser("status")
    status.add_argument("job_id")
    commands.add_parser("jobs")
    commands.add_parser("client-config", help="Emit absolute-path MCP config for this interpreter")
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    if args.command == "serve":
        from .server import build_server
        build_server(settings).run(transport="stdio")
        return 0
    if args.command == "doctor":
        result = doctor(settings)
    elif args.command == "capabilities":
        result = asyncio.run(discover_official(settings))
    elif args.command == "call":
        values = json.loads(args.args_file.read_text(encoding="utf-8-sig"))
        result = asyncio.run(run_operation(settings, args.operation, values))
    elif args.command in ("submit", "status", "jobs"):
        from .jobs import JobManager
        jobs = JobManager(settings)
        if args.command == "submit":
            from .models import validate_operation_args
            values = validate_operation_args(args.operation, json.loads(args.args_file.read_text(encoding="utf-8-sig")))
            result = jobs.submit(args.operation, values)
        elif args.command == "status":
            result = jobs.status(args.job_id)
        else:
            result = jobs.list_jobs()
    else:
        import sys
        import os
        config = (args.config or Path(os.environ.get("ADS_MCP_CONFIG", "ads-mcp.toml"))).resolve()
        result = {"mcpServers": {"zhang-ads": {"command": sys.executable,
                  "args": ["-m", "ads_mcp", "--config", str(config), "serve"]}}}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0
