"""Shared operation validation for MCP, CLI and background workers."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[^\x00\r\n]+$")]
ArtifactPath = Annotated[str, Field(min_length=1, max_length=4096, pattern=r"^[^\x00]+$")]
Cellview = Annotated[str, Field(min_length=5, max_length=1538, pattern=r"^[^:/\\\x00\r\n]+:[^:/\\\x00\r\n]+:[^:/\\\x00\r\n]+$")]
Finite = Annotated[float, Field(strict=True)]
ParameterValue = Annotated[str, Field(max_length=16000)]
Parameters = Annotated[dict[Name, ParameterValue], Field(max_length=500)]

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class AddInstance(Strict):
    action: Literal["add_instance"]
    component: Cellview
    name: Name
    position: tuple[Finite, Finite]
    angle: Finite = 0
    parameters: Parameters = Field(default_factory=dict)

class SetParameters(Strict):
    action: Literal["set_parameters"]
    name: Name
    parameters: Parameters = Field(min_length=1)

class Connect(Strict):
    action: Literal["connect"]
    from_instance: Name
    from_pin: int = Field(strict=True, ge=1)
    to_instance: Name
    to_pin: int = Field(strict=True, ge=1)
    net: Name

class AddWire(Strict):
    action: Literal["add_wire"]
    points: list[tuple[Finite, Finite]] = Field(min_length=2, max_length=1000)

class Shape(Strict):
    action: Literal["rectangle", "polygon", "path"]
    layer: int = Field(strict=True, ge=0)
    purpose: int = Field(default=0, strict=True, ge=0)
    points: list[tuple[Finite, Finite]] = Field(min_length=2, max_length=1000)
    width: Finite = Field(default=1, gt=0)

    @model_validator(mode="after")
    def geometry(self):
        if self.action == "rectangle":
            if len(self.points) != 2:
                raise ValueError("A rectangle requires exactly two opposite corners")
            if self.points[0][0] == self.points[1][0] or self.points[0][1] == self.points[1][1]:
                raise ValueError("A rectangle must have nonzero width and height")
        elif self.action == "polygon" and len(set(self.points)) < 3:
            raise ValueError("A polygon requires at least three distinct points")
        return self

class Port(Strict):
    action: Literal["add_port"]
    number: int = Field(strict=True, ge=1)
    layer: int = Field(strict=True, ge=0)
    position: tuple[Finite, Finite]
    angle: Finite = 0

Edit = Annotated[AddInstance | SetParameters | Connect | AddWire | Shape | Port, Field(discriminator="action")]

class CreateWorkspace(Strict):
    workspace: ArtifactPath
    library: Name
    layout_units: Name = "micron"
    pdk_libraries: dict[Name, ArtifactPath] = Field(default_factory=dict, max_length=100)

class Design(Strict):
    workspace: ArtifactPath
    cellview: Cellview

class CreateDesign(Design):
    kind: Literal["schematic", "layout"]

class EditDesign(Design):
    edits: list[Edit] = Field(min_length=1, max_length=100)
    snapshot: Literal[True] = True

class SimulateDesign(Design):
    dataset_name: str = Field(default="simulation", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
    simulator_options: dict[Name, str | list[str]] = Field(default_factory=dict, max_length=3)

    @model_validator(mode="after")
    def simulator_paths(self):
        allowed = {"pdk_dirs", "verilog_dir", "rel_data_dir"}
        if set(self.simulator_options) - allowed:
            raise ValueError("Only pdk_dirs, verilog_dir and rel_data_dir simulator options are supported")
        for key, value in self.simulator_options.items():
            paths = value if key == "pdk_dirs" else [value]
            if (key == "pdk_dirs" and not isinstance(value, list)) or (key != "pdk_dirs" and not isinstance(value, str)):
                raise ValueError("pdk_dirs is a list; other simulator paths are strings")
            if len(paths) > 100 or any(not isinstance(v, str) or not 1 <= len(v) <= 4096 or "\x00" in v for v in paths):
                raise ValueError("Invalid simulator model search paths")
        return self

class RunMomentum(Strict):
    source_dir: ArtifactPath
    input_hashes: dict[Name, Annotated[str, Field(pattern=r"^[a-fA-F0-9]{64}$")]] = Field(min_length=1, max_length=1000)
    timeout_seconds: int = Field(default=3600, strict=True, ge=1, le=86400)
    solver_threads: int | None = Field(default=None, strict=True, ge=1, le=256)

class ReadDataset(Strict):
    path: ArtifactPath
    block: str = Field(default="", max_length=512)

class ExecutePython(Strict):
    code: str = Field(min_length=1, max_length=60000)

OPERATIONS = {
    "create_workspace": CreateWorkspace, "create_design": CreateDesign,
    "inspect_design": Design, "edit_design": EditDesign,
    "generate_netlist": Design, "simulate_design": SimulateDesign,
    "run_momentum": RunMomentum, "read_dataset": ReadDataset,
    "execute_python": ExecutePython,
}

def validate_operation_args(operation: str, arguments: dict) -> dict:
    """Reject unknown actions, extra fields and malformed CLI/worker input before ADS starts."""
    if operation not in OPERATIONS:
        raise ValueError("Unsupported operation: " + operation)
    if not isinstance(arguments, dict):
        raise ValueError("Operation arguments must be a JSON object")
    return OPERATIONS[operation].model_validate(arguments).model_dump(mode="json")
