"""Reviewed primitives evaluated inside an owned ADS Python session.

Only ``execute`` is sent to ADS. Keep its dependencies and helpers local so
``inspect.getsource(execute)`` is a complete, inspectable worker payload.
"""


def execute(operation: str, args: dict, project_root: str, output_dir: str, state_root: str | None = None) -> dict:
    """Operate on project-owned ADS data; never install or modify a vendor PDK.

    Circuit analyses use the controllers already saved in the design. Momentum
    accepts an explicitly hash-bound, already preprocessed input directory; it
    does not guess a substrate, ports, calibration, or frequency setup.
    """
    from pathlib import Path
    import hashlib
    import json
    import math
    import numbers
    import os
    import re
    import shutil
    import subprocess
    import time
    from uuid import uuid4

    supported = {"create_workspace", "create_design", "inspect_design", "edit_design",
                 "generate_netlist", "simulate_design", "read_dataset", "run_momentum"}
    if operation not in supported:
        raise ValueError("Unsupported native operation: " + str(operation))
    if not isinstance(args, dict):
        raise ValueError("Native arguments must be an object")
    root = Path(project_root).resolve(strict=True)
    state = Path(state_root).resolve(strict=True) if state_root is not None else root
    if not Path(output_dir).is_absolute():
        raise ValueError("output_dir must be an absolute runtime-owned state directory")
    output = Path(output_dir).resolve()
    if (not root.is_dir() or not state.is_dir() or output == state
            or not output.is_relative_to(state) or output == root):
        raise ValueError("output_dir must be a dedicated directory inside the runtime-owned state root")
    output.mkdir(parents=True, exist_ok=True)

    def scoped(value, *, exists=True):
        if not isinstance(value, str) or not value:
            raise ValueError("An explicit project path is required")
        path = (root / value).resolve()
        if path == root or not path.is_relative_to(root):
            raise ValueError("Path is outside the owned project directory")
        if exists and not path.exists():
            raise ValueError("Project input does not exist: " + str(path))
        return path

    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", value):
            raise ValueError("Invalid ADS library, cell, or instance identifier")
        return value

    def point(value):
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) for v in value)):
            raise ValueError("Coordinates must be two finite numbers")
        return tuple(value)

    def number(value, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(name + " must be a finite number")
        return value

    def sha(path):
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def reference(path):
        path = Path(path).resolve()
        return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}

    def save(name, value):
        path = output / name
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        return reference(path)

    def unlocked(directory):
        # A lock is evidence of an owner, including an owner outside this server.
        # Never delete it or infer that an old timestamp makes it safe to ignore.
        for entry in directory.rglob("*"):
            if entry.name.lower().endswith((".lck", ".lock", ".cdslck")):
                raise ValueError("ADS lock present; preserve its owner: " + str(entry))

    def no_links(directory):
        for entry in [directory, *directory.rglob("*")]:
            if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                raise ValueError("Input trees may not contain symbolic links or junctions")

    def log_review(text):
        diagnostics = []
        for line_no, line in enumerate(text.splitlines(), 1):
            lower = line.lower()
            if re.search(r"\b(?:0|no)\s+(?:fatal\s+)?errors?\b", lower):
                continue
            category = None
            if re.search(r"license.*(?:denied|failed|unavailable|not available)|unable to.*license", lower):
                category = "license_error"
            elif re.search(r"failed to converge|not converged|convergence failure|singular matrix", lower):
                category = "convergence_error"
            elif re.search(r"\bfatal\b|^\s*error\b|\berror (?:detected|found)|simulation.*(?:aborted|failed)", lower):
                category = "fatal_error"
            elif "warning" in lower or "truncat" in lower:
                category = "warning"
            if category:
                diagnostics.append({"category": category, "line": line_no, "text": line[:1500]})
        return {"passed": not any(d["category"] != "warning" for d in diagnostics),
                "diagnostics": diagnostics}

    def run_owned(command, cwd, log, timeout, environment):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        started = time.monotonic()
        with Path(log).open("xb") as stream:
            process = subprocess.Popen(command, cwd=str(cwd), env=environment, stdout=stream,
                                       stderr=subprocess.STDOUT, shell=False, creationflags=flags)
            timed_out = False
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                # This PID comes only from the Popen handle just created here.
                if os.name == "nt" and process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=stream, stderr=subprocess.STDOUT, shell=False,
                                   creationflags=flags, timeout=30, check=False)
                elif process.poll() is None:
                    process.kill()
                returncode = process.wait(timeout=30)
        return {"command": command, "pid": process.pid, "returncode": returncode,
                "timed_out": timed_out, "elapsed_seconds": time.monotonic() - started,
                "log": reference(log), "shell": False}

    if operation == "run_momentum":
        source = scoped(args["source_dir"])
        if not source.is_dir():
            raise ValueError("source_dir must be an already preprocessed Momentum directory")
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("Momentum input and output directories must be separate")
        unlocked(source)
        no_links(source)
        required = ("proj_a", "proj.ltd", "proj.plan", "proj.pin", "proj.prt", "proj.opt", "proj.sti", "proj.cfg")
        expected = args.get("input_hashes")
        if not isinstance(expected, dict) or not expected:
            raise ValueError("input_hashes must bind every source file before dispatch")
        source_files = {p.relative_to(source).as_posix(): p for p in source.rglob("*") if p.is_file()}
        if set(expected) != set(source_files):
            raise ValueError("Input hash inventory differs from the complete source directory")
        if not all(name in source_files and source_files[name].stat().st_size for name in required):
            raise ValueError("Missing complete native preprocessed Momentum input files")
        if any(name in source_files for name in ("proj.sio", "proj.sam")):
            raise ValueError("Completed Momentum outputs must be recovered, not rerun")
        for name, path in source_files.items():
            if not isinstance(expected[name], str) or sha(path) != expected[name]:
                raise ValueError("Momentum source hash changed: " + name)
        if not os.environ.get("HPEESOF_DIR"):
            raise ValueError("The runtime must configure HPEESOF_DIR")
        home = Path(os.environ["HPEESOF_DIR"]).resolve(strict=True)
        wrapper = home / "bin" / ("adsMomWrapper.bat" if os.name == "nt" else "adsMomWrapper")
        dump_exe = home / "bin" / ("siodump.exe" if os.name == "nt" else "siodump")
        if not wrapper.is_file() or not dump_exe.is_file():
            raise ValueError("Installed ADS Momentum wrapper and siodump are required")
        timeout = number(args.get("timeout_seconds", 1800), "timeout_seconds")
        if not 1 <= timeout <= 86400:
            raise ValueError("timeout_seconds must be in 1..86400")
        threads = args.get("solver_threads")
        if threads is not None and (type(threads) is not int or not 1 <= threads <= 256):
            raise ValueError("solver_threads must be null or an integer in 1..256")
        from xml.etree import ElementTree as ET
        ports = ET.parse(source / "proj.prt").findall("./port_list/port")
        if not ports:
            raise ValueError("Preprocessed native port inventory is empty")
        port_inventory = [{"id": p.attrib.get("id"), "name": p.findtext("name"),
                           "reference_real": p.findtext("impedance/real"),
                           "reference_imag": p.findtext("impedance/imag")} for p in ports]
        work = output / "momentum"
        shutil.copytree(source, work)
        for name in source_files:
            if sha(work / name) != expected[name] or sha(source_files[name]) != expected[name]:
                raise ValueError("Momentum input changed while making its isolated copy")
        environment = dict(os.environ)
        for key in ("PYTHONHOME", "PYTHONPATH"):
            environment.pop(key, None)
        inherited = {k: environment.get(k) for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")}
        environment.update(HPEESOF_DIR=str(home), ADS_HOME=str(home))
        if threads is not None:
            for key in inherited:
                environment[key] = str(threads)
        environment["PATH"] = str(home / "bin") + os.pathsep + environment.get("PATH", "")
        # The reviewed source runner relocates only these nonphysical output
        # fields. Preserve every physical statement and retain the input digest.
        original_options = (work / "proj.opt").read_text(encoding="utf-8")
        runtime_options = original_options
        for field in ("logFile", "DS_DIR", "DS_NAME"):
            runtime_options = re.sub(r"\b" + field + r"\s+[^;]*;", "", runtime_options, flags=re.I)
        runtime_options += '\nlogFile "proj.log";\nDS_DIR "' + str(work).replace("\\", "/") + '";\nDS_NAME "em_result";\n'
        (work / "proj.opt").write_text(runtime_options, encoding="utf-8")
        inputs = {name: {"sha256": expected[name], "bytes": path.stat().st_size}
                  for name, path in source_files.items()}
        save("momentum_inputs.json", {"source_dir": str(source), "inputs": inputs,
             "native_ports": port_inventory, "substrate_input": inputs["proj.ltd"],
             "pin_input": inputs["proj.pin"], "port_input": inputs["proj.prt"],
             "runtime_output_options": reference(work / "proj.opt"),
             "runtime_option_fields_changed": ["logFile", "DS_DIR", "DS_NAME"],
             "inherited_thread_environment": inherited, "requested_threads": threads,
             "physical_reference_planes_verified": False, "stackup_verified": False})
        process = run_owned([str(wrapper), "-O", "-3D", "proj", "proj"], work,
                            output / "momentum.log", timeout, environment)
        raw = output / ("raw.s" + str(len(ports)) + "p")
        solved = (not process["timed_out"] and process["returncode"] == 0
                  and all((work / n).is_file() and (work / n).stat().st_size for n in ("proj.sio", "proj.sam")))
        dump = None
        if solved:
            dump = run_owned([str(dump_exe), "-s", "-f", "1", "-t", str(raw), str(work / "proj.sio")],
                             work, output / "siodump.log", min(timeout, 120), environment)
        exported = bool(dump and not dump["timed_out"] and dump["returncode"] == 0
                        and raw.is_file() and raw.stat().st_size)
        logs = (output / "momentum.log").read_text(encoding="utf-8", errors="replace")
        if (work / "proj.log").is_file():
            logs += "\n" + (work / "proj.log").read_text(encoding="utf-8", errors="replace")
        review = log_review(logs)
        source_unchanged = all(sha(path) == expected[name] for name, path in source_files.items())
        result = {"ok": solved and exported and review["passed"] and source_unchanged,
                  "status": "completed" if solved and exported and review["passed"] and source_unchanged else "failed",
                  "solver_started": True, "source_unchanged": source_unchanged,
                  "process": process, "export_process": dump, "log_review": review,
                  "input_manifest": reference(output / "momentum_inputs.json"),
                  "raw_touchstone": reference(raw) if exported else None,
                  "source_sio": reference(work / "proj.sio") if solved else None,
                  "native_ports": port_inventory, "requested_solver_threads": threads,
                  "thread_log_lines": [line for line in logs.splitlines() if re.search(r"threads?|processors?|cores?", line, re.I)],
                  "actual_thread_count_verified": False, "frequency_coverage_verified": False,
                  "passivity_verified": False, "thermal_noise_verified": False,
                  "physical_reference_planes_verified": False, "stackup_verified": False,
                  "qualification": "raw_solver_output_only", "automatic_retry": False}
        result["evidence"] = save("momentum_result.json", result)
        return result

    if operation == "read_dataset":
        from keysight.ads import dataset
        original_path = Path(args["path"])
        original_path = original_path if original_path.is_absolute() else root / original_path
        current = original_path
        while current != current.parent:
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise ValueError("Dataset paths may not traverse symbolic links or junctions")
            current = current.parent
        path = original_path.resolve(strict=True)
        if not any(path != base and path.is_relative_to(base) for base in (root, state)):
            raise ValueError("Dataset must belong to project_root or the runtime-owned state root")
        if not path.is_file() or path.suffix.lower() != ".ds":
            raise ValueError("A project-owned ADS .ds file is required")
        before = reference(path)
        with dataset.open(path) as data:
            blocks = list(data)
            block = args.get("block", "")
            if not block:
                if reference(path) != before:
                    raise ValueError("Dataset changed during inventory")
                return {"ok": True, "blocks": blocks, "source": before}
            if block not in blocks:
                raise ValueError("Dataset block is absent")
            frame = data[block].to_dataframe().reset_index()
        target = output / ("dataset_" + uuid4().hex[:8] + ".csv")
        frame.to_csv(target, index=False)
        if reference(path) != before:
            raise ValueError("Dataset changed during export")
        return {"ok": True, "block": block, "rows": len(frame), "columns": list(frame.columns),
                "csv": reference(target), "source": before,
                "preview": [{str(k): str(v) for k, v in row.items()}
                            for row in frame.head(6).to_dict(orient="records")],
                "qualification": "dataset_export_only"}

    workspace = scoped(args["workspace"], exists=operation != "create_workspace")
    if output.is_relative_to(workspace) or workspace.is_relative_to(output):
        raise ValueError("Native operation evidence and target workspace must be separate")
    if operation == "create_workspace":
        if workspace.exists():
            raise FileExistsError("Workspace already exists; no overwrite is permitted")
    else:
        if not workspace.is_dir() or not (workspace / "lib.defs").is_file():
            raise ValueError("workspace must contain an existing lib.defs")
        unlocked(workspace)

    snapshot = None
    if operation == "edit_design":
        if output.is_relative_to(workspace) or workspace.is_relative_to(output):
            raise ValueError("Edit evidence and workspace must be separate for snapshots")
        if args.get("snapshot", True) is not True:
            raise ValueError("Editing requires a pre-edit disk snapshot")
        no_links(workspace)
        destination = output / "workspace_before"
        shutil.copytree(workspace, destination)
        snapshot = {"path": str(destination), "scope": "saved_disk_files_only",
                    "includes_unsaved_gui_state": False}

    from keysight.ads import de
    from keysight.ads.de import db_uu as db
    from keysight.ads.de.experimental import DesignBuilder
    if de.workspace_is_open():
        active = Path(de.active_workspace().path).resolve()
        if active != workspace:
            raise ValueError("Owned session already has another workspace open; preserving it")

    if operation == "create_workspace":
        library = identifier(args["library"])
        units = args.get("layout_units", "micron")
        if units not in ("millimeter", "micron", "mil"):
            raise ValueError("Unsupported layout units")
        external = args.get("pdk_libraries", {})
        if not isinstance(external, dict):
            raise ValueError("pdk_libraries must map names to existing absolute paths")
        pdk_paths = {}
        for name, value in external.items():
            identifier(name)
            path = Path(value)
            if name == library or not path.is_absolute() or not path.is_dir():
                raise ValueError("PDK library must be an existing external directory with a unique name")
            pdk_paths[name] = path.resolve()
        workspace.parent.mkdir(parents=True, exist_ok=True)
        handle = de.create_workspace(workspace)
        handle.open()
        lib = de.create_new_library(library, workspace / library)
        handle.add_library(library, lib.path, de.LibraryMode.NON_SHARED)
        lib.setup_schematic_tech()
        lib.create_layout_tech_std_ads(units, 1000)
        for name, path in pdk_paths.items():
            handle.add_library(name, path, de.LibraryMode.READ_ONLY)
        return {"ok": True, "workspace": str(workspace), "library": library,
                "pdk_libraries": {n: str(p) for n, p in pdk_paths.items()},
                "vendor_libraries_read_only": True, "model_qualified": False}

    if not de.workspace_is_open():
        de.open_workspace(str(workspace))
    parts = args["cellview"].split(":")
    if len(parts) != 3:
        raise ValueError("Use library:cell:view")
    for part in parts:
        identifier(part)
    library = de.get_open_library(parts[0])
    library_path = Path(library.path).resolve()
    if not library_path.is_relative_to(workspace) or not library_path.is_relative_to(root):
        raise ValueError("Target library must be inside the owned workspace; vendor libraries are read-only")

    if operation == "create_design":
        kind = args["kind"]
        if kind not in ("schematic", "layout") or parts[2] != kind:
            raise ValueError("kind must match the schematic/layout view name")
        builder = (DesignBuilder.build_schematic if kind == "schematic" else DesignBuilder.build_layout)(
            args["cellview"], overwrite=False)
        builder.save_design()
        return {"ok": True, "cellview": args["cellview"], "kind": kind, "overwritten": False}

    design = db.open_design(args["cellview"], "Append") if operation == "edit_design" else db.open_design(args["cellview"])

    def pin_readback(pin):
        term = pin.inst_term
        numbered = term.is_numbered
        return {"id": pin.inst_pin_id,
                "number": term.term_number if numbered else None,
                "terminal_name": None if numbered else term.term_name,
                "binding": "number" if numbered else "name",
                "net": pin.net.name if pin.net else None,
                "position": [pin.snap_point.x, pin.snap_point.y] if pin.snap_point else None}

    def inspection():
        return {"cellview": args["cellview"], "instances": [
            {"name": i.name, "component": str(i.master_lcv_name),
             "position": [i.origin.x, i.origin.y], "orientation": str(i.orient),
             "pins": [pin_readback(p) for p in i.inst_pins],
             "parameters": {p.name: str(p.value) for p in i.parameters}} for i in design.instances],
                "nets": [n.name for n in design.nets], "physical_connectivity_verified": False,
                "qualification": "native_logical_readback_only"}

    if operation == "inspect_design":
        return {"ok": True, **inspection()}
    if operation == "edit_design":
        edits = args["edits"]
        if not isinstance(edits, list) or not 1 <= len(edits) <= 100:
            raise ValueError("edits requires 1..100 typed edits")
        actions = {"add_instance", "set_parameters", "connect", "add_wire", "rectangle", "polygon", "path", "add_port"}
        for edit in edits:
            if not isinstance(edit, dict) or edit.get("action") not in actions:
                raise ValueError("Unsupported edit action")
            action = edit["action"]
            if action in ("add_instance", "set_parameters"):
                identifier(edit["name"])
                if not isinstance(edit.get("parameters", {}), dict):
                    raise ValueError("parameters must be a mapping")
                if not all(isinstance(k, str) and isinstance(v, (str, list))
                           and (not isinstance(v, list) or all(isinstance(s, str) for s in v))
                           for k, v in edit.get("parameters", {}).items()):
                    raise ValueError("Parameter values must be ADS expression strings or string lists")
            if action == "connect":
                identifier(edit["from_instance"])
                identifier(edit["to_instance"])
                if any(type(edit[key]) is not int or edit[key] < 1 for key in ("from_pin", "to_pin")):
                    raise ValueError("Connections require positive terminal numbers from inspect_design, not string pin IDs or zero-based indexes")
                if not isinstance(edit["net"], str) or not edit["net"]:
                    raise ValueError("An explicit connection net name is required")
            if action == "add_port" and (type(edit["number"]) is not int or edit["number"] < 1):
                raise ValueError("Port number must be a positive integer")
            if action in ("add_instance", "add_port"):
                point(edit["position"])
                number(edit.get("angle", 0), "angle")
            if action in ("add_wire", "rectangle", "polygon", "path"):
                points = edit["points"]
                if not isinstance(points, list) or not 2 <= len(points) <= 1000:
                    raise ValueError("Geometry requires 2..1000 points")
                for value in points:
                    point(value)
                if action == "rectangle" and len(points) != 2:
                    raise ValueError("Rectangle needs two opposite corners")
                if action == "polygon" and len(points) < 3:
                    raise ValueError("Polygon needs at least three vertices")
                if action == "path" and number(edit["width"], "width") <= 0:
                    raise ValueError("Path width must be positive")
            if action in ("rectangle", "polygon", "path", "add_port"):
                if type(edit["layer"]) is not int or type(edit.get("purpose", 0)) is not int:
                    raise ValueError("Layer and purpose must be integers")
        builder = DesignBuilder(design)
        builder.allow_wires = design.is_schematic
        completed = []
        for index, edit in enumerate(edits):
            try:
                action = edit["action"]
                if action == "add_instance":
                    if design.find_instance(edit["name"]) is not None:
                        raise ValueError("Instance already exists")
                    instance = builder.place_instance(edit["component"], point(edit["position"]),
                                                     name=edit["name"], angle=edit.get("angle", 0))
                    for key, value in edit.get("parameters", {}).items():
                        instance.parameters[key].value = value
                elif action == "set_parameters":
                    instance = design.get_instance(edit["name"])
                    for key, value in edit["parameters"].items():
                        instance.parameters[key].value = value
                elif action == "connect":
                    builder.make_connection((design.get_instance(edit["from_instance"]), edit["from_pin"]),
                                            (design.get_instance(edit["to_instance"]), edit["to_pin"]), edit["net"])
                elif action == "add_wire":
                    design.add_wire([point(p) for p in edit["points"]])
                elif action == "add_port":
                    builder.place_dot_pin(edit["number"], edit["layer"], point(edit["position"]), angle=edit.get("angle", 0))
                else:
                    layer = db.LayerId(edit["layer"], edit.get("purpose", 0))
                    points = [point(p) for p in edit["points"]]
                    if action == "rectangle":
                        design.add_rectangle(layer, points[0], points[1])
                    elif action == "polygon":
                        design.add_polygon(layer, points)
                    else:
                        design.add_path(layer, points, edit["width"])
                design.save_design()
                completed.append({"index": index, "action": action})
            except Exception as error:
                design.save_design()
                return {"ok": False, "status": "partial", "completed": completed, "failed_index": index,
                        "error": str(error), "snapshot": snapshot, "readback": inspection(),
                        "automatic_retry": False,
                        "note": "Earlier edits and possibly part of the failed edit were saved; inspect before retrying."}
        return {"ok": True, "completed": completed, "snapshot": snapshot, "readback": inspection(),
                "parameter_callbacks_invoked": False, "pcell_geometry_verified": False,
                "note": "PDK-dependent parameter callbacks and physical geometry require separate native verification."}

    netlist = design.generate_netlist()
    target = output / "input.ckt"
    with target.open("x", encoding="utf-8") as stream:
        stream.write(netlist)
    if operation == "generate_netlist":
        return {"ok": True, "netlist": reference(target), "qualification": "native_netlist_only"}
    if operation == "simulate_design":
        from keysight.edatoolbox import ads
        from keysight.ads import dataset
        dataset_name = identifier(args.get("dataset_name", "result"))
        options = args.get("simulator_options", {})
        if not isinstance(options, dict) or set(options) - {"pdk_dirs", "verilog_dir", "rel_data_dir"}:
            raise ValueError("Only explicit external PDK directory options are supported")
        for key, value in options.items():
            paths = value if key == "pdk_dirs" else [value]
            if not isinstance(paths, list) or not paths:
                raise ValueError("PDK paths must be explicitly declared")
            for value in paths:
                path = Path(value)
                if not path.is_absolute() or not path.is_dir():
                    raise ValueError("PDK paths must be existing absolute directories")
        log = output / "simulator.log"
        error = None
        try:
            ads.CircuitSimulator().run_netlist(netlist, output_dir=str(output), working_dir=str(output),
                output_file=str(log), netlist_file=str(target), dataset_name=dataset_name, **options)
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
        datasets = sorted(output.glob("*.ds"))
        inventory = []
        try:
            for path in datasets:
                with dataset.open(path) as data:
                    for block in data:
                        frame = data[block].to_dataframe().reset_index()
                        numeric = 0
                        nonfinite = 0
                        for row in frame.itertuples(index=False, name=None):
                            for value in row:
                                if isinstance(value, numbers.Number) and not isinstance(value, bool):
                                    numeric += 1
                                    value = complex(value)
                                    if not math.isfinite(value.real) or not math.isfinite(value.imag):
                                        nonfinite += 1
                        inventory.append({"dataset": path.name, "block": block, "rows": len(frame),
                                          "columns": list(frame.columns), "numeric_values": numeric,
                                          "nonfinite_values": nonfinite})
        except Exception as exc:
            error = error or "Dataset validation: " + type(exc).__name__ + ": " + str(exc)
        review = log_review(log.read_text(encoding="utf-8", errors="replace")) if log.is_file() else {"passed": False, "diagnostics": [{"category": "missing_log"}]}
        execution_ok = bool(datasets) and all(p.stat().st_size for p in datasets) and review["passed"] and not error
        numerical_ok = bool(inventory) and all(i["rows"] > 0 and i["numeric_values"] > 0 and i["nonfinite_values"] == 0 for i in inventory)
        result = {"ok": bool(execution_ok and numerical_ok), "execution_ok": bool(execution_ok),
                  "numerical_ok": numerical_ok, "netlist": reference(target),
                  "log": reference(log) if log.is_file() else None,
                  "datasets": [reference(p) for p in datasets], "dataset_inventory": inventory,
                  "log_review": review, "error": error, "ads_version": str(de.version()),
                  "noise_model_qualified": False, "performance_passed": None,
                  "qualification": "native_simulation_only", "automatic_retry": False}
        result["evidence"] = save("simulation_result.json", result)
        return result
    raise ValueError("Native operation did not reach an implementation")
