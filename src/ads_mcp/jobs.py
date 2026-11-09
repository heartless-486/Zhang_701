"""Durable single-attempt jobs. Locks never bypass ADS/OA locks."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Iterator
from uuid import uuid4
from .config import Settings, scoped_path, _plain_directory_chain, _reparse

_TERMINAL = {"completed", "failed", "failed_launch", "cancelled", "interrupted", "rejected_capacity"}
_OPERATIONS = {"simulate_design", "run_momentum"}

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)

@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Nonblocking process-held lock; OS releases it if the holder dies."""
    _plain_directory_chain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("ADS MCP resource is already in use") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

@contextmanager
def workspace_lock(settings: Settings, workspace: str | Path) -> Iterator[None]:
    """Runtime supplies the actual workspace path; all package users share this lock."""
    target = scoped_path(settings.project_root, workspace, must_exist=False)
    identity = os.path.normcase(str(target))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    with file_lock(settings.state_dir / "locks" / ("workspace-" + digest + ".lock")):
        yield

def _tail(path: Path, maximum: int = 16_384) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - maximum))
        return stream.read(maximum).decode("utf-8", errors="replace")

class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.directory = settings.state_dir / "jobs"
        _plain_directory_chain(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, subprocess.Popen] = {}

    def _slot_path(self, slot: int) -> Path:
        return self.settings.state_dir / "locks" / f"worker-{slot}.lock"

    def _available_slot(self, count: int) -> int | None:
        for slot in range(count):
            try:
                with file_lock(self._slot_path(slot)):
                    return slot
            except RuntimeError:
                continue
        return None

    def _capacity_policy(self) -> None:
        policy = self.directory / "capacity.json"
        if policy.exists():
            old_count = int(json.loads(policy.read_text(encoding="utf-8"))["max_workers"])
            if old_count == self.settings.max_workers:
                return
            for slot in range(max(old_count, self.settings.max_workers)):
                try:
                    with file_lock(self._slot_path(slot)):
                        pass
                except RuntimeError as exc:
                    raise RuntimeError("All servers must use the same max_workers while jobs are active") from exc
        atomic_json(policy, {"max_workers": self.settings.max_workers, "updated_at": utc_now()})

    def submit(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation not in _OPERATIONS:
            raise ValueError("Only simulate_design and run_momentum support background jobs")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        from .models import validate_operation_args
        arguments = validate_operation_args(operation, arguments)
        encoded = json.dumps({"operation": operation, "arguments": arguments}, sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 1_048_576:
            raise ValueError("Job input exceeds 1 MiB")
        with file_lock(self.settings.state_dir / "locks" / "submission.lock"):
            self._capacity_policy()
            slot = self._available_slot(self.settings.max_workers)
            if slot is None:
                raise RuntimeError("Native capacity is full; poll existing jobs before submitting again")
            job_id = uuid4().hex
            directory = self.directory / job_id
            directory.mkdir()
            manifest = {"job_id": job_id, "operation": operation, "status": "starting",
                        "created_at": utc_now(), "input_sha256": hashlib.sha256(encoded).hexdigest(),
                        "attempt_count": 1, "automatic_retries": 0,
                        "native_execution": "not_yet_observed", "rf_qualified": False,
                        "output_directory": str(directory), "capacity_slot": slot}
            atomic_json(directory / "manifest.json", manifest)
            specification = directory / "spec.json"
            atomic_json(specification, {"settings": self.settings.to_dict(), "operation": operation,
                                       "arguments": arguments, "job_id": job_id,
                                       "capacity_slot": slot, "input_sha256": manifest["input_sha256"]})
            environment = os.environ.copy()
            environment.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
            # Deliberately do not impose OMP/MKL thread limits on external solvers.
            try:
                with (directory / "worker.stdout.log").open("ab") as stdout, (directory / "worker.stderr.log").open("ab") as stderr:
                    process = subprocess.Popen([sys.executable, "-m", "ads_mcp.job_worker", str(specification)],
                                               cwd=str(directory), env=environment, stdin=subprocess.DEVNULL,
                                               stdout=stdout, stderr=stderr,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                               start_new_session=os.name != "nt")
                self._processes[job_id] = process
            except OSError as exc:
                manifest.update(status="failed_launch", ended_at=utc_now(), error=f"{type(exc).__name__}: {exc}")
                atomic_json(directory / "manifest.json", manifest)
                return self.status(job_id)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                if current["status"] != "starting" or process.poll() is not None:
                    break
                time.sleep(0.025)
            return self.status(job_id)

    def _job_directory(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or re.fullmatch(r"[a-f0-9]{32}", job_id) is None:
            raise ValueError("Invalid job_id")
        directory = self.directory / job_id
        if _reparse(directory) or not (directory / "manifest.json").is_file():
            raise ValueError("Unknown job_id")
        return directory

    def status(self, job_id: str) -> dict[str, Any]:
        directory = self._job_directory(job_id)
        manifest_path = directory / "manifest.json"
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        process = self._processes.get(job_id)
        if process is not None:
            exit_code = process.poll()
            if exit_code is not None and value["status"] not in _TERMINAL:
                value.update(status="interrupted", worker_exit_code=exit_code, ended_at=utc_now(),
                             native_execution="unknown_after_worker_exit")
                atomic_json(manifest_path, value)
        value["cancellable_by_this_server"] = process is not None and process.poll() is None
        if value["status"] not in _TERMINAL and process is None:
            value["worker_ownership"] = "another_server_or_previous_session; saved PID is not cancellation authority"
        value["stdout_tail"] = _tail(directory / "worker.stdout.log")
        value["stderr_tail"] = _tail(directory / "worker.stderr.log")
        result_path = directory / "result.json"
        if result_path.is_file():
            value["result_path"] = str(result_path)
            if result_path.stat().st_size <= 65_536:
                value["result"] = json.loads(result_path.read_text(encoding="utf-8"))
        return value

    def list_jobs(self) -> list[dict[str, Any]]:
        identifiers = sorted((path.name for path in self.directory.iterdir()
                              if path.is_dir() and re.fullmatch(r"[a-f0-9]{32}", path.name)), reverse=True)
        return [self.status(job_id) for job_id in identifiers[:100]]

    def cancel(self, job_id: str) -> dict[str, Any]:
        directory = self._job_directory(job_id)
        process = self._processes.get(job_id)
        current = self.status(job_id)
        if current["status"] in _TERMINAL:
            return current
        if process is None:
            raise RuntimeError("Only a worker launched by this server can be cancelled; saved PIDs are not trusted")
        if process.poll() is not None:
            return self.status(job_id)
        atomic_json(directory / "cancel.json", {"requested_at": utc_now(), "job_id": job_id,
                                               "note": "Owned process-tree termination requested; retain partial outputs"})
        if os.name == "nt":
            # Popen owns a stable Windows process handle. Do not resolve a saved
            # PID with taskkill: it could have been reused after worker exit.
            if current["status"] != "starting" and current.get("process_tree_containment") != "windows_job_object":
                raise RuntimeError("Worker containment was not verified; refuse PID-based cancellation")
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.TerminateProcess.restype = wintypes.BOOL
            if not kernel.TerminateProcess(int(process._handle), 0xC000013A):
                if process.poll() is None:
                    raise OSError(ctypes.get_last_error(), "Owned-worker termination failed")
        else:
            import signal
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return {**self.status(job_id), "cancel_requested": True,
                    "note": "Termination requested; worker remains live. Partial results are unqualified."}
        current = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if current["status"] not in _TERMINAL:
            current.update(status="cancelled", ended_at=utc_now(), worker_exit_code=process.returncode,
                           native_execution="interrupted; child_solver_completion_unknown", rf_qualified=False)
            atomic_json(directory / "manifest.json", current)
        return self.status(job_id)
