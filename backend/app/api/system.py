"""System readiness + local DualSPHysics configuration.

Single source of truth: the actual resolution/validation code lives in
``scripts/scenario_runner/env.py`` (the SAME code the Scenario Runner uses
before launching GenCase / DualSPHysics / PartVTK), so the backend and the
runner can never disagree about what is installed.

Resolution order (unchanged, per-run only — the Windows environment is
never modified):

  1. --dualsphysics-root CLI flag        (runner CLI only)
  2. DUALSPHYSICS_ROOT environment variable
  3. "dualsphysics_root" in the scenario file
  4. scenarios/local.json                (machine-local, git-ignored)
  5. clear error

The settings UI writes ONLY ``scenarios/local.json`` (git-ignored); nothing
machine-specific is ever stored in committed scenario files.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_SCENARIO = REPO_ROOT / "scripts" / "run_scenario.py"
SCENARIOS_DIR = REPO_ROOT / "scenarios"
LOCAL_CONFIG_PATH = SCENARIOS_DIR / "local.json"

router = APIRouter(prefix="/api/system", tags=["System"])


def _ensure_scripts_on_path() -> None:
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


# ---------------------------------------------------------------------------
# DualSPHysics inspection (delegates to scenario_runner.env)
# ---------------------------------------------------------------------------

# binary_paths() returns (label, path) tuples in REQUIRED_BINARIES order.
_BINARY_KEYS = ("gencase", "solver", "partvtk")


def _inspect_root(root: str | None) -> dict[str, Any]:
    """Resolve ``root`` (or the configured default) and check the binaries."""
    _ensure_scripts_on_path()
    from scenario_runner.env import (
        EnvError,
        binary_paths,
        resolve_dualsphysics_root,
    )

    try:
        info = resolve_dualsphysics_root(root, None)
    except EnvError as exc:
        return {
            "configured": root is not None,
            "root": None,
            "source": None,
            "exists": False,
            "ready": False,
            "missing": [],
            "error": str(exc),
            "env_override": os.environ.get("DUALSPHYSICS_ROOT") or None,
        }

    exists = info.root.is_dir()
    binaries: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for key, (_label, path) in zip(_BINARY_KEYS, binary_paths(info.root)):
        available = exists and path.is_file()
        binaries[key] = {
            "available": available,
            "filename": path.name,
            "path": str(path),
        }
        if not available:
            missing.append(path.name)

    return {
        "configured": True,
        "root": str(info.root),
        "source": info.source,
        "exists": exists,
        "ready": exists and not missing,
        "missing": missing,
        "error": (
            None
            if exists
            else f"Installation folder not found: {info.root}"
        ),
        **binaries,
        "env_override": os.environ.get("DUALSPHYSICS_ROOT") or None,
    }


class DualSPHysicsRequest(BaseModel):
    root: str | None = None


@router.get("/dualsphysics")
def get_dualsphysics() -> dict[str, Any]:
    """Current DualSPHysics status via the runner's resolution order."""
    return _inspect_root(None)


@router.post("/dualsphysics/validate")
def validate_dualsphysics(request: DualSPHysicsRequest) -> dict[str, Any]:
    """Validate a path WITHOUT saving it (Settings → Validate button)."""
    root = (request.root or "").strip()
    if not root:
        raise HTTPException(
            status_code=400,
            detail="Enter the DualSPHysics installation folder first.",
        )
    return _inspect_root(root)


@router.put("/dualsphysics")
def save_dualsphysics(request: DualSPHysicsRequest) -> dict[str, Any]:
    """Persist the path to scenarios/local.json (machine-local, git-ignored)."""
    root = (request.root or "").strip()

    if not root:
        # Clear the machine-local setting; env/scenario resolution still applies.
        if LOCAL_CONFIG_PATH.exists():
            try:
                data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            data.pop("dualsphysics_root", None)
            LOCAL_CONFIG_PATH.write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
        result = _inspect_root(None)
        result["saved"] = True
        return result

    # Authoritative validation first: same resolver the runner uses.
    check = _inspect_root(root)
    if not check.get("root"):
        raise HTTPException(status_code=400, detail=check.get("error") or "Invalid path.")
    if not check.get("exists"):
        raise HTTPException(
            status_code=400,
            detail=f"Installation folder not found: {check['root']}",
        )

    data: dict[str, Any] = {}
    if LOCAL_CONFIG_PATH.exists():
        try:
            loaded = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data["dualsphysics_root"] = check["root"]
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_PATH.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )

    # Re-inspect so `source` reflects how the runner will actually resolve it.
    result = _inspect_root(None)
    result["saved"] = True
    return result


# ---------------------------------------------------------------------------
# System readiness
# ---------------------------------------------------------------------------


def _component(ok: bool, detail: str | None = None) -> dict[str, Any]:
    return {"ready": ok, "detail": detail}


@router.get("/status")
def system_status() -> dict[str, Any]:
    """Compact readiness report for the UI system-status panel."""

    # Backend (this process) is answering -> ready.
    backend = _component(True, f"FastAPI on this host (pid {os.getpid()})")

    # Dam inventory
    try:
        from app.services.dam_service import load_dam_data

        count = len(load_dam_data().get("features", []))
        dam_data = _component(count > 0, f"{count:,} dam records loaded")
    except Exception as exc:
        dam_data = _component(False, f"{type(exc).__name__}: {exc}")

    # DEM / terrain pipeline (rasterio + the terrain service modules)
    try:
        specs = [
            importlib.util.find_spec("rasterio") is not None,
            importlib.util.find_spec("numpy") is not None,
        ]
        _ensure_scripts_on_path()
        importlib.import_module("app.services.terrain.dem")
        pipeline_ok = all(specs)
        dem_pipeline = _component(
            pipeline_ok,
            "rasterio + terrain services available"
            if pipeline_ok
            else "rasterio/numpy not installed",
        )
    except Exception as exc:
        dem_pipeline = _component(False, f"{type(exc).__name__}: {exc}")

    # Scenario Runner
    try:
        _ensure_scripts_on_path()
        from scenario_runner.config import list_scenarios

        scenarios = list_scenarios()
        runner_ok = RUN_SCENARIO.exists() and bool(scenarios)
        scenario_runner = _component(
            runner_ok,
            f"scripts/run_scenario.py · {len(scenarios)} scenario(s)"
            if runner_ok
            else "run_scenario.py or scenarios missing",
        )
    except Exception as exc:
        scenario_runner = _component(False, f"{type(exc).__name__}: {exc}")

    return {
        "backend": backend,
        "dam_data": dam_data,
        "dem_pipeline": dem_pipeline,
        "scenario_runner": scenario_runner,
        "dualsphysics": _inspect_root(None),
    }
