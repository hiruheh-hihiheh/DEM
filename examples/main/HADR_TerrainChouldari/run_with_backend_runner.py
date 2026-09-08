"""
Optional helper to run the experimental terrain case through the existing
backend SPHRunner infrastructure.

This does NOT modify runner.py.

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

RUNNER_XML = CASE_DIR / "HADR_TerrainChouldari_runner_Def.xml"
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