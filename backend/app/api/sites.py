"""Site onboarding API (DEM upload → validation → prepare → run)."""

from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data" / "terrain"
EXAMPLES_ROOT = REPO_ROOT / "examples" / "main"
SCENARIOS_DIR = REPO_ROOT / "scenarios"

router = APIRouter(prefix="/api/sites", tags=["Sites"])


class StudyAreaRequest(BaseModel):
    lon: float
    lat: float
    width_km: float = 10.0
    height_km: float = 10.0
    clip: bool = True


class PrepareRequest(BaseModel):
    lon: float
    lat: float
    width_km: float = 10.0
    height_km: float = 10.0
    reservoir_water_depth: float = 0.66
    breach_width: float = 1.0
    breach_time: float = 0.25
    simulation_time: float = 6.0
    particle_spacing: float = 0.1
    fluid_bed_clearance: float = 0.16
    dam_crest_height: float = 0.8
    dam_thickness: float = 0.5
    case_name: Optional[str] = None


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def ensure_scripts_on_path() -> None:
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    backend = str(REPO_ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)


def _slugify(text: str) -> str:
    text = str(text).lower().strip()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = re.sub(r"[_-]{2,}", "_", text).strip("_")
    return text or f"site_{uuid.uuid4().hex[:6]}"


@router.post("/upload-dem")
async def upload_dem(
    file: UploadFile = File(...),
    site_slug: Optional[str] = Form(None),
    site_name: Optional[str] = Form(None),
):
    ensure_scripts_on_path()
    name = (file.filename or "dem.tif").lower()
    if not name.endswith((".tif", ".tiff")):
        raise HTTPException(status_code=400, detail="Please upload a GeoTIFF (.tif/.tiff)")

    if not site_slug:
        site_slug = _slugify(site_name or Path(file.filename or "site").stem)
    if not SLUG_RE.match(site_slug):
        raise HTTPException(status_code=400, detail="Invalid site_slug")

    site_dir = DATA_ROOT / site_slug
    for d in (site_dir / "input", site_dir / "processed", site_dir / "sph", site_dir / "metadata"):
        d.mkdir(parents=True, exist_ok=True)

    dem_path = site_dir / "input" / (Path(file.filename or f"{site_slug}.tif").name)
    if dem_path.exists():
        dem_path = site_dir / "input" / f"{site_slug}_{uuid.uuid4().hex[:6]}_{Path(file.filename).name}"
    dem_path.write_bytes(await file.read())

    try:
        from app.services.terrain.dem import DEMReader
        with DEMReader(dem_path) as dem:
            info = dem.info
            stats = dem.statistics()
    except Exception as e:
        dem_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"DEM validation failed: {e}")

    meta = {
        "site_slug": site_slug,
        "site_name": site_name or site_slug,
        "original_filename": file.filename,
        "dem_path": str(dem_path.relative_to(REPO_ROOT)),
        "info": {
            "crs": info.crs,
            "epsg": info.epsg,
            "width": info.width,
            "height": info.height,
            "count": info.count,
            "dtype": info.dtype,
            "bounds": list(info.bounds),
            "resolution": list(info.resolution),
            "nodata": info.nodata,
        },
        "stats": {
            "count": stats.count,
            "minimum": stats.minimum,
            "maximum": stats.maximum,
            "mean": stats.mean,
            "median": stats.median,
            "nodata": stats.nodata,
        },
    }
    (site_dir / "metadata" / "upload.json").write_text(json.dumps(meta, indent=2))
    return {"ok": True, "site_slug": site_slug, "meta": meta}


@router.get("/{site_slug}/validate")
def validate_site(site_slug: str):
    ensure_scripts_on_path()
    meta_path = DATA_ROOT / site_slug / "metadata" / "upload.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Site not found")
    return {"ok": True, "meta": json.loads(meta_path.read_text())}


@router.post("/{site_slug}/preview")
def generate_preview(site_slug: str, req: StudyAreaRequest):
    ensure_scripts_on_path()
    meta_path = DATA_ROOT / site_slug / "metadata" / "upload.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Site not found")
    meta = json.loads(meta_path.read_text())
    dem_path = REPO_ROOT / meta["dem_path"]
    proc = DATA_ROOT / site_slug / "processed"
    proc.mkdir(parents=True, exist_ok=True)
    clipped = proc / f"{site_slug}_clip.tif"
    preview = proc / f"{site_slug}_preview.png"
    try:
        from app.services.terrain.clip import TerrainClipper
        clipper = TerrainClipper(dem_path)
        info = clipper.clip(req.lon, req.lat, width_km=req.width_km, height_km=req.height_km, output_path=clipped)
        try:
            from app.services.terrain.preview import TerrainPreviewer
            TerrainPreviewer(clipped).analyze_and_plot(req.lon, req.lat, output_path=preview)
        except Exception:
            pass
        return {
            "ok": True,
            "clipped": str(clipped.relative_to(REPO_ROOT)),
            "preview": str(preview.relative_to(REPO_ROOT)) if preview.exists() else None,
            "clip_info": {"bounds": list(info.bounds), "width": info.width, "height": info.height},
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{site_slug}/prepare")
def prepare_site(site_slug: str, req: PrepareRequest):
    ensure_scripts_on_path()
    meta_path = DATA_ROOT / site_slug / "metadata" / "upload.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Site not found")
    meta = json.loads(meta_path.read_text())
    dem_path = REPO_ROOT / meta["dem_path"]
    site_dir = DATA_ROOT / site_slug
    proc = site_dir / "processed"
    sph = site_dir / "sph"
    for d in (proc, sph):
        d.mkdir(parents=True, exist_ok=True)
    clipped = proc / f"{site_slug}_clip.tif"
    sph_npz = sph / f"{site_slug}_sph_terrain.npz"
    sph_stl = sph / f"{site_slug}_sph_terrain.stl"

    case_name = req.case_name or "".join(w.capitalize() for w in re.split(r"[-_]+", site_slug)) or "Site"
    safe = re.sub(r"[^A-Za-z0-9_]", "", case_name)
    case_name = safe or "Site"

    src_dir = EXAMPLES_ROOT / "HADR_TerrainChouldari"
    dst_dir = EXAMPLES_ROOT / f"HADR_Terrain{case_name}"
    if not dst_dir.exists():
        shutil.copytree(src_dir, dst_dir)
    gen_path = dst_dir / "generate_terrain_case.py"
    cfg_path = dst_dir / "terrain_case_config.json"

    cfg_data = json.loads(cfg_path.read_text())
    cfg_data["case_name"] = f"HADR_Terrain{case_name}"
    cfg_data["terrain_npz"] = str(sph_npz.relative_to(REPO_ROOT))
    cfg_data["terrain_stl"] = str(sph_stl.relative_to(REPO_ROOT))
    cfg_data["particle_spacing"] = float(req.particle_spacing)
    cfg_data["simulation_time"] = float(req.simulation_time)
    cfg_data["reservoir_water_depth"] = float(req.reservoir_water_depth)
    cfg_data["fluid_bed_clearance"] = float(req.fluid_bed_clearance)
    cfg_data["breach_width"] = float(req.breach_width)
    cfg_data["breach_time"] = float(req.breach_time)
    cfg_data["dam_crest_height"] = float(req.dam_crest_height)
    cfg_data["dam_thickness"] = float(req.dam_thickness)
    cfg_path.write_text(json.dumps(cfg_data, indent=2))

    scenario = {
        "name": site_slug,
        "display_name": meta.get("site_name") or site_slug,
        "description": f"Auto-generated site from uploaded DEM: {site_slug}",
        "longitude": float(req.lon),
        "latitude": float(req.lat),
        "data_root": f"data/terrain/{site_slug}",
        "runs_root": f"simulations/{site_slug}/runs",
        "inputs": {"dem": str(clipped.relative_to(REPO_ROOT))},
        "clip": {
            "source_dem": str(dem_path.relative_to(REPO_ROOT)),
            "width_km": float(req.width_km),
            "height_km": float(req.height_km),
        },
        "terrain": {
            "condition": {"max_fill_depth": 5.0, "preview": True},
            "flow": {"preview": True},
            "domain": {"corridor_width_km": 1.0, "preview": True},
            "sph_terrain": {"target_resolution_m": 30.0, "scale": 0.02, "csv": True, "preview": True},
            "mesh": {"max_edge_m": 1.5, "preview": True},
        },
        "case": {
            "generator": str(gen_path.relative_to(REPO_ROOT)),
            "config": str(cfg_path.relative_to(REPO_ROOT)),
            "overrides": {},
            "geometry_preview": True,
        },
        "simulation": {"enabled": True},
        "postprocess": [
            {
                "name": "Particle outflow diagnostic",
                "script": str((dst_dir / "analyze_particle_outflow.py").relative_to(REPO_ROOT)),
                "args": ["--out-dir", "{sim_run_dir}"],
            }
        ],
    }
    scen_path = SCENARIOS_DIR / f"{site_slug}.json"
    scen_path.write_text(json.dumps(scenario, indent=2))
    return {"ok": True, "scenario": str(scen_path.relative_to(REPO_ROOT)), "case_dir": str(dst_dir.relative_to(REPO_ROOT))}


@router.post("/{site_slug}/run-pipeline")
def run_pipeline(site_slug: str, dry_run: bool = False):
    ensure_scripts_on_path()
    scen_path = SCENARIOS_DIR / f"{site_slug}.json"
    if not scen_path.exists():
        raise HTTPException(status_code=400, detail="Scenario not prepared")
    import subprocess
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "run_scenario.py"), site_slug]
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--step")
        cmd.append("sph")
    try:
        p = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        return {"ok": p.returncode == 0, "returncode": p.returncode, "stdout": p.stdout[-12000:] if p.stdout else "", "stderr": p.stderr[-8000:] if p.stderr else ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
