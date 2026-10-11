"""Site onboarding API (DEM upload â†’ validation â†’ prepare â†’ run)."""

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
        # Run terrain + sph stages so dependencies are generated in order
        cmd.append("--step")
        cmd.append("terrain")
        cmd.append("--step")
        cmd.append("sph")
    try:
        p = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        output = (p.stdout or "") + ("\n" + (p.stderr or "") if p.stderr else "")
        return {"ok": p.returncode == 0, "returncode": p.returncode, "stdout": output[-16000:] if output else "", "stderr": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
