#!/usr/bin/env python3
"""Hydro Twin scenario runner — one command for the full DEM/GIS/DualSPHysics workflow.

This is an orchestration layer over the existing project components:

  terrain pipeline   backend/app/services/terrain/  (clip/condition/flow/domain/sph_terrain/mesh)
  case generation     examples/main/<Case>/generate_terrain_case.py
  simulation          backend/app/services/sph/SPHRunner (GenCase / DualSPHysics / PartVTK)
  post-processing     examples/main/<Case>/*.py diagnostic scripts

Scenarios are plain JSON files in scenarios/. The existing individual CLIs
keep working unchanged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from scenario_runner import runner as engine  # noqa: E402
from scenario_runner.config import ScenarioError, list_scenarios  # noqa: E402
from scenario_runner.env import EnvError  # noqa: E402
from scenario_runner.report import utf8_console  # noqa: E402

STEP_CHOICES = ("terrain", "sph", "simulation", "postprocess")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_scenario.py",
        description=(
            "Run a complete Hydro Twin scenario with one command: DEM "
            "conditioning -> flow accumulation -> flood domain -> SPH terrain "
            "-> STL -> DualSPHysics case -> GenCase -> DualSPHysics -> PartVTK "
            "-> post-processing. Existing individual CLIs keep working."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/run_scenario.py list\n"
            "  python scripts/run_scenario.py chouldari --dry-run\n"
            "  python scripts/run_scenario.py chouldari\n"
            "  python scripts/run_scenario.py chouldari --step terrain\n"
            "  python scripts/run_scenario.py chouldari --force\n"
            "  python scripts/run_scenario.py chouldari --clean\n"
            "\n"
            "stages:\n"
            "  terrain      clip -> condition -> flow -> flood domain\n"
            "  sph          SPH terrain NPZ -> STL -> DualSPHysics case\n"
            "  simulation   GenCase -> DualSPHysics -> PartVTK\n"
            "  postprocess  analysis scripts listed by the scenario\n"
            "\n"
            "exit codes: 0 success | 1 validation/step failure | 2 usage/config\n"
        ),
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        help="scenario name from scenarios/<name>.json, or 'list'",
    )
    parser.add_argument(
        "--list",
        dest="list_flag",
        action="store_true",
        help="list available scenarios and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate configuration/inputs and show the full plan without executing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-run steps even if their outputs are fresh (bypasses the manifest)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help=(
            "delete this scenario's generated outputs before running "
            "(raw inputs are never touched; each deletion is printed first)"
        ),
    )
    parser.add_argument(
        "--step",
        choices=STEP_CHOICES,
        help="run a single pipeline stage instead of the full workflow",
    )
    parser.add_argument(
        "--dualsphysics-root",
        metavar="PATH",
        help=(
            "DualSPHysics installation for this run only (overrides "
            "DUALSPHYSICS_ROOT / scenario / local config; nothing is written "
            "to the Windows environment)"
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show full Python tracebacks on failures",
    )
    return parser


def print_scenario_list() -> int:
    items = list_scenarios()
    print("Available scenarios:\n")
    if not items:
        print("  (none found - add scenarios/<name>.json)")
        return 1
    width = max(len(item["name"]) for item in items)
    for index, item in enumerate(items, 1):
        desc = f" - {item['description']}" if item["description"] else ""
        print(f"  {index}. {item['name']:<{width}}   {item['display_name']}{desc}")
    print("\nInspect one with:  python scripts/run_scenario.py <name> --dry-run")
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_flag or (args.scenario and args.scenario.lower() == "list"):
        return print_scenario_list()

    if not args.scenario:
        parser.print_help()
        return 2

    try:
        return engine.run(args)
    except ScenarioError as exc:
        print(f"\nConfiguration error:\n\n{exc}\n", file=sys.stderr)
        return 2
    except EnvError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
