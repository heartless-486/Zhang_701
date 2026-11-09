"""Protocol deployment probes must not wait forever for an unresponsive server."""
import asyncio
import importlib.util
from pathlib import Path
import pytest

def test_protocol_probe_timeout_cancels_unresponsive_check(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_mcp.py"
    spec = importlib.util.spec_from_file_location("deployment_protocol_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cancelled = []
    async def stalled(config):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)
    monkeypatch.setattr(module, "_check", stalled)
    with pytest.raises(TimeoutError):
        asyncio.run(module.check(tmp_path / "config.toml", timeout_seconds=0.01))
    assert cancelled == [True]
