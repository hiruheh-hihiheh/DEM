"""
Optional helper to run the experimental terrain case through the existing
backend SPHRunner infrastructure.

This does NOT modify runner.py.

The runner definition XML name is read from this case's
terrain_case_config.json (runner_definition_name), so it works for any
generated site case without editing hard-coded Chouldari paths.

Important:
- The current SPHRunner hardcodes the internal case name HADR_DamBreak.
- This terrain run is still isolated because SPHRunner creates a unique
  simulation_id directory under simulations/runs.
- The generated runner XML uses an absolute path to the copied terrain STL,
  because the existing runner copies only the XML definition file.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


CASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CASE_DIR.parents[2]
BACKEND_DIR = REPO_ROOT / "backend"


def _load_case_config() -> dict:
    config_path = CASE_DIR / "terrain_case_config.json"
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - helper script
        print(f"WARNING: cannot parse {config_path.name}: {exc}")
        return {}


_CFG = _load_case_config()
CASE_NAME = str(_CFG.get("case_name", "HADR_TerrainChouldari"))
RUNNER_DEFINITION_NAME = str(
    _CFG.get("runner_definition_name", f"{CASE_NAME}_runner_Def.xml")
)

RUNNER_XML = CASE_DIR / RUNNER_DEFINITION_NAME
RUNS_DIR = REPO_ROOT / "simulations" / "runs"


def main() -> int:
    dualsph_root = os.getenv("DUALSPHYSICS_ROOT")

    if not dualsph_root:
        print("ERROR: DUALSPHYSICS_ROOT environment variable is not set.")
        return 1

    if not RUNNER_XML.exists():
        print(f"ERROR: Runner XML not found: {RUNNER_XML}")
        print("Run generate_terrain_case.py first.")
        return 1

    sys.path.insert(0, str(BACKEND_DIR))

    try:
        from app.services.sph.runner import SPHRunner
    except ImportError as exc:
        print(f"ERROR: Unable to import backend SPHRunner: {exc}")
        return 1

    print("WARNING: This is an experimental terrain integration case.")
    print("The existing SPHRunner may still use its internal HADR_DamBreak case name.")
    print("The run remains isolated under simulations/runs/<simulation_id>.")

    try:
        runner = SPHRunner(Path(dualsph_root))
        result = runner.run(RUNNER_XML, RUNS_DIR)
    except Exception as exc:
        print(f"ERROR: Terrain runner execution failed: {exc}")
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())