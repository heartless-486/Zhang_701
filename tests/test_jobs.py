"""Lifecycle tests use fake workers and OS leases, never ADS."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from ads_mcp.config import Settings
from ads_mcp.jobs import JobManager, atomic_json, file_lock, workspace_lock
from ads_mcp import job_worker

class FakeProcess:
    def __init__(self, command, **kwargs):
        self.pid, self.returncode = 987654321, None
        manifest = Path(command[-1]).parent / "manifest.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value.update(status="running", worker_pid=self.pid)
        atomic_json(manifest, value)
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        self.returncode = -15
        return self.returncode

class JobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(None, self.root, self.root / "state")
        self.manager = JobManager(self.settings)

    def submit(self):
        with patch("ads_mcp.jobs.subprocess.Popen", FakeProcess):
            return self.manager.submit("simulate_design", {"workspace": "work", "cellview": "lib:cell:schematic"})

    def test_input_receipt_and_unqualified_state(self):
        result = self.submit()
        specification = json.loads((Path(result["output_directory"]) / "spec.json").read_text(encoding="utf-8"))
        value = {"operation": specification["operation"], "arguments": specification["arguments"]}
        digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(result["input_sha256"], digest)
        self.assertEqual(result["attempt_count"], 1)
        self.assertEqual(result["automatic_retries"], 0)
        self.assertFalse(result["rf_qualified"])
        self.assertTrue(result["cancellable_by_this_server"])
        self.assertEqual(len(self.manager.list_jobs()), 1)

    def test_full_capacity_rejects_before_launch(self):
        with file_lock(self.settings.state_dir / "locks" / "worker-0.lock"):
            with patch("ads_mcp.jobs.subprocess.Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, "capacity"):
                    self.manager.submit("simulate_design", {"workspace": "work", "cellview": "lib:cell:schematic"})
                launch.assert_not_called()

    def test_workspace_lock_conflicts_and_releases(self):
        (self.root / "work").mkdir()
        with workspace_lock(self.settings, "work"):
            with self.assertRaises(RuntimeError):
                with workspace_lock(self.settings, self.root / "work"):
                    self.fail("must conflict")
        with workspace_lock(self.settings, "work"):
            pass
        with workspace_lock(self.settings, "new_workspace"):
            pass

    def test_foreign_saved_pid_is_not_cancellation_authority(self):
        result = self.submit()
        other = JobManager(self.settings)
        with patch("ads_mcp.jobs.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "Only a worker"):
                other.cancel(result["job_id"])
            run.assert_not_called()
        self.assertFalse(other.status(result["job_id"])["cancellable_by_this_server"])

    def test_dead_worker_is_interrupted_with_bounded_logs(self):
        result = self.submit()
        (Path(result["output_directory"]) / "worker.stdout.log").write_bytes(b"x" * 100_000)
        self.manager._processes[result["job_id"]].returncode = 7
        status = self.manager.status(result["job_id"])
        self.assertEqual(status["status"], "interrupted")
        self.assertLessEqual(len(status["stdout_tail"]), 16_384)
        self.assertNotIn("result", status)

    def test_path_and_operation_admission(self):
        with self.assertRaises(ValueError):
            self.manager.submit("execute_python", {"code": "pass"})
        with self.assertRaises(ValueError):
            self.manager.status("../elsewhere")

    def run_worker(self, result, native):
        module = types.ModuleType("ads_mcp.runtime")
        module.run_operation = native
        specification = Path(result["output_directory"]) / "spec.json"
        manifest_path = specification.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "starting"
        atomic_json(manifest_path, manifest)
        with patch.dict(sys.modules, {"ads_mcp.runtime": module}), patch.object(sys, "argv", ["worker", str(specification)]), patch.object(job_worker, "_contain_process_tree", return_value="test_no_native_children"):
            return job_worker.main()

    def test_worker_preserves_response_before_completed_receipt(self):
        result = self.submit()
        async def native(*args, **kwargs):
            return {"ok": True, "native_value": 37}
        self.assertEqual(self.run_worker(result, native), 0)
        directory = Path(result["output_directory"])
        receipt = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["result_sha256"], hashlib.sha256((directory / "result.json").read_bytes()).hexdigest())
        self.assertFalse(receipt["rf_qualified"])

    def test_worker_failure_preserves_cost_and_does_not_retry(self):
        result = self.submit()
        calls = []
        async def native(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("native failed")
        with self.assertRaisesRegex(RuntimeError, "native failed"):
            self.run_worker(result, native)
        directory = Path(result["output_directory"])
        receipt = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(calls, [1])
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["attempt_count"], 1)
        self.assertFalse((directory / "result.json").exists())

    def test_bad_arguments_do_not_start_a_worker_or_allocate_attempt(self):
        with patch("ads_mcp.jobs.subprocess.Popen") as launch:
            with self.assertRaises(ValueError):
                self.manager.submit("simulate_design", {"workspace": "work", "shell": "unsafe"})
            launch.assert_not_called()
        self.assertEqual(self.manager.list_jobs(), [])

    def test_capacity_cannot_change_during_active_lease(self):
        self.manager._capacity_policy()
        other = JobManager(Settings(None, self.root, self.root / "state", max_workers=2))
        with file_lock(self.settings.state_dir / "locks" / "worker-0.lock"):
            with self.assertRaisesRegex(RuntimeError, "same max_workers"):
                other._capacity_policy()
        other._capacity_policy()
        self.assertEqual(json.loads((other.directory / "capacity.json").read_text())["max_workers"], 2)

    def test_worker_spec_cannot_be_replayed_after_completion(self):
        result = self.submit()
        async def native(*args, **kwargs):
            return {"ok": True}
        self.run_worker(result, native)
        directory = Path(result["output_directory"])
        specification = directory / "spec.json"
        with patch.object(sys, "argv", ["worker", str(specification)]), patch.object(job_worker, "_contain_process_tree") as containment:
            with self.assertRaisesRegex(ValueError, "already started"):
                job_worker.main()
            containment.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object containment")
    def test_windows_cancel_uses_owned_handle_and_kills_descendant(self):
        import os
        import subprocess
        import time
        import psutil
        result = self.submit()
        directory = Path(result["output_directory"])
        script = (
            "from ads_mcp.job_worker import _contain_process_tree; "
            "import subprocess,sys,time; "
            "print(_contain_process_tree(),flush=True); "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
            "creationflags=subprocess.CREATE_NO_WINDOW); "
            "print(child.pid,flush=True); time.sleep(30)"
        )
        process = subprocess.Popen([sys.executable, "-B", "-u", "-c", script], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: process.poll() is None and process.terminate())
        self.assertEqual(process.stdout.readline().strip(), "windows_job_object")
        child_pid = int(process.stdout.readline())
        descendant = psutil.Process(child_pid)
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["process_tree_containment"] = "windows_job_object"
        atomic_json(manifest_path, manifest)
        self.manager._processes[result["job_id"]] = process
        cancelled = self.manager.cancel(result["job_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        descendant.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
