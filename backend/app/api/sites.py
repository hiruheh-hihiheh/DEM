"""Site onboarding API (DEM upload â†’ validation â†’ prepare â†’ run)."""

from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data" / "terrain"
EXAMPLES_ROOT = REPO_ROOT / "examples" / "main"
SCENARIOS_DIR = REPO_ROOT / "scenarios"

router = APIRouter(prefix="/api/sites", tags=["Sites"])


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Terrain simulation scale written to every scenario's
# terrain.sph_terrain.scale.  1 simulation unit = 1 / TERRAIN_SCALE metres
# (0.02 => 1 sim = 50 m), so metre-based onboarding inputs are converted with
# this factor and mean the same physical size at every site.
TERRAIN_SCALE = 0.02


class StudyAreaRequest(BaseModel):
    lon: float
    lat: float
    width_km: float = 10.0
    height_km: float = 10.0
    clip: bool = True


class PrepareRequest(BaseModel):
    """Onboarding geometry/placement inputs.

    Coordinate units: degrees (WGS84) for lon/lat and dam placement.

    All reservoir/dam dimensions are in METRES; they are converted to the
    simulation unit system with ``TERRAIN_SCALE`` when the case config is
    written (see ``_metres_to_sim``).  ``particle_spacing`` stays in
    simulation units because it must be a fixed lattice step.
    """

    lon: float
    lat: float
    width_km: float = 10.0
    height_km: float = 10.0

    # --- Dam placement & orientation (optional) -------------------------
    # The study area is still clipped about (lon, lat); the dam anchor drives
    # conditioning / flow / domain / SPH terrain.  Defaults to (lon, lat).
    dam_lon: Optional[float] = None
    dam_lat: Optional[float] = None
    # Flow axis through the dam ("x" or "y") and which side is upstream
    # (+1 or -1).  None lets the generator infer the axis from the terrain.
    # A sign is only meaningful relative to a fixed axis, so supplying
    # ``dam_upstream_sign`` without an explicit ``dam_axis`` is rejected.
    dam_axis: Optional[str] = None
    dam_upstream_sign: Optional[int] = None

    # --- Reservoir & dam geometry, in METRES ----------------------------
    reservoir_water_depth_m: float = 33.0
    reservoir_freeboard_m: float = 2.5
    dam_crest_height_m: float = 40.0
    dam_thickness_m: float = 25.0
    dam_embedment_m: float = 5.0
    reservoir_length_m: float = 150.0
    fluid_bed_clearance_m: float = 8.0
    breach_width_m: float = 50.0
    breach_time: float = 0.25
    simulation_time: float = 6.0
    particle_spacing: float = 0.1
    # End a reservoir segment at the waterline where its bed rises above the
    # common surface (per-segment clip) rather than dropping the whole segment.
    # Off by default: turning it on is an explicit opt-in, because it changes
    # the reservoir geometry.
    reservoir_clip_to_wet_reach: bool = False
    case_name: Optional[str] = None


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


def _validate_lon_lat(lon: float, lat: float, lon_name: str, lat_name: str) -> None:
    """Range-check WGS84 degrees, raising HTTP 400 with a clear message.

    ``lon_name``/``lat_name`` are the request field names, so the error points
    at exactly which coordinate (and which source, study area vs dam anchor) is
    out of range.
    """
    if not (-180.0 <= lon <= 180.0):
        raise HTTPException(
            status_code=400,
            detail=f"{lon_name} must be between -180 and 180 (got {lon})",
        )
    if not (-90.0 <= lat <= 90.0):
        raise HTTPException(
            status_code=400,
            detail=f"{lat_name} must be between -90 and 90 (got {lat})",
        )


def _metres_to_sim(value: float) -> float:
    """Convert a length in metres to terrain simulation units."""
    return float(value) * TERRAIN_SCALE


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


TEMPLATE_CASE_NAME = "HADR_TerrainChouldari"

# Committed example outputs that ship inside the Chouldari template. They are
# never referenced by a generated case config, so they are dropped only when a
# brand-new case directory is created (existing case dirs are left untouched).
_TEMPLATE_STALE_FILES = (
    f"{TEMPLATE_CASE_NAME}_Def.xml",
    f"{TEMPLATE_CASE_NAME}_runner_Def.xml",
    f"{TEMPLATE_CASE_NAME}_gate_motion.txt",
    f"{TEMPLATE_CASE_NAME}_geometry_preview.png",
    f"{TEMPLATE_CASE_NAME}_geometry_preview.vtk",
    f"{TEMPLATE_CASE_NAME}_geometry_preview_metadata.json",
    "chouldari_sph_terrain.stl",
    "terrain_case_summary.json",
)

# The template also ships a complete example solver-output tree and Python byte
# caches. These contain Chouldari runtime paths (log, particles, bi4/vtk) and
# must never be inherited by a generated case.
_TEMPLATE_STALE_DIRS = (
    f"{TEMPLATE_CASE_NAME}_out",
    "__pycache__",
)


def _is_template_stale(name: str) -> bool:
    return name in _TEMPLATE_STALE_FILES or name in _TEMPLATE_STALE_DIRS


def _ignore_template_artifacts(dir_path: str, names: list[str]) -> set[str]:
    """copytree ignore hook: skip template-only example outputs."""
    return {name for name in names if _is_template_stale(name)}


def _scrub_template_outputs(dst_dir: Path) -> None:
    for name in _TEMPLATE_STALE_FILES:
        stale = dst_dir / name
        if stale.exists():
            stale.unlink()
    for name in _TEMPLATE_STALE_DIRS:
        stale = dst_dir / name
        if stale.is_dir():
            shutil.rmtree(stale)


def _safe_case_dir(src_dir: Path, dst_dir: Path) -> None:
    """Create a new case dir from the template, or top up a partial one.

    Never deletes an existing case directory: re-preparation must preserve
    simulation outputs and results. Only files that are missing are copied
    from the template; existing files are left untouched. When a genuinely
    new case is created, unrelated template example outputs (including the
    Chouldari solver-output tree and terrain STL) are excluded so the case
    contains only site-specific artifacts.
    """
    if not dst_dir.exists():
        shutil.copytree(src_dir, dst_dir, ignore=_ignore_template_artifacts)
        _scrub_template_outputs(dst_dir)
        return
    for src in src_dir.rglob("*"):
        rel = src.relative_to(src_dir)
        if any(_is_template_stale(part) for part in rel.parts):
            continue
        dst = dst_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


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
    case_name_full = f"HADR_Terrain{case_name}"

    # --- Dam placement & orientation controls ---------------------------
    dam_axis = req.dam_axis
    if dam_axis is not None:
        dam_axis = str(dam_axis).lower()
        if dam_axis not in ("x", "y"):
            raise HTTPException(status_code=400, detail="dam_axis must be 'x' or 'y'")
    dam_sign = req.dam_upstream_sign
    if dam_sign is not None:
        dam_sign = int(dam_sign)
        if dam_sign not in (1, -1):
            raise HTTPException(status_code=400, detail="dam_upstream_sign must be +1 or -1")
        if dam_axis is None:
            # +1/-1 selects a direction ALONG a flow axis; with the axis left
            # automatic the sign has no defined meaning and would be silently
            # ignored by the generator.  Require an explicit axis instead.
            raise HTTPException(
                status_code=400,
                detail=(
                    "dam_upstream_sign requires an explicit dam_axis ('x' or 'y'); "
                    "the upstream side is undefined while the flow axis is inferred"
                ),
            )
    # --- Coordinate validation (runs before any case config is created) --
    # The study-area centre is always validated; the dam anchor, when given,
    # must be a complete, valid pair. Both dam_lon and dam_lat are required
    # together, or both omitted to fall back to the (validated) study area.
    _validate_lon_lat(float(req.lon), float(req.lat), "lon", "lat")
    if (req.dam_lon is None) != (req.dam_lat is None):
        raise HTTPException(
            status_code=400,
            detail=(
                "dam_lon and dam_lat must be provided together, or both left "
                "empty to use the study-area center"
            ),
        )
    if req.dam_lon is not None:
        dam_lon = float(req.dam_lon)
        dam_lat = float(req.dam_lat)
        _validate_lon_lat(dam_lon, dam_lat, "dam_lon", "dam_lat")
    else:
        dam_lon = float(req.lon)
        dam_lat = float(req.lat)

    src_dir = EXAMPLES_ROOT / "HADR_TerrainChouldari"
    dst_dir = EXAMPLES_ROOT / case_name_full
    if dst_dir.resolve() == src_dir.resolve():
        raise HTTPException(
            status_code=400,
            detail="Refusing to modify the original HADR_TerrainChouldari example. Choose a different site.",
        )
    _safe_case_dir(src_dir, dst_dir)
    gen_path = dst_dir / "generate_terrain_case.py"
    cfg_path = dst_dir / "terrain_case_config.json"

    if not cfg_path.exists():
        raise HTTPException(status_code=500, detail=f"Case template is missing {cfg_path.name}")

    cfg_data = json.loads(cfg_path.read_text())
    # Site-specific names so the generated definition/runner files match the
    # case_name written by the generator (avoids Chouldari leftovers).
    cfg_data["case_name"] = case_name_full
    cfg_data["definition_name"] = f"{case_name_full}_Def.xml"
    cfg_data["runner_definition_name"] = f"{case_name_full}_runner_Def.xml"
    cfg_data["summary_name"] = "terrain_case_summary.json"
    cfg_data["terrain_npz"] = str(sph_npz.relative_to(REPO_ROOT))
    cfg_data["terrain_stl"] = str(sph_stl.relative_to(REPO_ROOT))
    # Geometry in metres -> simulation units (constant physical meaning).
    cfg_data["particle_spacing"] = float(req.particle_spacing)
    cfg_data["simulation_time"] = float(req.simulation_time)
    cfg_data["reservoir_water_depth"] = _metres_to_sim(req.reservoir_water_depth_m)
    cfg_data["reservoir_freeboard"] = _metres_to_sim(req.reservoir_freeboard_m)
    cfg_data["fluid_bed_clearance"] = _metres_to_sim(req.fluid_bed_clearance_m)
    cfg_data["breach_width"] = _metres_to_sim(req.breach_width_m)
    cfg_data["breach_time"] = float(req.breach_time)
    cfg_data["dam_crest_height"] = _metres_to_sim(req.dam_crest_height_m)
    cfg_data["dam_thickness"] = _metres_to_sim(req.dam_thickness_m)
    cfg_data["dam_embedment"] = _metres_to_sim(req.dam_embedment_m)
    cfg_data["reservoir_length"] = _metres_to_sim(req.reservoir_length_m)
    cfg_data["reservoir_clip_to_wet_reach"] = bool(req.reservoir_clip_to_wet_reach)
    # Explicit dam placement/orientation.  None lets the generator infer.
    cfg_data["force_upstream_axis"] = dam_axis
    cfg_data["force_upstream_sign"] = dam_sign
    cfg_path.write_text(json.dumps(cfg_data, indent=2))

    scenario = {
        "name": site_slug,
        "display_name": meta.get("site_name") or site_slug,
        "description": f"Auto-generated site from uploaded DEM: {site_slug}",
        "longitude": float(req.lon),
        "latitude": float(req.lat),
        # Dam anchor for conditioning / flow / domain / SPH terrain (defaults
        # to the study-area center above).
        "dam_longitude": dam_lon,
        "dam_latitude": dam_lat,
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


def _stage_outputs(site_slug: str) -> dict[str, list[Path]]:
    """Resolve the artifacts each pipeline stage is expected to produce.

    Uses the Scenario Runner's own planner so the checked paths always match
    what the runner writes (including default output naming).
    """
    from scenario_runner.config import load_scenario
    from scenario_runner.steps import build_plan, select_steps

    scenario = load_scenario(site_slug)
    run_dir = scenario.runs_root / "pending"
    plan = build_plan(scenario, run_dir)

    def collect(stage: str) -> list[Path]:
        paths: list[Path] = []
        for step in select_steps(plan, [stage]):
            paths.extend(step.outputs)
        return paths

    return {"terrain": collect("terrain"), "sph": collect("sph")}


def _missing(paths: list[Path]) -> list[str]:
    return [str(p) for p in paths if not p.exists()]


def _case_summary(site_slug: str) -> dict | None:
    """Load the generated case summary for a scenario, if it exists.

    The generator writes this even when validation fails, so it carries the
    specific failed checks (and any terrain limitation) behind an opaque
    non-zero pipeline exit.
    """
    try:
        from scenario_runner.config import load_scenario
        from scenario_runner.steps import case_output_paths

        scenario = load_scenario(site_slug)
        summary_path = case_output_paths(scenario)["summary"]
    except Exception:
        return None
    if not summary_path.exists():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _attach_case_validation(result: dict, site_slug: str) -> dict:
    """Fold physical-validation detail from the case summary into a run result.

    A bare "SPH generation failed" hides why the case was rejected.  Surface the
    failed checks, the limitations and the terrain diagnostic so the frontend
    can explain the physics and the missing data.
    """
    summary = _case_summary(site_slug)
    if not summary:
        return result

    validation = summary.get("validation")
    if isinstance(validation, dict):
        result["validation"] = validation
        errors = [str(e) for e in validation.get("errors", []) if str(e)]
        result["validation_errors"] = errors
    limitations = [str(x) for x in summary.get("limitations", []) if str(x)]
    if limitations:
        result["limitations"] = limitations
    diagnostic = summary.get("terrain_diagnostic")
    if isinstance(diagnostic, dict):
        result["terrain_diagnostic"] = diagnostic

    errors = result.get("validation_errors") or []
    if errors:
        checks = None
        if isinstance(validation, dict):
            checks = validation.get("checks_passed")
        header = "Case geometry failed physical validation"
        if isinstance(checks, int):
            header += f" ({checks} checks passed, {len(errors)} failed)"
        result["error"] = header + ":\n" + "\n".join(f"- {e}" for e in errors)
    return result


@router.post("/{site_slug}/run-pipeline")
def run_pipeline(site_slug: str, dry_run: bool = False):
    ensure_scripts_on_path()
    scen_path = SCENARIOS_DIR / f"{site_slug}.json"
    if not scen_path.exists():
        raise HTTPException(status_code=400, detail="Scenario not prepared")
    import subprocess

    def run_step(step: str) -> tuple[int, str]:
        cmd = [sys.executable, str(REPO_ROOT / "scripts" / "run_scenario.py"), site_slug, "--step", step]
        p = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = p.stdout or ""
        if p.stderr:
            out += ("\n" if out else "") + p.stderr
        return p.returncode, out

    try:
        if dry_run:
            p = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "run_scenario.py"), site_slug, "--dry-run"],
                cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            output = (p.stdout or "") + ("\n" + (p.stderr or "") if p.stderr else "")
            return {"ok": p.returncode == 0, "stage": "dry-run", "returncode": p.returncode,
                    "stdout": output[-16000:] if output else "", "stderr": ""}

        expected = _stage_outputs(site_slug)

        # --- Stage 1: terrain (must succeed before SPH starts) --------------
        rc, out = run_step("terrain")
        if rc != 0:
            return {"ok": False, "stage": "terrain", "returncode": rc,
                    "stdout": out[-16000:] if out else "", "stderr": "",
                    "error": f"Terrain processing failed (exit code {rc})"}
        missing = _missing(expected["terrain"])
        if missing:
            return {"ok": False, "stage": "terrain", "returncode": rc,
                    "stdout": out[-16000:] if out else "", "stderr": "",
                    "error": "Terrain finished but required outputs are missing: " + ", ".join(missing)}

        # --- Stage 2: SPH case generation -----------------------------------
        rc2, out2 = run_step("sph")
        combined = (out + "\n" + out2) if out2 else out
        if rc2 != 0:
            return _attach_case_validation({
                "ok": False, "stage": "sph", "returncode": rc2,
                "stdout": combined[-16000:] if combined else "", "stderr": "",
                "error": f"SPH generation failed (exit code {rc2})",
            }, site_slug)
        missing2 = _missing(expected["sph"])
        if missing2:
            return _attach_case_validation({
                "ok": False, "stage": "sph", "returncode": rc2,
                "stdout": combined[-16000:] if combined else "", "stderr": "",
                "error": "SPH finished but required case outputs are missing: " + ", ".join(missing2),
            }, site_slug)
        return {"ok": True, "stage": "complete", "returncode": 0,
                "stdout": combined[-16000:] if combined else "", "stderr": ""}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
