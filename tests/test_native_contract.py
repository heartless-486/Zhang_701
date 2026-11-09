"""Offline behavior tests with a minimal ADS API double; no RF solver is run."""
import hashlib
import inspect
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from ads_mcp.native import execute


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspaces" / "sample"
        self.workspace.mkdir(parents=True)
        (self.workspace / "lib.defs").write_text("DEFINE owned ./owned\n", encoding="utf-8")
        self.library = self.workspace / "owned"
        self.library.mkdir()
        self.output = self.root / "runs" / "test"
        self.design = types.SimpleNamespace(instances=[], nets=[], is_schematic=True)
        self.design.save_design = lambda: None
        self.design.generate_netlist = lambda: "R:R1 1 0 R=50\n"
        self.design.find_instance = lambda name: None
        self.design.get_instance = lambda name: (_ for _ in ()).throw(ValueError("No such instance"))
        self.de = types.ModuleType("keysight.ads.de")
        self.de.workspace_is_open = lambda: True
        self.de.active_workspace = lambda: types.SimpleNamespace(path=self.workspace)
        self.de.get_open_library = lambda name: types.SimpleNamespace(path=self.library)
        self.db = types.ModuleType("keysight.ads.de.db_uu")
        self.db.open_design = lambda *args: self.design
        self.db.LayerId = lambda *args: args
        self.de.db_uu = self.db
        self.experimental = types.ModuleType("keysight.ads.de.experimental")
        self.experimental.DesignBuilder = lambda design: types.SimpleNamespace()
        keysight = types.ModuleType("keysight")
        ads = types.ModuleType("keysight.ads")
        ads.de = self.de
        keysight.ads = ads
        self.modules = {"keysight": keysight, "keysight.ads": ads,
                        "keysight.ads.de": self.de, "keysight.ads.de.db_uu": self.db,
                        "keysight.ads.de.experimental": self.experimental}

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, operation, **args):
        with patch.dict(sys.modules, self.modules):
            return execute(operation, {"workspace": str(self.workspace), "cellview": "owned:dut:schematic", **args},
                           str(self.root), str(self.output))

    def test_source_is_self_contained(self):
        namespace = {}
        exec(inspect.getsource(execute), namespace)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            namespace["execute"]("shell", {}, str(self.root), str(self.output))

    def test_relative_state_output_is_rejected_before_ads_import(self):
        with self.assertRaisesRegex(ValueError, "output_dir"):
            execute("inspect_design", {}, str(self.root), "relative_state")

    def test_runtime_state_may_be_separate_from_project(self):
        with tempfile.TemporaryDirectory() as state:
            with patch.dict(sys.modules, self.modules):
                result = execute("inspect_design", {"workspace": str(self.workspace), "cellview": "owned:dut:schematic"}, str(self.root), str(Path(state).resolve() / "operation"), str(Path(state).resolve()))
            self.assertTrue(result["ok"])

    def reparse_attribute(self, flagged):
        """Model a Windows reparse bit without creating an actual junction."""
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            details = original(path, *args, **kwargs)
            if path == flagged:
                return types.SimpleNamespace(st_mode=details.st_mode, st_file_attributes=0x400)
            return details
        return patch.object(Path, "lstat", lstat)

    def test_edit_rejects_windows_reparse_attribute_before_copy(self):
        junction = self.library / "nested_junction"
        junction.mkdir()
        with self.reparse_attribute(junction), patch("shutil.copytree") as copy:
            with self.assertRaisesRegex(ValueError, "symbolic links or junctions"):
                self.call("edit_design", edits=[{"action": "set_parameters", "name": "R1", "parameters": {"R": "50 Ohm"}}])
            copy.assert_not_called()

    def test_dataset_rejects_windows_reparse_attribute_before_open(self):
        directory = self.root / "datasets"
        directory.mkdir()
        path = directory / "saved.ds"
        path.write_bytes(b"source dataset double")
        dataset = types.ModuleType("keysight.ads.dataset")
        dataset.open = unittest.mock.Mock(side_effect=AssertionError("must not open a reparse source"))
        self.modules["keysight.ads"].dataset = dataset
        with patch.dict(sys.modules, self.modules), self.reparse_attribute(directory):
            with self.assertRaisesRegex(ValueError, "symbolic links or junctions"):
                execute("read_dataset", {"path": str(path)}, str(self.root), str(self.output))
        dataset.open.assert_not_called()

    def test_locked_workspace_is_preserved(self):
        lock = self.library / "design.oa.lck"
        lock.write_text("owner", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ADS lock present"):
            self.call("inspect_design")
        self.assertEqual(lock.read_text(), "owner")

    def test_vendor_library_cannot_be_targeted(self):
        self.de.get_open_library = lambda name: types.SimpleNamespace(path=self.root.parent)
        with self.assertRaisesRegex(ValueError, "vendor libraries"):
            self.call("inspect_design")

    def test_netlist_has_hash_bound_source(self):
        result = self.call("generate_netlist")
        path = Path(result["netlist"]["path"])
        self.assertEqual(result["netlist"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result["qualification"], "native_netlist_only")

    def test_unknown_edit_is_rejected_without_mutating_design(self):
        with self.assertRaisesRegex(ValueError, "Unsupported edit"):
            self.call("edit_design", edits=[{"action": "delete_everything"}])
        self.assertEqual((self.output / "workspace_before" / "lib.defs").read_bytes(),
                         (self.workspace / "lib.defs").read_bytes())

    def test_late_native_edit_error_reports_partial_saved_state(self):
        wires = []
        self.design.add_wire = lambda points: wires.append(points)
        edits = [{"action": "add_wire", "points": [[0, 0], [1, 0]]},
                 {"action": "set_parameters", "name": "missing", "parameters": {"R": "50 Ohm"}}]
        result = self.call("edit_design", edits=edits)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed_index"], 1)
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(len(wires), 1)
        self.assertTrue(Path(result["snapshot"]["path"]).is_dir())
        self.assertFalse(result["automatic_retry"])

    def test_missing_momentum_binding_never_launches_solver(self):
        source = self.root / "preprocessed"
        source.mkdir()
        for name in ("proj_a", "proj.ltd", "proj.plan", "proj.pin", "proj.prt", "proj.opt", "proj.sti", "proj.cfg"):
            (source / name).write_text("input", encoding="utf-8")
        with patch("subprocess.Popen") as launch:
            with self.assertRaisesRegex(ValueError, "input_hashes"):
                self.call("run_momentum", source_dir=str(source))
        launch.assert_not_called()

    def test_existing_momentum_output_is_not_recomputed(self):
        source = self.root / "preprocessed"
        source.mkdir()
        for name in ("proj_a", "proj.ltd", "proj.plan", "proj.pin", "proj.prt", "proj.opt", "proj.sti", "proj.cfg", "proj.sio"):
            (source / name).write_text("input", encoding="utf-8")
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
        with patch("subprocess.Popen") as launch:
            with self.assertRaisesRegex(ValueError, "recovered, not rerun"):
                self.call("run_momentum", source_dir=str(source), input_hashes=hashes)
        launch.assert_not_called()


    def test_momentum_runs_fixed_command_on_hash_bound_copy(self):
        source = self.root / "preprocessed"
        source.mkdir()
        names = ("proj_a", "proj.ltd", "proj.plan", "proj.pin", "proj.prt", "proj.opt", "proj.sti", "proj.cfg")
        for name in names:
            (source / name).write_text("physics;", encoding="utf-8")
        (source / "proj.prt").write_text('<root><port_list><port id="1"><name>P1</name><impedance><real>50</real><imag>0</imag></impedance></port></port_list></root>', encoding="utf-8")
        (source / "proj.opt").write_text('physics; DS_DIR "old"; DS_NAME "old"; logFile "old.log";', encoding="utf-8")
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
        home = self.root / "ads"
        (home / "bin").mkdir(parents=True)
        import os
        wrapper = home / "bin" / ("adsMomWrapper.bat" if os.name == "nt" else "adsMomWrapper")
        dump = home / "bin" / ("siodump.exe" if os.name == "nt" else "siodump")
        wrapper.write_text("test double only", encoding="utf-8")
        dump.write_text("test double only", encoding="utf-8")
        launches = []

        def launch(command, **kwargs):
            launches.append((command, kwargs))
            work = Path(kwargs["cwd"])
            kwargs["stdout"].write(b"Simulation finished; 11 threads.\n")
            if command[0] == str(wrapper):
                (work / "proj.sio").write_bytes(b"raw source")
                (work / "proj.sam").write_bytes(b"samples")
            else:
                Path(command[command.index("-t") + 1]).write_text("# Hz S RI R 50\n1 0.1 0\n", encoding="utf-8")
            return types.SimpleNamespace(pid=12345, wait=lambda timeout: 0, poll=lambda: 0)

        with patch.dict("os.environ", {"HPEESOF_DIR": str(home), "OMP_NUM_THREADS": "11"}):
            with patch("subprocess.Popen", side_effect=launch):
                result = self.call("run_momentum", source_dir=str(source), input_hashes=hashes)
        self.assertTrue(result["ok"])
        self.assertEqual(launches[0][0], [str(wrapper), "-O", "-3D", "proj", "proj"])
        self.assertNotEqual(Path(launches[0][1]["cwd"]), source)
        self.assertEqual(launches[0][1]["env"]["OMP_NUM_THREADS"], "11")
        self.assertEqual(hashlib.sha256((source / "proj.opt").read_bytes()).hexdigest(), hashes["proj.opt"])
        self.assertIn('DS_NAME "em_result"', (self.output / "momentum" / "proj.opt").read_text())
        self.assertFalse(result["passivity_verified"])
        self.assertFalse(result["physical_reference_planes_verified"])
        self.assertFalse(result["actual_thread_count_verified"])
        self.assertIsNone(result["requested_solver_threads"])
        self.assertTrue(Path(result["raw_touchstone"]["path"]).is_file())

    def test_read_dataset_allows_runtime_state_as_read_only_source(self):
        with tempfile.TemporaryDirectory() as state_name:
            state = Path(state_name).resolve()
            path = state / "previous" / "saved.ds"
            path.parent.mkdir()
            path.write_bytes(b"real dataset input double")
            class Data:
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def __iter__(self):
                    return iter(["SP1.SP"])
            dataset = types.ModuleType("keysight.ads.dataset")
            dataset.open = lambda path: Data()
            self.modules["keysight.ads"].dataset = dataset
            self.modules["keysight.ads.dataset"] = dataset
            with patch.dict(sys.modules, self.modules):
                result = execute("read_dataset", {"path": str(path)}, str(self.root), str(state / "next"), str(state))
            self.assertEqual(result["blocks"], ["SP1.SP"])
            self.assertEqual(result["source"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_nonfinite_native_dataset_cannot_pass_numerical_check(self):
        class Frame:
            columns = ["freq", "S[1,1]"]
            def reset_index(self):
                return self
            def __len__(self):
                return 1
            def itertuples(self, **kwargs):
                return iter([(1e9, complex(float("nan"), 0))])
        class Data:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def __iter__(self):
                return iter(["SP1.SP"])
            def __getitem__(self, key):
                return types.SimpleNamespace(to_dataframe=lambda: Frame())
        dataset = types.ModuleType("keysight.ads.dataset")
        dataset.open = lambda path: Data()
        self.modules["keysight.ads"].dataset = dataset
        self.modules["keysight.ads.dataset"] = dataset
        toolbox = types.ModuleType("keysight.edatoolbox")
        def simulate(netlist, **kwargs):
            Path(kwargs["output_file"]).write_text("Simulation finished. 0 errors.\n", encoding="utf-8")
            (Path(kwargs["output_dir"]) / "test.ds").write_bytes(b"dataset")
        toolbox.ads = types.SimpleNamespace(CircuitSimulator=lambda: types.SimpleNamespace(run_netlist=simulate))
        self.modules["keysight.edatoolbox"] = toolbox
        self.de.version = lambda: "test-only"
        result = self.call("simulate_design")
        self.assertTrue(result["execution_ok"])
        self.assertFalse(result["numerical_ok"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["noise_model_qualified"])
        self.assertIsNone(result["performance_passed"])


    def test_inspection_separates_numbered_terminal_from_string_pin_id(self):
        pin = types.SimpleNamespace(inst_pin_id="R1.2", net=None, snap_point=None,
                                    inst_term=types.SimpleNamespace(is_numbered=True, term_number=2))
        instance = types.SimpleNamespace(name="R1", master_lcv_name="ads_rflib:R:symbol",
                                         origin=types.SimpleNamespace(x=1, y=2), orient="R0",
                                         inst_pins=[pin], parameters=[])
        self.design.instances = [instance]
        result = self.call("inspect_design")
        actual = result["instances"][0]["pins"][0]
        self.assertEqual(actual["id"], "R1.2")
        self.assertEqual(actual["number"], 2)
        self.assertEqual(actual["binding"], "number")

    def test_connect_rejects_zero_based_terminal_number(self):
        with self.assertRaisesRegex(ValueError, "positive terminal numbers"):
            self.call("edit_design", edits=[{"action": "connect", "from_instance": "R1", "from_pin": 0,
                                            "to_instance": "P1", "to_pin": 1, "net": "RF_IN"}])


if __name__ == "__main__":
    unittest.main()
