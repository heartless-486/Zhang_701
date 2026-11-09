"""Standard MCP SDK owns transport, schema validation, and stdout discipline."""
from __future__ import annotations
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import doctor, scoped_path
from .jobs import JobManager
from .models import Edit
from .runtime import discover_official, official_tool, run_operation

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

def build_server(settings):
    mcp = FastMCP("Zhang ADS MCP", instructions=(
        "Inspect ads_doctor and version-matched ADS documentation first. All native operations "
        "use separate owned headless sessions and an explicit workspace. Never select a GUI session "
        "by foreground window. Poll returned job IDs instead of launching duplicates. "
        "MCP installation, native execution, and RF/physical qualification are separate evidence. "
        "Raw Python is trusted local code, not an operating-system sandbox."
    ))
    jobs = JobManager(settings)

    @mcp.tool(annotations=READ)
    def ads_doctor() -> dict:
        """Inspect local configuration/resources without launching ADS or checking out a license."""
        return doctor(settings)

    @mcp.tool(annotations=READ)
    async def ads_capabilities() -> dict:
        """Discover exact installed official MCP tool schemas. Does not start a local ADS session."""
        return await discover_official(settings)

    @mcp.tool(annotations=READ)
    async def ads_get_docs(path: str = "") -> dict:
        """Read installed ADS API documentation. Empty path opens the official documentation index."""
        return await official_tool(settings, "get_docs", {"path": path})

    @mcp.tool(annotations=READ)
    async def ads_search_docs(query: str) -> dict:
        """Search installed version-matched official ADS documentation."""
        return await official_tool(settings, "search_docs", {"query": query})

    @mcp.tool(annotations=READ)
    async def ads_list_sessions() -> dict:
        """List GUI sessions for inspection only. This server never connects to or changes them."""
        return await official_tool(settings, "list_sessions", {})

    @mcp.tool(annotations=READ)
    def ads_list_files(path: str = ".", pattern: str = "*") -> dict:
        """List bounded immediate project files. No recursive wildcard or hidden-file export."""
        import fnmatch
        directory = scoped_path(settings.project_root, path)
        if not directory.is_dir() or "/" in pattern or "\\" in pattern:
            raise ValueError("Use one project directory and a filename pattern")
        entries = []
        for item in directory.iterdir():
            if len(entries) >= 500:
                return {"files": entries, "truncated": True}
            if item.name.startswith(".") or not fnmatch.fnmatch(item.name, pattern):
                continue
            try:
                safe = scoped_path(settings.project_root, str(item))
            except ValueError:
                continue
            entries.append({"path": str(safe.relative_to(settings.project_root)), "directory": safe.is_dir()})
        return {"files": entries, "truncated": False}

    @mcp.tool(annotations=READ)
    def ads_read_text(path: str, offset: int = 0, limit: int = 16000) -> dict:
        """Read a bounded project log/netlist/JSON/CSV/TOML file, with pagination."""
        import codecs
        target = scoped_path(settings.project_root, path)
        if any(part.startswith(".") for part in target.relative_to(settings.project_root).parts):
            raise ValueError("Hidden project files are not exposed by text artifact reads")
        if target.suffix.lower() not in {".log", ".txt", ".json", ".csv", ".toml", ".ckt", ".net", ".pin", ".cfg"}:
            raise ValueError("Unsupported text artifact suffix")
        if not 0 <= offset <= 10_000_000 or not 1 <= limit <= 64000:
            raise ValueError("Invalid bounded range")
        with target.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(limit)
            more = bool(stream.peek(1))
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            text = decoder.decode(data, final=not more)
            # Complete a boundary character, consuming at most three additional bytes.
            while decoder.getstate()[0] and more:
                tail = stream.read(1)
                more = bool(stream.peek(1))
                text += decoder.decode(tail, final=not more)
            next_offset = stream.tell() if more else None
        return {"text": text, "next_offset": next_offset, "offset_unit": "UTF-8 bytes"}

    @mcp.tool(annotations=WRITE)
    async def ads_create_workspace(workspace: str, library: str, layout_units: str = "micron",
                                   pdk_libraries: dict[str, str] | None = None) -> dict:
        """Create a NEW workspace/library; optional local PDK libraries are attached read-only."""
        return await run_operation(settings, "create_workspace", {
            "workspace": workspace, "library": library, "layout_units": layout_units,
            "pdk_libraries": pdk_libraries or {}})

    @mcp.tool(annotations=WRITE)
    async def ads_create_design(workspace: str, cellview: str, kind: Literal["schematic", "layout"]) -> dict:
        """Create a new library:cell:view without overwriting an existing design."""
        return await run_operation(settings, "create_design", dict(workspace=workspace, cellview=cellview, kind=kind))

    @mcp.tool(annotations=READ)
    async def ads_inspect_design(workspace: str, cellview: str) -> dict:
        """Read actual saved instances, parameters and pin/net information in an owned ADS session."""
        return await run_operation(settings, "inspect_design", dict(workspace=workspace, cellview=cellview))

    @mcp.tool(annotations=WRITE)
    async def ads_edit_design(workspace: str, cellview: str, edits: list[Edit]) -> dict:
        """Snapshot then apply typed edits and read back. Partial saved edits are reported explicitly."""
        if not 1 <= len(edits) <= 100:
            raise ValueError("Provide 1–100 edits")
        return await run_operation(settings, "edit_design", {
            "workspace": workspace, "cellview": cellview, "snapshot": True,
            "edits": [edit.model_dump() for edit in edits]})

    @mcp.tool(annotations=READ)
    async def ads_generate_netlist(workspace: str, cellview: str) -> dict:
        """Generate and hash the saved schematic netlist; does not start a circuit solve."""
        return await run_operation(settings, "generate_netlist", dict(workspace=workspace, cellview=cellview))

    @mcp.tool(annotations=WRITE)
    def ads_simulate(workspace: str, cellview: str, dataset_name: str = "simulation",
                     simulator_options: dict[str, str | list[str]] | None = None) -> dict:
        """Submit ONE isolated circuit simulation of existing SP/DC/AC/HB/noise controllers; poll job ID."""
        from .models import validate_operation_args
        arguments = validate_operation_args("simulate_design", dict(workspace=workspace, cellview=cellview,
                           dataset_name=dataset_name, simulator_options=simulator_options or {}))
        return jobs.submit("simulate_design", arguments)

    @mcp.tool(annotations=WRITE)
    def ads_run_momentum(source_dir: str, input_hashes: dict[str, str], timeout_seconds: int = 3600,
                               solver_threads: int | None = None) -> dict:
        """Submit ONE Momentum solve on a protected copy of preprocessed proj inputs with explicit hashes."""
        if not 1 <= timeout_seconds <= settings.tool_timeout_seconds:
            raise ValueError("timeout_seconds exceeds configured tool timeout")
        from .models import validate_operation_args
        arguments = validate_operation_args("run_momentum", dict(source_dir=source_dir, input_hashes=input_hashes,
                                                timeout_seconds=timeout_seconds, solver_threads=solver_threads))
        return jobs.submit("run_momentum", arguments)

    @mcp.tool(annotations=READ)
    async def ads_read_dataset(path: str, block: str = "") -> dict:
        """List native dataset blocks or export one block to CSV; retain full complex values."""
        return await run_operation(settings, "read_dataset", dict(path=path, block=block))

    @mcp.tool(annotations=READ)
    def ads_job_status(job_id: str) -> dict:
        """Read persisted status and bounded logs. A failed/unknown attempt is never a free retry."""
        return jobs.status(job_id)

    @mcp.tool(annotations=READ)
    def ads_list_jobs() -> list[dict]:
        """List retained attempts, including failed, cancelled and interrupted work."""
        return jobs.list_jobs()

    @mcp.tool(annotations=WRITE)
    def ads_cancel_job(job_id: str) -> dict:
        """Cancel only a worker created by this server instance; preserve all attempt evidence."""
        return jobs.cancel(job_id)

    if settings.allow_execute_python:
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
        async def ads_execute_python(code: str) -> dict:
            """TRUSTED CODE: execute native ADS Python in a new owned session. No OS sandbox is provided."""
            if not 1 <= len(code) <= 60000:
                raise ValueError("Provide 1–60000 characters")
            return await run_operation(settings, "execute_python", {"code": code})
    return mcp
