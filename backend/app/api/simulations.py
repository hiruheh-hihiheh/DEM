from app.services.dam_service import get_dam_by_id
from pathlib import Path
import os

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from app.schemas.simulation import (
    CreateJobRequest,
    SPHSimulationRequest,
    SPHSimulationResponse,
)

from app.services.sph.scenario import build_scenario
from app.services.sph.xml_generator import generate_xml
from app.services.sph.runner import SPHRunner
from app.services.sph.job_manager import (
    JOBS_DIR,
    JobConflict,
    JobError,
    get_manager,
    scenario_catalog,
)


router = APIRouter(
    prefix="/api/simulations",
    tags=["Simulations"],
)


@router.post(
    "/sph",
    response_model=SPHSimulationResponse,
)
def run_sph_simulation(
    request: SPHSimulationRequest,
):
    dualsph_root = os.getenv(
        "DUALSPHYSICS_ROOT"
    )

    if not dualsph_root:
        raise HTTPException(
            status_code=500,
            detail=(
                "DUALSPHYSICS_ROOT environment "
                "variable is not configured."
            ),
        )

    try:
        dam = get_dam_by_id(request.dam_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Dam not found: {request.dam_id}",
        )

    scenario = build_scenario(
        dam=dam,
        scenario=request.scenario,
        reservoir_level=request.reservoir_level,
        breach_width=request.breach_width,
        breach_time=request.breach_time,
        simulation_time=request.simulation_time,
        particle_spacing=request.particle_spacing,
    )

    project_root = Path(__file__).resolve().parents[3]

    runs_dir = (
        project_root
        / "simulations"
        / "runs"
    )

    config_dir = (
        project_root
        / "simulations"
        / "templates"
    )

    config_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    xml_path = (
        config_dir
        / "HADR_DamBreak_Def.xml"
    )

    try:
        generate_xml(
            scenario,
            xml_path,
        )

        runner = SPHRunner(
            Path(dualsph_root)
        )

        result = runner.run(
            xml_path,
            runs_dir,
        )

        return SPHSimulationResponse(
            simulation_id=result["simulation_id"],
            status="completed",
            dam_id=request.dam_id,
            scenario=request.scenario,
            output_directory=result[
                "output_directory"
            ],
            message=(
                "SPH simulation completed successfully."
            ),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# Scenario catalogue + job API (backed by scripts/run_scenario.py)
# ---------------------------------------------------------------------------

_RESULT_FILES = {
    "manifest.json": "application/json",
    "frames.bin": "application/octet-stream",
    "terrain.bin": "application/octet-stream",
    "flood.geojson": "application/geo+json",
    "analytics.csv": "text/csv",
}


@router.get("/scenarios")
def list_scenarios():
    """Scenarios the runner can execute, with their effective parameters."""
    try:
        return {"scenarios": scenario_catalog()}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot read scenario catalogue: {exc}",
        ) from exc


@router.post("/jobs", status_code=202)
def create_job(request: CreateJobRequest):
    """Start a simulation job (runs the existing scenario runner)."""
    manager = get_manager()
    try:
        job = manager.create_simulation_job(
            request.scenario,
            request.parameters.model_dump(),
        )
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "job_id": job["id"],
        "status": job["status"],
        "scenario": job["scenario"],
        "validated_config": job["validated_config"],
        "message": "Simulation job started.",
    }


@router.get("/jobs")
def list_jobs():
    """Simulation history: web jobs plus runs launched from the CLI."""
    manager = get_manager()
    jobs = manager.list_jobs()
    active = [
        j["id"] for j in jobs if j.get("status") in ("queued", "running")
    ]
    return {"jobs": jobs, "active_job_id": active[0] if active else None}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = get_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job


@router.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: str, offset: int = 0):
    manager = get_manager()
    if manager.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    lines, new_offset = manager.read_log(job_id, offset)
    return {"lines": lines, "offset": new_offset}


@router.post("/jobs/{job_id}/process")
def process_job_result(job_id: str):
    """Build/refresh the browser result package for a completed run."""
    manager = get_manager()
    try:
        manifest = manager.process_runner_run(job_id)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "job_id": job_id,
        "available": True,
        "metrics": manifest.get("metrics"),
    }


@router.get("/jobs/{job_id}/result")
def get_job_result(job_id: str):
    manager = get_manager()
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    manifest = JOBS_DIR / job_id / "result" / "manifest.json"
    if not manifest.exists():
        raise HTTPException(
            status_code=404,
            detail="No processed result for this simulation yet.",
        )
    import json as _json

    return _json.loads(manifest.read_text(encoding="utf-8"))


@router.get("/jobs/{job_id}/result/{filename}")
def get_job_result_file(job_id: str, filename: str):
    if filename not in _RESULT_FILES:
        raise HTTPException(status_code=404, detail="Unknown result file.")
    path = JOBS_DIR / job_id / "result" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not available.")
    return FileResponse(
        path,
        media_type=_RESULT_FILES[filename],
        filename=f"{job_id}_{filename}",
    )


@router.get("/jobs/{job_id}/export")
def export_job(job_id: str):
    """Download a ZIP with the full result package + metadata + logs."""
    import json as _json
    import zipfile

    manager = get_manager()
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    result_dir = JOBS_DIR / job_id / "result"
    if not result_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="Nothing to export for this simulation.",
        )

    bundle = JOBS_DIR / job_id / "export.zip"
    bundle.write_bytes(b"")  # truncate stale bundles
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(result_dir.iterdir()):
            if path.is_file():
                archive.write(path, f"result/{path.name}")
        archive.writestr(
            "simulation.json",
            _json.dumps(job, indent=2, default=str),
        )
        lines, _ = manager.read_log(job_id, 0)
        if lines:
            archive.writestr("run.log", "\n".join(lines) + "\n")
    return FileResponse(
        bundle,
        media_type="application/zip",
        filename=f"{job_id}_export.zip",
    )


@router.post("/imports", status_code=202)
async def import_package(file: UploadFile = File(...)):
    """Import an existing simulation output package (ZIP of PartVTK output)."""
    name = (file.filename or "upload.zip").lower()
    if not name.endswith(".zip"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a .zip package containing "
            "particles/PartFluid_*.vtk.",
        )

    import uuid

    manager = get_manager()
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    staging = JOBS_DIR / f"upload_{uuid.uuid4().hex[:8]}.zip"
    payload = await file.read()
    if not payload:
        staging.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The file is empty.")
    staging.write_bytes(payload)

    try:
        job = manager.create_import_job(staging, file.filename or "upload.zip")
    except JobError as exc:
        staging.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "job_id": job["id"],
        "status": job["status"],
        "message": "Import started.",
    }