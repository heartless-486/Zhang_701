import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ads_mcp.config import Settings
from ads_mcp import runtime
from ads_mcp.models import validate_operation_args


@pytest.fixture
def settings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "workspace").mkdir()
    return Settings(None, project, tmp_path / "state", tool_timeout_seconds=3600)


def response(value, structured=False):
    return {"structuredContent": value} if structured else {"content": [{"type": "text", "text": json.dumps(value)}]}


@pytest.mark.parametrize("structured", [False, True])
def test_official_errors_and_result_marker(structured):
    with pytest.raises(RuntimeError, match="failed"):
        runtime.check_official(response({"success": False, "stderr": "native failure"}, structured))
    with pytest.raises(RuntimeError, match="error"):
        runtime.check_official({"isError": True})
    body = response({"ok": True, "stdout": "noise\n" + runtime.MARKER + '{"ok":true,"count":2}\n'}, structured)
    assert runtime.extract_result(body) == {"ok": True, "count": 2}
    with pytest.raises(RuntimeError, match="unknown"):
        runtime.extract_result(response({"stdout": "truncated response"}, structured))
    with pytest.raises(RuntimeError, match="Invalid native result"):
        runtime.extract_result(response({"stdout": runtime.MARKER + '[]'}, structured))


def test_cli_worker_validation_is_not_only_mcp():
    with pytest.raises(ValueError):
        validate_operation_args("unknown", {})
    with pytest.raises(ValueError):
        validate_operation_args("inspect_design", [])
    with pytest.raises(ValidationError):
        validate_operation_args("inspect_design", {"workspace": "w", "cellview": "bad", "ignored": True})
    with pytest.raises(ValidationError):
        validate_operation_args("simulate_design", {"workspace": "w", "cellview": "lib:cell:schematic", "dataset_name": "../../escape"})
    with pytest.raises(ValidationError):
        validate_operation_args("run_momentum", {"source_dir": "w", "input_hashes": {"proj.pin": "not a hash"}})
    args = validate_operation_args("simulate_design", {"workspace": "w", "cellview": "lib:cell:schematic", "simulator_options": {"pdk_dirs": ["models"], "rel_data_dir": "data"}})
    assert args["simulator_options"]["pdk_dirs"] == ["models"]
    with pytest.raises(ValidationError):
        validate_operation_args("simulate_design", {"workspace": "w", "cellview": "lib:cell:schematic", "simulator_options": {"shell": "command"}})


@pytest.mark.parametrize("edit", [
    {"action": "rectangle", "layer": 1, "points": [[0, 0], [1, 1], [2, 2]]},
    {"action": "rectangle", "layer": 1, "points": [[0, 0], [0, 1]]},
    {"action": "polygon", "layer": 1, "points": [[0, 0], [1, 1]]},
    {"action": "path", "layer": 1, "points": [[0, 0], [float("nan"), 1]]},
    {"action": "add_wire", "points": [[False, 0], [1, 1]]},
    {"action": "connect", "from_instance": "a", "from_pin": -1, "to_instance": "b", "to_pin": 0, "net": "n"},
])
def test_invalid_edits_rejected_before_launch(edit):
    with pytest.raises(ValidationError):
        validate_operation_args("edit_design", {"workspace": "w", "cellview": "lib:cell:layout", "edits": [edit]})


def test_native_invocation_is_owned_and_persisted(settings, monkeypatch):
    calls = []
    async def fake(settings, output, callback):
        async def call(name, args):
            calls.append((name, args))
            if name == "execute_python":
                return response({"stdout": runtime.MARKER + '{"ok":true}'}, True)
            return response({"mode": "local"})
        return await callback(call, {})
    monkeypatch.setattr(runtime, "_with_official", fake)
    result = asyncio.run(runtime.run_operation(settings, "inspect_design", {"workspace": "workspace", "cellview": "lib:cell:schematic"}))
    assert [name for name, _ in calls] == ["start_local_session", "execute_python"]
    code = calls[1][1]["code"]
    assert repr(str(settings.state_dir)) in code
    assert "connect_session(" not in code
    evidence = Path(result["evidence_directory"])
    assert json.loads((evidence / "result.json").read_text())["ok"]
    assert json.loads((evidence / "request.json").read_text())["native_source_sha256"]
    assert result["physical_qualification"] is False


def test_external_state_dataset_and_raw_permission(settings, monkeypatch):
    settings.state_dir.mkdir()
    dataset = settings.state_dir / "result.ds"
    dataset.write_bytes(b"placeholder")
    async def fake(settings, output, callback):
        async def call(name, args):
            return response({"stdout": runtime.MARKER + '{"ok":true}'}, True)
        return await callback(call, {})
    monkeypatch.setattr(runtime, "_with_official", fake)
    assert asyncio.run(runtime.run_operation(settings, "read_dataset", {"path": str(dataset)}))["ok"]
    with pytest.raises(PermissionError):
        asyncio.run(runtime.run_operation(settings, "execute_python", {"code": "print(1)"}))
    opted_in = Settings(None, settings.project_root, settings.state_dir, allow_execute_python=True)
    assert asyncio.run(runtime.run_operation(opted_in, "execute_python", {"code": "print(1)"}))["ok"]


def test_path_escape_rejected_before_official(settings, tmp_path, monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("No native process should launch on a rejected path")
    monkeypatch.setattr(runtime, "_with_official", unexpected)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError):
        asyncio.run(runtime.run_operation(settings, "inspect_design", {"workspace": str(outside), "cellview": "lib:cell:schematic"}))


def test_worker_environment_retains_solver_settings(settings, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "do not copy")
    monkeypatch.setenv("OMP_NUM_THREADS", "18")
    monkeypatch.setenv("ADS_LICENSE_FILE", "27000@local-license-server")
    env = runtime.worker_environment(settings)
    assert "EXAMPLE_API_KEY" not in env
    assert env["OMP_NUM_THREADS"] == "18"
    assert env["ADS_LICENSE_FILE"] == "27000@local-license-server"
