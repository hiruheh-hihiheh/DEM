"""Scenario configuration loading and validation.

Scenario files live in ``scenarios/<name>.json``. ``scenarios/local.json`` is
machine-local configuration (git-ignored), not a scenario. Every relative path
in a scenario file is resolved against the repository root.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "scenarios"
LOCAL_CONFIG_PATH = SCENARIOS_DIR / "local.json"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Defaults mirror the CLI defaults of backend/app/services/terrain/.
TERRAIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "condition": {"max_fill_depth": 5.0, "preview": True},
    "flow": {"preview": True},
    "domain": {"corridor_width_km": 1.0, "preview": True},
    "sph_terrain": {"target_resolution_m": 30.0, "scale": 0.02,
                    "csv": True, "preview": True},
    "mesh": {"max_edge_m": 1.5, "preview": True},
}

# Inclusive (min, max) bounds for terrain numeric parameters.
TERRAIN_RANGES: dict[tuple[str, str], tuple[float, float]] = {
    ("condition", "max_fill_depth"): (1e-6, 1000.0),
    ("domain", "corridor_width_km"): (1e-6, 100.0),
    ("sph_terrain", "target_resolution_m"): (1e-3, 10000.0),
    ("sph_terrain", "scale"): (1e-6, 10.0),
    ("mesh", "max_edge_m"): (1e-6, 100.0),
}

# Inclusive (min, max) bounds for case-config overrides (keys must also exist
# in the base case configuration file).
CASE_OVERRIDE_RANGES: dict[str, tuple[float, float]] = {
    "particle_spacing": (1e-4, 10.0),
    "simulation_time": (1e-3, 86400.0),
    "time_out": (1e-4, 3600.0),
    "domain_margin_xy": (0.0, 10000.0),
    "domain_margin_z_bottom": (0.0, 10000.0),
    "domain_margin_z_top": (0.0, 10000.0),
    "dam_search_radius": (1e-3, 10000.0),
    "dam_thickness": (1e-3, 10000.0),
    "dam_crest_height": (1e-3, 10000.0),
    "dam_embedment": (0.0, 10000.0),
    "dam_min_span": (1e-3, 10000.0),
    "dam_max_span": (1e-3, 10000.0),
    "dam_segment_length": (1e-3, 10000.0),
    "dam_valley_threshold": (0.0, 10000.0),
    "reservoir_length": (1e-3, 100000.0),
    "reservoir_water_depth": (0.0, 10000.0),
    "reservoir_freeboard": (0.0, 10000.0),
    "reservoir_segments": (1, 100000),
    "fluid_bed_clearance": (0.0, 10000.0),
    "breach_width": (0.0, 10000.0),
    "breach_time": (0.0, 86400.0),
    "breach_lift_duration": (1e-4, 86400.0),
    "breach_lift_distance": (0.0, 10000.0),
    "breach_motion_steps": (1, 1000000),
}

_ALLOWED_TOP_LEVEL = {
    "name", "display_name", "description", "dam_id", "longitude", "latitude",
    "data_root", "runs_root", "inputs", "clip", "terrain", "case",
    "simulation", "postprocess", "dualsphysics_root",
}

_ALLOWED_TERRAIN_OUTPUTS = {
    "conditioned", "conditioned_preview",
    "flow_tif", "flow_preview",
    "domain_tif", "domain_preview",
    "sph_npz", "sph_csv", "sph_preview",
    "stl", "mesh_preview",
}


class ScenarioError(Exception):
    """Raised for unreadable or invalid scenario configuration."""


def resolve_path(value: str) -> Path:
    """Resolve a scenario path against the repository root."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p.resolve()


def rel(path: Path | str) -> str:
    """Repo-relative POSIX path for display (absolute outside the repo)."""
    p = Path(path)
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except (ValueError, OSError):
        return str(p)


def ensure_backend_on_path() -> None:
    backend = str(REPO_ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)


def find_dam(dam_id: str) -> dict:
    """Look up a dam feature in data/dams/raw/dam.geojson via the backend service."""
    ensure_backend_on_path()
    try:
        from app.services.dam_service import get_dams
        inventory = get_dams()
    except Exception as exc:  # missing file, parse error, import error
        raise ScenarioError(
            f"Cannot load the dam inventory (data/dams/raw/dam.geojson): {exc}"
        ) from exc

    for feature in inventory.get("features", []):
        if str(feature.get("id")) == str(dam_id):
            return feature

    raise ScenarioError(
        f"Dam '{dam_id}' not found in data/dams/raw/dam.geojson "
        f"({len(inventory.get('features', []))} dams checked). "
        "Use the 'id' value shown by GET /api/dams."
    )


@dataclass
class Scenario:
    """Validated scenario definition with all paths resolved."""

    name: str
    display_name: str
    description: str

    dam_id: str | None
    dam_name: str | None
    river: str | None
    longitude: float
    latitude: float

    data_root: Path
    runs_root: Path
    inputs: dict[str, Path]

    clip: dict[str, Any] | None
    terrain: dict[str, Any]
    outputs: dict[str, Path]

    case: dict[str, Any]
    simulation: dict[str, Any]
    postprocess: list[dict[str, Any]]

    dualsphysics_root: Path | None
    path: Path

    @property
    def manifest_path(self) -> Path:
        return self.data_root / "metadata" / "manifest.json"

    @property
    def latest_run_pointer(self) -> Path:
        return self.runs_root.parent / "latest.json"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_local_config() -> dict[str, Any]:
    """Read scenarios/local.json (machine-local, git-ignored) if present."""
    if not LOCAL_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ScenarioError(
            f"Cannot parse {rel(LOCAL_CONFIG_PATH)}: {exc}. "
            "Fix or delete this file (it is optional and never committed)."
        ) from exc
    if not isinstance(data, dict):
        raise ScenarioError(f"{rel(LOCAL_CONFIG_PATH)} must contain a JSON object.")
    return data


def list_scenarios() -> list[dict[str, Any]]:
    """Light listing of scenarios/*.json (no validation; broken files included)."""
    items: list[dict[str, Any]] = []
    if not SCENARIOS_DIR.is_dir():
        return items
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        stem = path.stem
        if stem == "local" or stem.startswith("local.") or stem.endswith(".example"):
            continue
        entry: dict[str, Any] = {"name": stem, "display_name": stem, "description": ""}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                entry["display_name"] = str(data.get("display_name") or stem)
                entry["description"] = str(data.get("description") or "")
        except Exception:
            entry["description"] = "(unreadable scenario file)"
        items.append(entry)
    return items


def load_scenario(name: str) -> Scenario:
    """Load and fully validate scenarios/<name>.json."""
    if not SLUG_RE.match(name or ""):
        raise ScenarioError(
            f"Invalid scenario name '{name}'. Expected letters, digits, '-' or '_' "
            "(lowercase)."
        )

    path = SCENARIOS_DIR / f"{name}.json"
    if not path.exists():
        available = [s["name"] for s in list_scenarios()]
        listing = ", ".join(available) if available else "(none found)"
        raise ScenarioError(
            f"Scenario '{name}' not found: expected {rel(path)}. "
            f"Available scenarios: {listing}"
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ScenarioError(f"Cannot parse {rel(path)}: {exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioError(f"{rel(path)} must contain a JSON object.")

    problems: list[str] = []

    def expect(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    for key in data:
        if key not in _ALLOWED_TOP_LEVEL:
            problems.append(f"unknown setting '{key}'")

    # --- identity -------------------------------------------------------
    scenario_name = data.get("name")
    expect(isinstance(scenario_name, str) and scenario_name, "missing 'name'")
    if isinstance(scenario_name, str):
        expect(
            scenario_name == name,
            f"'name' must be '{name}' (match the file name)",
        )
    display_name = data.get("display_name") or (scenario_name or name)
    description = str(data.get("description") or "")

    # --- dam / coordinates ---------------------------------------------
    dam_id = data.get("dam_id")
    expect(dam_id is None or (isinstance(dam_id, str) and dam_id),
           "'dam_id' must be a non-empty string")
    longitude = data.get("longitude")
    latitude = data.get("latitude")
    dam_name: str | None = None
    river: str | None = None

    if longitude is not None or latitude is not None:
        expect(_is_number(longitude) and -180.0 <= float(longitude or 0) <= 180.0,
               "'longitude' must be a number in [-180, 180]")
        expect(_is_number(latitude) and -90.0 <= float(latitude or 0) <= 90.0,
               "'latitude' must be a number in [-90, 90]")
        if not (_is_number(longitude) and _is_number(latitude)):
            longitude = latitude = None
    elif dam_id:
        try:
            feature = find_dam(dam_id)
            coords = feature["geometry"]["coordinates"]
            longitude, latitude = float(coords[0]), float(coords[1])
            props = feature.get("properties", {})
            dam_name = props.get("name")
            river = props.get("river")
        except ScenarioError as exc:
            problems.append(str(exc))
    if longitude is None or latitude is None:
        expect(False, "either 'dam_id' or 'longitude'+'latitude' is required")

    # --- roots & inputs --------------------------------------------------
    data_root = data.get("data_root")
    runs_root = data.get("runs_root")
    expect(isinstance(data_root, str) and data_root, "missing 'data_root'")
    expect(isinstance(runs_root, str) and runs_root, "missing 'runs_root'")
    data_root_path = resolve_path(data_root) if isinstance(data_root, str) and data_root else REPO_ROOT
    runs_root_path = resolve_path(runs_root) if isinstance(runs_root, str) and runs_root else REPO_ROOT

    inputs_raw = data.get("inputs")
    expect(isinstance(inputs_raw, dict) and "dem" in inputs_raw,
           "missing 'inputs.dem'")
    inputs: dict[str, Path] = {}
    if isinstance(inputs_raw, dict):
        for key, value in inputs_raw.items():
            if key not in ("dem", "secondary_dem"):
                problems.append(f"unknown input '{key}'")
            elif not isinstance(value, str) or not value:
                problems.append(f"'inputs.{key}' must be a path string")
            else:
                inputs[key] = resolve_path(value)

    # --- optional clip step ---------------------------------------------
    clip_cfg = data.get("clip")
    clip: dict[str, Any] | None = None
    if clip_cfg is not None:
        if not isinstance(clip_cfg, dict):
            problems.append("'clip' must be an object")
        else:
            for key in clip_cfg:
                if key not in ("source_dem", "width_km", "height_km"):
                    problems.append(f"unknown clip setting '{key}'")
            source = clip_cfg.get("source_dem")
            if not isinstance(source, str) or not source:
                problems.append("'clip.source_dem' is required")
            width = clip_cfg.get("width_km", 5.0)
            height = clip_cfg.get("height_km", 5.0)
            if not (_is_number(width) and float(width) > 0):
                problems.append("'clip.width_km' must be a positive number")
            if not (_is_number(height) and float(height) > 0):
                problems.append("'clip.height_km' must be a positive number")
            if isinstance(source, str) and source:
                clip = {
                    "source_dem": resolve_path(source),
                    "width_km": float(width),
                    "height_km": float(height),
                }

    # --- terrain sections -------------------------------------------------
    terrain_raw = data.get("terrain") or {}
    if not isinstance(terrain_raw, dict):
        problems.append("'terrain' must be an object")
        terrain_raw = {}

    terrain: dict[str, Any] = {}
    for section, defaults in TERRAIN_DEFAULTS.items():
        section_raw = terrain_raw.get(section) or {}
        if not isinstance(section_raw, dict):
            problems.append(f"'terrain.{section}' must be an object")
            section_raw = {}
        for key in section_raw:
            if key not in defaults:
                problems.append(f"unknown setting 'terrain.{section}.{key}'")
        merged = dict(defaults)
        merged.update(section_raw)
        for key, value in merged.items():
            if key in ("preview", "csv"):
                if not isinstance(value, bool):
                    problems.append(f"'terrain.{section}.{key}' must be true/false")
            elif not _is_number(value):
                problems.append(f"'terrain.{section}.{key}' must be a number")
        for (sec, key), (low, high) in TERRAIN_RANGES.items():
            if sec == section and _is_number(merged.get(key)):
                value = float(merged[key])
                if not (low <= value <= high):
                    problems.append(
                        f"'terrain.{sec}.{key}' = {value} outside valid range "
                        f"[{low}, {high}]"
                    )
        terrain[section] = merged
    for key in terrain_raw:
        if key not in TERRAIN_DEFAULTS and key != "outputs":
            problems.append(f"unknown terrain setting '{key}'")

    # --- artifact outputs (defaults follow the project's naming) ----------
    stem = inputs.get("dem", Path(name)).stem if "dem" in inputs_raw else name
    processed_dir = data_root_path / "processed"
    sph_dir = data_root_path / "sph"
    default_outputs: dict[str, Path] = {
        "conditioned": processed_dir / f"{stem}_conditioned.tif",
        "conditioned_preview": processed_dir / f"{stem}_conditioned_comparison.png",
        "flow_tif": processed_dir / f"{stem}_flow_accumulation.tif",
        "flow_preview": processed_dir / f"{stem}_flow_preview.png",
        "domain_tif": processed_dir / f"{stem}_flood_domain.tif",
        "domain_preview": processed_dir / f"{stem}_flood_domain_preview.png",
        "sph_npz": sph_dir / f"{name}_sph_terrain.npz",
        "sph_csv": sph_dir / f"{name}_sph_terrain.csv",
        "sph_preview": sph_dir / f"{name}_sph_terrain_preview.png",
        "stl": sph_dir / f"{name}_sph_terrain.stl",
        "mesh_preview": sph_dir / f"{name}_sph_terrain_mesh_preview.png",
    }
    outputs_raw = terrain_raw.get("outputs") or {}
    if not isinstance(outputs_raw, dict):
        problems.append("'terrain.outputs' must be an object")
        outputs_raw = {}
    outputs: dict[str, Path] = dict(default_outputs)
    for key, value in outputs_raw.items():
        if key not in _ALLOWED_TERRAIN_OUTPUTS:
            problems.append(f"unknown output '{key}' in terrain.outputs")
        elif not isinstance(value, str) or not value:
            problems.append(f"'terrain.outputs.{key}' must be a path string")
        else:
            outputs[key] = resolve_path(value)

    # --- DualSPHysics case --------------------------------------------------
    case_raw = data.get("case")
    if case_raw is None:
        problems.append("missing 'case' section (case generator + config)")
        case_raw = {}
    if not isinstance(case_raw, dict):
        problems.append("'case' must be an object")
        case_raw = {}
    for key in case_raw:
        if key not in ("generator", "config", "overrides", "geometry_preview",
                       "preview_script", "preview_outputs"):
            problems.append(f"unknown case setting '{key}'")

    generator = case_raw.get("generator")
    base_config = case_raw.get("config")
    expect(isinstance(generator, str) and generator, "'case.generator' is required")
    expect(isinstance(base_config, str) and base_config, "'case.config' is required")

    overrides = case_raw.get("overrides") or {}
    if not isinstance(overrides, dict):
        problems.append("'case.overrides' must be an object")
        overrides = {}

    base_cfg_data: dict[str, Any] = {}
    if isinstance(base_config, str) and base_config:
        base_path = resolve_path(base_config)
        if base_path.exists():
            try:
                loaded = json.loads(base_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    base_cfg_data = loaded
                else:
                    problems.append(f"'case.config' file must contain a JSON object: {rel(base_path)}")
            except Exception as exc:
                problems.append(f"cannot parse 'case.config' {rel(base_path)}: {exc}")
        else:
            problems.append(f"'case.config' file not found: {rel(base_path)}")

    for key, value in overrides.items():
        if key not in base_cfg_data:
            problems.append(
                f"'case.overrides.{key}' is not a key of the base case config "
                f"(real parameters only)"
            )
            continue
        base_value = base_cfg_data[key]
        if isinstance(base_value, bool) or base_value is None or isinstance(base_value, str):
            if type(value) is not type(base_value):
                problems.append(f"'case.overrides.{key}' must be of type {type(base_value).__name__}")
        elif _is_number(base_value):
            if not _is_number(value):
                problems.append(f"'case.overrides.{key}' must be a number")
            else:
                bounds = CASE_OVERRIDE_RANGES.get(key)
                if bounds and not (bounds[0] <= float(value) <= bounds[1]):
                    problems.append(
                        f"'case.overrides.{key}' = {value} outside valid range "
                        f"[{bounds[0]}, {bounds[1]}]"
                    )
        elif not isinstance(value, type(base_value)):
            problems.append(f"'case.overrides.{key}' has the wrong type")

    geometry_preview = case_raw.get("geometry_preview", False)
    if not isinstance(geometry_preview, bool):
        problems.append("'case.geometry_preview' must be true/false")
        geometry_preview = False
    preview_script = case_raw.get("preview_script")
    if preview_script is None and isinstance(generator, str) and generator:
        preview_script = str(resolve_path(generator).parent / "preview_terrain_case.py")
    if preview_script is not None and not isinstance(preview_script, str):
        problems.append("'case.preview_script' must be a path string")
        preview_script = None

    preview_outputs_raw = case_raw.get("preview_outputs") or {}
    if not isinstance(preview_outputs_raw, dict):
        problems.append("'case.preview_outputs' must be an object")
        preview_outputs_raw = {}
    preview_outputs = {
        "png": processed_dir / f"{name}_geometry_preview.png",
        "vtk": processed_dir / f"{name}_geometry_preview.vtk",
        "json": processed_dir / f"{name}_geometry_preview.json",
    }
    for key, value in preview_outputs_raw.items():
        if key not in preview_outputs:
            problems.append(f"unknown case preview output '{key}'")
        elif not isinstance(value, str) or not value:
            problems.append(f"'case.preview_outputs.{key}' must be a path string")
        else:
            preview_outputs[key] = resolve_path(value)

    # --- simulation / post-processing --------------------------------------
    sim_raw = data.get("simulation") or {}
    if not isinstance(sim_raw, dict):
        problems.append("'simulation' must be an object")
        sim_raw = {}
    for key in sim_raw:
        if key not in ("enabled",):
            problems.append(f"unknown simulation setting '{key}'")
    sim_enabled = sim_raw.get("enabled", True)
    if not isinstance(sim_enabled, bool):
        problems.append("'simulation.enabled' must be true/false")
        sim_enabled = True

    post_raw = data.get("postprocess")
    postprocess: list[dict[str, Any]] = []
    if post_raw is not None:
        if not isinstance(post_raw, list):
            problems.append("'postprocess' must be a list")
        else:
            for index, entry in enumerate(post_raw):
                label = f"postprocess[{index}]"
                if not isinstance(entry, dict):
                    problems.append(f"{label} must be an object")
                    continue
                for key in entry:
                    if key not in ("name", "script", "args", "cwd"):
                        problems.append(f"{label}: unknown setting '{key}'")
                entry_name = entry.get("name") or f"step {index + 1}"
                script = entry.get("script")
                args = entry.get("args") or []
                cwd = entry.get("cwd")
                if not isinstance(script, str) or not script:
                    problems.append(f"{label}: 'script' is required")
                    continue
                if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                    problems.append(f"{label}: 'args' must be a list of strings")
                    continue
                if cwd is not None and not isinstance(cwd, str):
                    problems.append(f"{label}: 'cwd' must be a path string")
                    continue
                postprocess.append({
                    "name": str(entry_name),
                    "script": resolve_path(script),
                    "args": list(args),
                    "cwd": resolve_path(cwd) if cwd else None,
                })

    dualsphysics_root = data.get("dualsphysics_root")
    if dualsphysics_root is not None and not (
        isinstance(dualsphysics_root, str) and dualsphysics_root
    ):
        problems.append("'dualsphysics_root' must be a path string")
        dualsphysics_root = None

    if problems:
        bullets = "\n".join(f"  - {p}" for p in problems)
        raise ScenarioError(f"Invalid scenario '{name}' ({rel(path)}):\n{bullets}")

    assert isinstance(scenario_name, str)
    assert longitude is not None and latitude is not None

    return Scenario(
        name=scenario_name,
        display_name=str(display_name),
        description=description,
        dam_id=str(dam_id) if dam_id else None,
        dam_name=dam_name,
        river=river,
        longitude=float(longitude),
        latitude=float(latitude),
        data_root=data_root_path,
        runs_root=runs_root_path,
        inputs=inputs,
        clip=clip,
        terrain=terrain,
        outputs=outputs,
        case={
            "generator": resolve_path(generator),
            "config": resolve_path(base_config),
            "overrides": dict(overrides),
            "geometry_preview": geometry_preview,
            "preview_script": resolve_path(preview_script) if preview_script else None,
            "preview_outputs": preview_outputs,
            "base_config_data": base_cfg_data,
        },
        simulation={"enabled": sim_enabled},
        postprocess=postprocess,
        dualsphysics_root=resolve_path(dualsphysics_root) if dualsphysics_root else None,
        path=path,
    )
