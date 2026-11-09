"""Write a new operator config and generated client entry, never overwrite existing config."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--ads-home", required=True)
parser.add_argument("--project-root", type=Path, required=True)
parser.add_argument("--config", type=Path, default=Path("ads-mcp.toml"))
parser.add_argument("--keep-existing", action="store_true")
args = parser.parse_args()
args.project_root.mkdir(parents=True, exist_ok=True)
config = args.config.resolve()
if config.exists():
    if not args.keep_existing:
        raise SystemExit("Configuration exists; inspect it instead of overwriting")
    print("Preserved existing configuration:", config)
else:
    def quoted(path):
        return json.dumps(str(Path(path).resolve()).replace("\\\\", "/"), ensure_ascii=False)
    config.write_text(
        "[ads_mcp]\n" +
        "ads_home = " + quoted(args.ads_home) + "\n" +
        "project_root = " + quoted(args.project_root) + "\n" +
        "state_dir = " + quoted(args.project_root / ".ads-mcp") + "\n" +
        "max_workers = 1\ntool_timeout_seconds = 3600\nallow_execute_python = false\n",
        encoding="utf-8")
result = subprocess.run(
    [sys.executable, "-m", "ads_mcp", "--config", str(config), "client-config"],
    check=True, capture_output=True, text=True, encoding="utf-8")
output = Path(".ads-mcp")
output.mkdir(exist_ok=True)
(output / "client-config.json").write_text(result.stdout, encoding="utf-8")
