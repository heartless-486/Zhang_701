"""Offline configuration checks; no ADS import or license checkout."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ads_mcp.config import Settings, doctor, load_settings, scoped_path, settings_from_dict

class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_config_paths_and_environment(self):
        path = self.root / "config.toml"
        path.write_text('[ads_mcp]\nproject_root="work"\nads_home="ads"\nmax_workers=2\n', encoding="utf-8")
        with patch.dict(os.environ, {"ADS_MCP_MAX_WORKERS": "3", "ADS_MCP_ALLOW_EXECUTE_PYTHON": "true"}, clear=True):
            settings = load_settings(path)
        self.assertEqual(settings.project_root, self.root / "work")
        self.assertEqual(settings.max_workers, 3)
        self.assertTrue(settings.allow_execute_python)
        self.assertEqual(settings_from_dict(settings.to_dict()), settings)

    def test_invalid_settings(self):
        for extra in ({"max_workers": 0}, {"max_workers": True}, {"tool_timeout_seconds": 0}, {"allow_execute_python": "false"}):
            with self.assertRaises(ValueError):
                Settings(None, self.root, self.root / "state", **extra)
        path = self.root / "config.toml"
        path.write_text('[ads_mcp]\nmax_wokers=4\n', encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "Unknown"):
            load_settings(path)

    def test_scoped_paths(self):
        (self.root / "public.txt").write_text("safe", encoding="utf-8")
        self.assertEqual(scoped_path(self.root, "public.txt"), self.root / "public.txt")
        self.assertEqual(scoped_path(self.root, "new/path", False), self.root / "new/path")
        for value in ("../escape", ".env", ".ssh/id_rsa", "some/credentials.json", "private.key"):
            with self.assertRaises(ValueError):
                scoped_path(self.root, value, False)
        with self.assertRaises(ValueError):
            scoped_path(self.root, self.root.parent / "other", False)

    def test_symlink_denied(self):
        (self.root / "real").mkdir()
        try:
            (self.root / "link").symlink_to(self.root / "real", target_is_directory=True)
        except OSError:
            self.skipTest("OS does not permit test symlinks")
        with self.assertRaisesRegex(ValueError, "Symlink|junction"):
            scoped_path(self.root, "link/new", False)

    def test_reparse_ancestor_is_rejected_before_canonicalization(self):
        import types
        directory = self.root / "junction"
        directory.mkdir()
        target = directory / "public.txt"
        target.write_text("safe", encoding="utf-8")
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            details = original(path, *args, **kwargs)
            if path == directory:
                return types.SimpleNamespace(st_mode=details.st_mode, st_file_attributes=0x400)
            return details
        with patch.object(Path, "lstat", lstat), patch.object(Path, "resolve", side_effect=AssertionError("Reparse must be rejected before resolve")):
            with self.assertRaisesRegex(ValueError, "symlinks or junctions"):
                scoped_path(self.root, target)

    @unittest.skipUnless(os.name == "nt", "Windows 8.3 paths only")
    def test_windows_short_aliases_share_scope_and_workspace_lock(self):
        import ctypes
        from ctypes import wintypes
        from ads_mcp.jobs import workspace_lock
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_short = kernel.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        def short(path):
            size = get_short(str(path), None, 0)
            if not size:
                self.skipTest("GetShortPathNameW is unavailable for this temporary directory")
            buffer = ctypes.create_unicode_buffer(size)
            written = get_short(str(path), buffer, size)
            if not written or written >= size:
                self.fail("GetShortPathNameW could not return the allocated short path")
            return Path(buffer.value)
        project = self.root / "project directory with long name"
        project.mkdir()
        workspace = project / "workspace directory with long name"
        workspace.mkdir()
        artifact = workspace / "public artifact.txt"
        artifact.write_text("source", encoding="utf-8")
        alias_root, alias_workspace, alias_artifact = short(project), short(workspace), short(artifact)
        if os.path.normcase(str(alias_root)) == os.path.normcase(str(project)):
            self.skipTest("The temporary volume does not generate Windows 8.3 aliases")
        self.assertEqual(scoped_path(project, alias_artifact), artifact.resolve())
        self.assertEqual(scoped_path(alias_root, artifact), artifact.resolve())
        self.assertEqual(scoped_path(alias_root, "new/artifact.txt", False), project / "new/artifact.txt")
        state = self.root / "shared state"
        long_settings = Settings(None, project, state)
        alias_settings = Settings(None, alias_root, state)
        with workspace_lock(long_settings, workspace):
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                with workspace_lock(alias_settings, alias_workspace):
                    self.fail("Aliases must not acquire two independent locks for one workspace")
        outside = self.root / "outside long directory"
        outside.mkdir()
        outside_file = outside / "outside artifact.txt"
        outside_file.write_text("outside", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inside project_root"):
            scoped_path(alias_root, short(outside_file))
        with self.assertRaisesRegex(ValueError, "Parent path traversal"):
            scoped_path(alias_root, "../outside artifact.txt", False)
        private = project / "credentials"
        private.mkdir()
        with self.assertRaisesRegex(ValueError, "Credential/private"):
            scoped_path(alias_root, short(private))

    def test_doctor_does_not_check_out_license(self):
        home = self.root / "ADS"
        (home / "bin").mkdir(parents=True)
        (home / "bin" / "ads-mcp.exe").write_bytes(b"")
        with patch("ads_mcp.config.shutil.which", return_value=None):
            result = doctor(Settings(home, self.root, self.root / "state"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["license_status"], "not_probed")
        self.assertFalse(result["simulation_started"])

    def test_explicit_home_is_not_replaced_by_ambient_ads_install(self):
        path = self.root / "ads-mcp.toml"
        path.write_text('[ads_mcp]\nads_home="ADS2027"\nproject_root="projects"\n', encoding="utf-8")
        with patch.dict(os.environ, {"ADS_HOME": str(self.root / "ADS2026"), "HPEESOF_DIR": str(self.root / "OldADS")}, clear=True):
            self.assertEqual(load_settings(path).ads_home, self.root / "ADS2027")
        with patch.dict(os.environ, {"ADS_MCP_ADS_HOME": str(self.root / "ExplicitOverride")}, clear=True):
            self.assertEqual(load_settings(path).ads_home, self.root / "ExplicitOverride")
        path.write_text('[ads_mcp]\nproject_root="projects"\n', encoding="utf-8")
        with patch.dict(os.environ, {"HPEESOF_DIR": str(self.root / "Fallback")}, clear=True):
            self.assertEqual(load_settings(path).ads_home, self.root / "Fallback")

    def test_cwd_default_config_and_boolean_integer_rejection(self):
        path = self.root / "ads-mcp.toml"
        path.write_text('[ads_mcp]\nproject_root="auto_projects"\n', encoding="utf-8")
        with patch("os.getcwd", return_value=str(self.root)), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_settings().project_root, self.root / "auto_projects")
        path.write_text('[ads_mcp]\nmax_workers=true\n', encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            load_settings(path)
