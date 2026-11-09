"""One explicit, isolated three-point ideal-resistor SP acceptance. No retries."""
import argparse
import asyncio
import csv
import json
from pathlib import Path
import time
from uuid import uuid4
from ads_mcp.config import load_settings
from ads_mcp.jobs import JobManager
from ads_mcp.runtime import run_operation

async def main(config):
    settings = load_settings(config)
    token = uuid4().hex[:10]
    workspace, library = "sp_" + token + "_wrk", "sp_" + token
    cellview = library + ":series_resistor:schematic"
    receipts = []
    async def call(operation, **args):
        result = await run_operation(settings, operation, args)
        receipts.append({"operation": operation, "result": result})
        if result.get("ok") is False:
            raise RuntimeError(result)
        return result
    await call("create_workspace", workspace=workspace, library=library)
    await call("create_design", workspace=workspace, cellview=cellview, kind="schematic")
    def part(component, name, position, parameters, angle=0):
        return dict(action="add_instance", component=component, name=name, position=position,
                    parameters=parameters, angle=angle)
    await call("edit_design", workspace=workspace, cellview=cellview, edits=[
        part("ads_simulation:TermG:symbol", "P1", [0,0], {"Num":"1","Z":"50 Ohm"}),
        part("ads_rflib:R:symbol", "R1", [10,0], {"R":"50 Ohm"}),
        part("ads_simulation:TermG:symbol", "P2", [20,0], {"Num":"2","Z":"50 Ohm"}, 180),
        part("ads_simulation:S_Param:symbol", "SP1", [0,10],
             {"Start":"1 GHz","Stop":"2 GHz","Step":"0.5 GHz","CalcS":"yes","CalcNoise":"no"})])
    observed = await call("inspect_design", workspace=workspace, cellview=cellview)
    assert {i["name"] for i in observed["instances"]} == {"P1","R1","P2","SP1"}
    def connect(a, ap, b, bp, net):
        return dict(action="connect",from_instance=a,from_pin=ap,to_instance=b,to_pin=bp,net=net)
    await call("edit_design", workspace=workspace, cellview=cellview, edits=[
        connect("P1",1,"R1",1,"RF_IN"),connect("R1",2,"P2",1,"RF_OUT")])
    await call("generate_netlist", workspace=workspace, cellview=cellview)
    manager = JobManager(settings)
    status = manager.submit("simulate_design", {
        "workspace":workspace,"cellview":cellview,"dataset_name":"resistor_sp"})
    job_id = status["job_id"]
    # ONE paid attempt, with no retry on failure, unknown completion or timeout.
    deadline = time.monotonic() + 120
    while status["status"] in {"starting","running"} and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        status = manager.status(job_id)
    if status["status"] != "completed":
        raise RuntimeError("One-attempt smoke stopped: " + json.dumps(status))
    result = status["result"]
    blocks = [b for b in result["dataset_inventory"] if "S[2,1]" in b["columns"]]
    assert len(blocks) == 1, blocks
    path = next(d["path"] for d in result["datasets"] if Path(d["path"]).name == blocks[0]["dataset"])
    exported = await call("read_dataset", path=path, block=blocks[0]["block"])
    with Path(exported["csv"]["path"]).open(newline="",encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3, rows
    expected = {"S[1,1]":1/3,"S[2,2]":1/3,"S[2,1]":2/3,"S[1,2]":2/3}
    maximum_error = max(abs(complex(row[column])-value) for row in rows for column,value in expected.items())
    assert maximum_error < 1e-8, maximum_error
    return {"ok":True,"workspace":workspace,"job_id":job_id,"paid_attempts":1,"automatic_retries":0,
            "frequency_points":len(rows),"s_matrix_maximum_absolute_error":maximum_error,
            "expected":"50-ohm series R between two 50-ohm ports; S11=S22=1/3, S12=S21=2/3",
            "simulation":result,"receipts":receipts,
            "noise_or_MMIC_or_EM_qualification":False}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    parser.add_argument("--run",action="store_true",help="Authorize exactly one ideal-circuit simulation")
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required; this uses one real circuit-simulation attempt")
    report = asyncio.run(main(args.config))
    print(json.dumps(report,ensure_ascii=False,indent=2))
