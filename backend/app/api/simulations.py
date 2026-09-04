from app.services.dam_service import get_dam_by_id
from pathlib import Path
import os

from fastapi import APIRouter, HTTPException

from app.schemas.simulation import (
    SPHSimulationRequest,
    SPHSimulationResponse,
)

from app.services.sph.scenario import build_scenario
from app.services.sph.xml_generator import generate_xml
from app.services.sph.runner import SPHRunner


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
        dam_id=request.dam_id,
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