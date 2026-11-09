"""Separate owned official ADS subprocess per operation, never borrowed GUI sessions."""
from __future__ import annotations
import hashlib
import inspect
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from .config import Settings, scoped_path

MARKER = "ZHANG_ADS_RESULT="

def worker_environment(settings):
    env = dict(os.environ)
    for name in list(env):
        if any(word in name.upper() for word in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")):
            env.pop(name, None)
    if settings.ads_home:
        env["HPEESOF_DIR"] = str(settings.ads_home)
        env["PATH"] = str(settings.ads_home / "bin") + os.pathsep + env.get("PATH", "")
    env.update(PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    # Preserve license/solver thread settings; do not inherit artificial Python test limits.
    return env

def atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)

def _official_values(payload):
    structured = payload.get("structuredContent")
    if isinstance(structured, dict):
        yield structured
    for block in payload.get("content", []):
        if block.get("type") != "text":
            continue
        try:
            value = json.loads(block.get("text", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            yield value


def check_official(payload):
    if payload.get("isError"):
        raise RuntimeError("Official ADS MCP returned an error; inspect the operation transcript")
    for value in _official_values(payload):
        if value.get("ok") is False or value.get("success") is False or value.get("error"):
            raise RuntimeError("Official ADS operation failed: " + str(value)[:2000])
    return payload


def extract_result(payload):
    check_official(payload)
    texts = [block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text"]
    for value in _official_values(payload):
        texts.extend(value[key] for key in ("stdout", "output", "result") if isinstance(value.get(key), str))
    for text in reversed(texts):
        for line in reversed(text.splitlines()):
            if line.startswith(MARKER):
                try:
                    value = json.loads(line[len(MARKER):])
                except ValueError as exc:
                    raise RuntimeError("Invalid native result marker; completion is unknown") from exc
                if not isinstance(value, dict):
                    raise RuntimeError("Invalid native result")
                return value
    raise RuntimeError("ADS returned no native result marker; completion is unknown. Do not retry automatically.")

async def _with_official(settings, output, callback):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from jsonschema import validate
    if not settings.ads_home:
        raise RuntimeError("Configure ads_home or ADS_HOME before native operations")
    executable = settings.ads_home / "bin" / ("ads-mcp.exe" if os.name == "nt" else "ads-mcp")
    if not executable.is_file():
        raise RuntimeError("Installed official ADS MCP not found: " + str(executable))
    transcript = []
    params = StdioServerParameters(command=str(executable), env=worker_environment(settings), cwd=str(output))
    try:
        with (output / "official-stderr.log").open("w", encoding="utf-8") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=settings.tool_timeout_seconds)) as session:
                    await session.initialize()
                    definitions = {tool.name: tool for tool in (await session.list_tools()).tools}
                    async def call(name, args):
                        if name not in definitions:
                            raise RuntimeError("Installed ADS does not expose: " + name)
                        validate(args, definitions[name].inputSchema)
                        result = await session.call_tool(name, args)
                        body = result.model_dump(mode="json", exclude_none=True)
                        transcript.append({"tool": name, "result": body})
                        # Store the raw completed response before receipt verification.
                        atomic_json(output / "official-transcript.json", {"calls": transcript})
                        return check_official(body)
                    return await callback(call, definitions)
    except BaseException as exc:
        atomic_json(output / "failure.json", {
            "error_type": type(exc).__name__, "message": str(exc)[:4000],
            "retry_performed": False, "completion": "unknown_if_native_work_started"})
        raise

def operation_directory(settings, output_dir=None):
    output = (Path(output_dir) if output_dir else settings.state_dir / "operations" / uuid4().hex).resolve()
    if not output.is_relative_to(settings.state_dir.resolve()):
        raise ValueError("Operation output must be inside state_dir")
    output.mkdir(parents=True, exist_ok=True)
    return output

async def official_tool(settings, name, arguments):
    if name not in {"get_docs", "search_docs", "list_sessions"}:
        raise ValueError("Only read-only official tools are forwarded")
    output = operation_directory(settings)
    async def invoke(call, definitions):
        return await call(name, arguments)
    return await _with_official(settings, output, invoke)

async def discover_official(settings):
    output = operation_directory(settings)
    async def discover(call, definitions):
        return {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                          for t in definitions.values()], "license_checkout_requested": False}
    return await _with_official(settings, output, discover)

async def run_operation(settings, operation, arguments, output_dir=None):
    from .models import validate_operation_args
    arguments = validate_operation_args(operation, arguments)
    if operation == "run_momentum" and arguments["timeout_seconds"] > settings.tool_timeout_seconds:
        raise ValueError("Momentum timeout exceeds configured tool timeout")
    from .native import execute
    from .jobs import workspace_lock
    output = operation_directory(settings, output_dir)
    workspace = arguments.get("workspace") or arguments.get("source_dir") or arguments.get("path")
    if workspace:
        try:
            resolved = scoped_path(settings.project_root, workspace, must_exist=operation != "create_workspace")
        except ValueError:
            if operation != "read_dataset":
                raise
            resolved = scoped_path(settings.state_dir, workspace)
        key = "workspace" if "workspace" in arguments else "source_dir" if "source_dir" in arguments else "path"
        arguments[key] = str(resolved)
        workspace = str(resolved)
    elif operation != "execute_python":
        raise ValueError("Explicit workspace or source path is required")
    if operation == "execute_python" and not settings.allow_execute_python:
        raise PermissionError("Raw ADS Python requires allow_execute_python=true in the local operator config")
    source = inspect.getsource(execute)
    atomic_json(output / "request.json", {
        "operation": operation, "arguments": arguments,
        "native_source_sha256": hashlib.sha256(source.encode()).hexdigest()})
    async def invoke(call, definitions):
        await call("start_local_session", {})
        if operation == "execute_python":
            result = {"ok": True, "raw_official_response": await call("execute_python", {"code": arguments["code"]}),
                      "evidence_directory": str(output), "physical_qualification": False}
            atomic_json(output / "result.json", result)
            return result
        code = (source + "\nimport json as _zh_json\n" +
                f"_zh_result = execute({operation!r}, {arguments!r}, {str(settings.project_root)!r}, {str(output)!r}, {str(settings.state_dir)!r})\n" +
                f"print({MARKER!r} + _zh_json.dumps(_zh_result, ensure_ascii=False, allow_nan=False))\n")
        result = extract_result(await call("execute_python", {"code": code}))
        atomic_json(output / "result.json", result)
        return {**result, "evidence_directory": str(output), "physical_qualification": False}
    lock_target = settings.project_root if operation in {"execute_python", "read_dataset"} else workspace
    with workspace_lock(settings, str(lock_target)):
        return await _with_official(settings, output, invoke)
