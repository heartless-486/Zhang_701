"""Private worker: one process, one native attempt, no automatic retries."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from .config import settings_from_dict
from .jobs import atomic_json, file_lock, utc_now

# The Windows handle is deliberately kept until process exit. Closing the last
# handle to this Job Object kills any owned solver descendants still running.
_WINDOWS_JOB_HANDLE = None

def _contain_process_tree() -> str:
    """Assign this owned worker to a private Windows kill-on-close job."""
    global _WINDOWS_JOB_HANDLE
    if os.name != "nt":
        return "owned_posix_process_group; detached_children_not_guaranteed"
    import ctypes
    from ctypes import wintypes
    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                    ("active_process_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority_class", wintypes.DWORD), ("scheduling_class", wintypes.DWORD)]
    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("read_operations", "write_operations", "other_operations", "read_bytes", "write_bytes", "other_bytes")]
    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IOCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "Could not create owned-worker Job Object; ADS not started")
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise OSError(error, "Could not configure owned-worker Job Object; ADS not started")
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise OSError(error, "Could not contain owned worker; ADS not started")
    _WINDOWS_JOB_HANDLE = handle
    return "windows_job_object"

def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m ads_mcp.job_worker SPEC.json")
    spec_path = Path(sys.argv[1]).resolve(strict=True)
    specification = json.loads(spec_path.read_text(encoding="utf-8"))
    settings = settings_from_dict(specification["settings"])
    job_id = specification["job_id"]
    directory = spec_path.parent
    if directory != (settings.state_dir / "jobs" / job_id).resolve():
        raise ValueError("Worker specification is outside its exact job directory")
    if specification["operation"] not in {"simulate_design", "run_momentum"}:
        raise ValueError("Unsupported background native operation")
    encoded = json.dumps({"operation": specification["operation"], "arguments": specification["arguments"]},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != specification["input_sha256"]:
        raise ValueError("Job input digest mismatch")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        with file_lock(settings.state_dir / "locks" / f"worker-{int(specification['capacity_slot'])}.lock"):
            # Immutable attempt identities cannot be replayed, even by accidentally
            # invoking this internal worker a second time on its old spec.
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["status"] != "starting":
                raise ValueError("This job attempt has already started; do not replay its specification")
            try:
                containment = _contain_process_tree()
                manifest.update(status="running", started_at=utc_now(), worker_pid=os.getpid(),
                                process_tree_containment=containment,
                                native_execution="dispatching; inspect native receipt")
                atomic_json(manifest_path, manifest)
                from .runtime import run_operation
                result = asyncio.run(run_operation(settings, specification["operation"],
                                                   specification["arguments"], output_dir=directory))
                atomic_json(directory / "result.json", result)
                failed = isinstance(result, dict) and (result.get("ok") is False or result.get("isError") is True)
                manifest.update(status="failed" if failed else "completed", ended_at=utc_now(),
                                native_execution="response_saved",
                                result_sha256=hashlib.sha256((directory / "result.json").read_bytes()).hexdigest())
                atomic_json(manifest_path, manifest)
                return 1 if failed else 0
            except BaseException as exc:
                manifest.update(status="failed", ended_at=utc_now(),
                                error=f"{type(exc).__name__}: {exc}"[:4096],
                                native_execution="failed_or_unknown; inspect retained logs", rf_qualified=False)
                atomic_json(manifest_path, manifest)
                raise
    except RuntimeError as exc:
        if manifest["status"] == "starting":
            manifest.update(status="rejected_capacity", ended_at=utc_now(), error=str(exc),
                            native_execution="not_started", rf_qualified=False)
            atomic_json(manifest_path, manifest)
        raise

if __name__ == "__main__":
    raise SystemExit(main())
