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
        self.root = Path(self.temp.name)

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
