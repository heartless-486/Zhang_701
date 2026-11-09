"""Portable configuration and path admission; never starts ADS."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tomllib
from typing import Any

_SENSITIVE = {".git", ".ssh", ".aws", ".azure", ".kube", "credentials", "credentials.json",
              "id_rsa", "id_ed25519", "api.txt", "_api.txt", "token", "tokens.json"}

def _reparse(path: Path) -> bool:
    """Recognize links and Windows junctions, including Python 3.11."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)

def _plain_directory_chain(path: Path) -> None:
    if any(_reparse(candidate) for candidate in (path, *path.parents)):
        raise ValueError("Paths must not traverse symlinks or junctions")

def scoped_path(root: Path | str, value: Path | str, must_exist: bool = True) -> Path:
    """Reject escaping, link traversal and private paths. Not an OS sandbox.

    Inspect original spellings before resolving: canonicalization must not hide
    symlinks/junctions. Compare canonical spellings afterwards so Windows 8.3
    names and their long-name equivalents identify the same admitted path.
    """
    raw_root = Path(root)
    raw = Path(value)
    if ".." in raw_root.parts or ".." in raw.parts:
        raise ValueError("Parent path traversal is not allowed")
    raw_root = raw_root.absolute()
    candidate = (raw if raw.is_absolute() else raw_root / raw).absolute()

    def reject_private(parts):
        for part in parts:
            lower = part.lower()
            if lower in _SENSITIVE or lower.startswith(".env") or lower.endswith((".pem", ".key", ".p12", ".pfx")):
                raise ValueError("Credential/private configuration paths are not project artifacts")

    # All existing ancestors are checked before resolve() can erase reparse
    # evidence. This also applies when an absolute candidate uses an 8.3 alias.
    for original in (raw_root, candidate):
        reject_private(original.parts)
        _plain_directory_chain(original)
    canonical_root = raw_root.resolve(strict=True)
    resolved = candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(canonical_root):
        raise ValueError("Resolved path must be inside project_root")
    # A short alias may hide a private long filename such as CREDEN~1.JSON.
    reject_private(canonical_root.parts)
    reject_private(resolved.relative_to(canonical_root).parts)
    return resolved

@dataclass(frozen=True)
class Settings:
    ads_home: Path | None
    project_root: Path
    state_dir: Path
    max_workers: int = 1
    tool_timeout_seconds: int = 120
    allow_execute_python: bool = False

    def __post_init__(self):
        if isinstance(self.max_workers, bool) or not isinstance(self.max_workers, int) or not 1 <= self.max_workers <= 64:
            raise ValueError("max_workers must be an integer between 1 and 64")
        if isinstance(self.tool_timeout_seconds, bool) or not isinstance(self.tool_timeout_seconds, int) or not 1 <= self.tool_timeout_seconds <= 86400:
            raise ValueError("tool_timeout_seconds must be an integer between 1 and 86400")
        if not isinstance(self.allow_execute_python, bool):
            raise ValueError("allow_execute_python must be boolean")
        for name in ("project_root", "state_dir", "ads_home"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value).expanduser().absolute())
        _plain_directory_chain(self.project_root)
        _plain_directory_chain(self.state_dir)

    @property
    def official_command(self) -> Path | None:
        if self.ads_home is None:
            return None
        for name in ("ads-mcp.exe", "ads-mcp"):
            candidate = self.ads_home / "bin" / name
            if candidate.is_file():
                return candidate
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"ads_home": str(self.ads_home) if self.ads_home else None,
                "project_root": str(self.project_root), "state_dir": str(self.state_dir),
                "max_workers": self.max_workers, "tool_timeout_seconds": self.tool_timeout_seconds,
                "allow_execute_python": self.allow_execute_python}

def settings_from_dict(values: dict[str, Any]) -> Settings:
    return Settings(**values)

def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
        return value.lower() in {"true", "1"}
    raise ValueError("Boolean settings accept true/false or 1/0")

def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Integer settings require an integer or decimal string")
    return int(value)

def load_settings(config_path: Path | str | None = None) -> Settings:
    selected = config_path or os.environ.get("ADS_MCP_CONFIG")
    if not selected and (Path.cwd() / "ads-mcp.toml").is_file():
        selected = Path.cwd() / "ads-mcp.toml"
    values: dict[str, Any] = {}
    base = Path.cwd()
    if selected:
        source = Path(selected).expanduser().resolve(strict=True)
        with source.open("rb") as stream:
            values = dict(tomllib.load(stream).get("ads_mcp", {}))
        base = source.parent
    allowed = {"ads_home", "project_root", "state_dir", "max_workers", "tool_timeout_seconds", "allow_execute_python"}
    if set(values) - allowed:
        raise ValueError("Unknown ADS MCP settings: " + ", ".join(sorted(set(values) - allowed)))
    environment = {"ads_home": os.environ.get("ADS_MCP_ADS_HOME") or (
                       (os.environ.get("ADS_HOME") or os.environ.get("HPEESOF_DIR")) if not values.get("ads_home") else None),
                   "project_root": os.environ.get("ADS_MCP_PROJECT_ROOT"),
                   "state_dir": os.environ.get("ADS_MCP_STATE_DIR"),
                   "max_workers": os.environ.get("ADS_MCP_MAX_WORKERS"),
                   "tool_timeout_seconds": os.environ.get("ADS_MCP_TOOL_TIMEOUT_SECONDS"),
                   "allow_execute_python": os.environ.get("ADS_MCP_ALLOW_EXECUTE_PYTHON")}
    values.update({key: value for key, value in environment.items() if value not in (None, "")})
    def path(value: Any) -> Path:
        result = Path(value).expanduser()
        return (result if result.is_absolute() else base / result).absolute()
    root = path(values.get("project_root", "projects"))
    return Settings(ads_home=path(values["ads_home"]) if values.get("ads_home") else None,
                    project_root=root, state_dir=path(values["state_dir"]) if values.get("state_dir") else root / ".ads-mcp",
                    max_workers=_integer(values.get("max_workers", 1)),
                    tool_timeout_seconds=_integer(values.get("tool_timeout_seconds", 120)),
                    allow_execute_python=_boolean(values.get("allow_execute_python", False)))

def doctor(settings: Settings) -> dict[str, Any]:
    """Read installation/resources only. No license checkout or ADS API import."""
    hardware: dict[str, Any] = {"logical_cpus": os.cpu_count(), "physical_cores": None,
                               "available_memory_bytes": None, "total_memory_bytes": None}
    try:
        import psutil
        memory = psutil.virtual_memory()
        hardware.update(physical_cores=psutil.cpu_count(logical=False),
                        available_memory_bytes=memory.available, total_memory_bytes=memory.total,
                        cpu_percent=psutil.cpu_percent(interval=0.1))
    except ImportError:
        hardware["resource_detail"] = "Install optional psutil for physical cores, RAM and CPU load"
    gpu = shutil.which("nvidia-smi")
    hardware["gpu"] = {"status": "not_detected"}
    if gpu:
        try:
            result = subprocess.run([gpu, "--query-gpu=name,memory.total,memory.free,utilization.gpu", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=3,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            hardware["gpu"] = {"status": "detected" if result.returncode == 0 else "query_failed",
                               "columns": ["name", "memory_total_MiB", "memory_free_MiB", "utilization_percent"],
                               "rows": [row.split(", ") for row in result.stdout[:4096].splitlines()]}
        except (OSError, subprocess.TimeoutExpired):
            hardware["gpu"] = {"status": "query_failed_or_timed_out"}
    return {"ok": settings.official_command is not None,
            "ads_home": str(settings.ads_home) if settings.ads_home else None,
            "official_mcp": str(settings.official_command) if settings.official_command else None,
            "python": sys.version.split()[0], "project_root": str(settings.project_root),
            "project_root_exists": settings.project_root.is_dir(), "state_dir": str(settings.state_dir),
            "max_workers": settings.max_workers, "tool_timeout_seconds": settings.tool_timeout_seconds,
            "allow_execute_python": settings.allow_execute_python, "hardware": hardware,
            "license_status": "not_probed", "solver_threads": "not_probed", "simulation_started": False,
            "note": "Executable presence is not native capability, license, PDK or RF qualification; GPU availability does not imply solver GPU support."}
