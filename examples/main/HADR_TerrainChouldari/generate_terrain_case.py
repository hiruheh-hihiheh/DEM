"""
Generate the experimental HADR_TerrainChouldari terrain breach case.

This redesigned generator builds the dam/reservoir/breach geometry from the
DEM-derived terrain points instead of using an arbitrary rectangular wall and
detached reservoir box.

Design intent:

- The real DEM terrain STL remains unchanged.
- The dam centerline is placed approximately perpendicular to the estimated
  upstream/downstream direction.
- The dam span is estimated from local terrain around the dam point.
- The dam wall is segmented and vertically anchored to local terrain.
- The dam crest is ONE common horizontal elevation.
- The reservoir is segmented and placed immediately upstream of the dam face.
- The reservoir has ONE common horizontal water-surface elevation.
- The breach is centered on the dam centerline.
- Fixed dam segments remain on both sides of the breach.
- Only the centered breach segment is a moving gate.

This script does NOT:
- run GenCase
- run DualSPHysics
- modify the existing synthetic HADR_DamBreak case
- modify backend runner behavior
- modify frontend behavior
- modify Delft3D integration
"""

from __future__ import annotations

import json
import math
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np


CASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CASE_DIR.parents[2]
DEFAULT_CONFIG_PATH = CASE_DIR / "terrain_case_config.json"

TERRAIN_MK = 0
FIXED_WALL_MK = 1
BREACH_GATE_MK = 200
MOTION_EPS = 1e-6


class TerrainCaseError(Exception):
    """Base exception for terrain case generation errors."""


@dataclass
class WallBox:
    x0: float
    x1: float
    y0: float
    y1: float
    z0: float
    z1: float
    ground_z: float
    cell_terrain_max: float | None
    kind: str


@dataclass
class FluidBox:
    x0: float
    x1: float
    y0: float
    y1: float
    z0: float
    z1: float
    ground_z: float
    cell_terrain_max: float | None
    water_surface_z: float
    water_depth: float


@dataclass
class DamGeometry:
    flow_axis: str
    upstream_sign: float
    upstream_method: str
    center_x: float
    center_y: float
    center_z: float
    span_start: float
    span_end: float
    endpoints: list[list[float]]
    thickness: float
    crest_z: float | None
    wall_bottom_min: float | None
    wall_bottom_max: float | None
    local_terrain_min: float | None
    local_terrain_max: float | None
    fixed_boxes: list[WallBox] = field(default_factory=list)
    breach_box: WallBox | None = None
    breach_center: float | None = None
    breach_width: float | None = None
    fixed_left_width: float | None = None
    fixed_right_width: float | None = None
    gate_initial_z: float | None = None
    gate_final_z: float | None = None
    gate_top_final_z: float | None = None
    gate_lift_distance: float | None = None


@dataclass
class ReservoirGeometry:
    boxes: list[FluidBox] = field(default_factory=list)
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    z_min: float | None = None
    z_max: float | None = None
    water_surface_z: float | None = None
    water_depth_min: float | None = None
    water_depth_max: float | None = None


def _resolve_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _positive(cfg: dict, key: str, default: float | None = None) -> float:
    value = cfg.get(key, default)
    if value is None:
        raise TerrainCaseError(f"Config value '{key}' is required")
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        raise TerrainCaseError(f"Config value '{key}' must be positive: {value}")
    return value


def _non_negative(cfg: dict, key: str, default: float) -> float:
    value = float(cfg.get(key, default))
    if not math.isfinite(value) or value < 0.0:
        raise TerrainCaseError(
            f"Config value '{key}' must be zero or positive: {value}"
        )
    return value


def _f(value: float) -> str:
    return f"{float(value):.6f}"


def _fmt(value: float | None) -> str:
    if value is None:
        return "None"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(numeric):
        return str(value)
    return f"{numeric:.6f}"


def _npz_scalar(data: np.lib.npyio.NpzFile, key: str) -> float | None:
    if key not in data:
        return None
    arr = np.asarray(data[key]).ravel()
    if arr.size == 0:
        return None
    try:
        value = float(arr[0])
    except (TypeError, ValueError):
        return None
    if math.isfinite(value):
        return value
    return None


def _load_terrain(cfg: dict) -> dict:
    npz_path = _resolve_path(cfg["terrain_npz"])
    if not npz_path.exists():
        raise TerrainCaseError(f"Terrain NPZ not found: {npz_path}")

    with np.load(npz_path, allow_pickle=False) as data:
        required = ("x_sim", "y_sim", "z_sim", "scale")
        missing = [key for key in required if key not in data]
        if missing:
            raise TerrainCaseError(
                f"Terrain NPZ is missing required arrays: {', '.join(missing)}"
            )
        x = np.asarray(data["x_sim"], dtype=float).ravel()
        y = np.asarray(data["y_sim"], dtype=float).ravel()
        z = np.asarray(data["z_sim"], dtype=float).ravel()
        scale = float(np.asarray(data["scale"]).ravel()[0])
        dam_x_m = _npz_scalar(data, "dam_x_m")
        dam_y_m = _npz_scalar(data, "dam_y_m")

    if not (x.size == y.size == z.size):
        raise TerrainCaseError("x_sim, y_sim, and z_sim have different lengths.")

    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x = x[finite]
    y = y[finite]
    z = z[finite]

    if x.size < 3:
        raise TerrainCaseError("Terrain NPZ contains fewer than three valid samples.")

    if not math.isfinite(scale) or scale <= 0.0:
        raise TerrainCaseError(f"Invalid terrain scale in NPZ: {scale}")

    if dam_x_m is not None and dam_y_m is not None:
        dam_x = dam_x_m * scale
        dam_y = dam_y_m * scale
        dam_coordinate_source = "npz_dam_x_m_y_m_times_scale"
    else:
        dam_x = float(cfg.get("fallback_dam_x_sim", 9.6898))
        dam_y = float(cfg.get("fallback_dam_y_sim", 9.8289))
        dam_coordinate_source = "config_fallback"

    dam_z = float(z[np.argmin((x - dam_x) ** 2 + (y - dam_y) ** 2)])

    return {
        "npz_path": npz_path,
        "x": x,
        "y": y,
        "z": z,
        "scale": scale,
        "dam_x": dam_x,
        "dam_y": dam_y,
        "dam_z": dam_z,
        "dam_coordinate_source": dam_coordinate_source,
    }


def _estimate_upstream_axis(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    dam_x: float,
    dam_y: float,
    radius: float,
) -> tuple[str, float, str]:
    dx = x - dam_x
    dy = y - dam_y
    dist = np.hypot(dx, dy)

    candidates = [
        ("x", 1.0, dx),
        ("x", -1.0, -dx),
        ("y", 1.0, dy),
        ("y", -1.0, -dy),
    ]

    best_axis = "x"
    best_sign = 1.0
    best_score = -math.inf
    best_method = "fallback-default"

    for axis, sign, projection in candidates:
        mask = (
            (dist <= radius)
            & (dist > 0.25 * radius)
            & (projection > 0.25 * dist)
        )
        count = int(mask.sum())
        if count < 3:
            continue
        score = float(np.mean(z[mask]))
        if score > best_score:
            best_score = score
            best_axis = axis
            best_sign = sign
            best_method = "local-upstream-elevation-heuristic"

    return best_axis, best_sign, best_method


def _cell_stats(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
) -> dict | None:
    mask = (
        (x >= min(x0, x1))
        & (x <= max(x0, x1))
        & (y >= min(y0, y1))
        & (y <= max(y0, y1))
        & np.isfinite(z)
    )
    if not np.any(mask):
        return None
    values = z[mask]
    return {
        "count": int(values.size),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "median": float(np.median(values)),
    }


def _estimate_dam_span(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    dam_x: float,
    dam_y: float,
    flow_axis: str,
    cfg: dict,
    particle_spacing: float,
) -> tuple[float, float]:
    search_radius = float(cfg.get("dam_search_radius", 8.0))
    min_span = float(cfg.get("dam_min_span", 2.0))
    max_span = float(cfg.get("dam_max_span", 12.0))
    valley_threshold = float(cfg.get("dam_valley_threshold", 0.30))

    dist = np.hypot(x - dam_x, y - dam_y)
    local = dist <= search_radius

    if flow_axis == "x":
        s = y[local] - dam_y
    else:
        s = x[local] - dam_x

    zs = z[local]
    finite = np.isfinite(s) & np.isfinite(zs)
    s = s[finite]
    zs = zs[finite]

    if s.size < 20:
        half = max(min_span / 2.0, 3.0 * particle_spacing)
        return -half, half

    p5, p95 = np.percentile(s, [5.0, 95.0])
    data_span = float(p95 - p5)

    if data_span < min_span:
        half = min_span / 2.0
        return -half, half

    bin_size = max(particle_spacing, data_span / 40.0)
    edges = np.arange(p5, p95 + bin_size, bin_size)

    if len(edges) < 3:
        half = max(min_span / 2.0, data_span / 2.0)
        return -half, half

    idx = np.digitize(s, edges) - 1
    valid = (idx >= 0) & (idx < len(edges) - 1)
    idx = idx[valid]
    zs_bin = zs[valid]

    bin_min = np.full(len(edges) - 1, np.nan)
    for b in range(len(edges) - 1):
        values = zs_bin[idx == b]
        if values.size > 0:
            bin_min[b] = float(np.min(values))

    valid_bins = ~np.isnan(bin_min)
    if not np.any(valid_bins):
        half = max(min_span / 2.0, data_span / 2.0)
        return -half, half

    valley_floor = float(np.nanmin(bin_min))
    relief = float(np.nanmax(bin_min)) - valley_floor
    cutoff = valley_floor + max(valley_threshold, 0.25 * relief)
    low = valid_bins & (bin_min <= cutoff)

    centers = (edges[:-1] + edges[1:]) / 2.0
    center_bin = int(np.argmin(np.abs(centers)))

    if low[center_bin]:
        start = center_bin
        end = center_bin
        while start > 0 and low[start - 1]:
            start -= 1
        while end < len(low) - 1 and low[end + 1]:
            end += 1
    else:
        low_indices = np.where(low)[0]
        if low_indices.size == 0:
            half = max(min_span / 2.0, data_span / 2.0)
            return -half, half
        nearest = int(low_indices[np.argmin(np.abs(centers[low_indices]))])
        start = nearest
        end = nearest
        while start > 0 and low[start - 1]:
            start -= 1
        while end < len(low) - 1 and low[end + 1]:
            end += 1
        if start > center_bin:
            start = center_bin
        if end < center_bin:
            end = center_bin

    s_min = float(edges[start])
    s_max = float(edges[end + 1])

    if s_min > 0.0:
        s_min = min(0.0, s_min)
    if s_max < 0.0:
        s_max = max(0.0, s_max)

    width = s_max - s_min
    if width < min_span:
        s_min = -min_span / 2.0
        s_max = min_span / 2.0
        width = min_span
    if width > max_span:
        s_min = -max_span / 2.0
        s_max = max_span / 2.0

    return float(s_min), float(s_max)


def _split_interval(
    start: float,
    end: float,
    target_length: float,
    max_segments: int = 50,
) -> list[tuple[float, float]]:
    width = end - start
    if width <= 0.0:
        return []
    n = max(1, int(round(width / max(target_length, 1e-6))))
    n = min(n, max_segments)
    edges = np.linspace(start, end, n + 1)
    return [(float(edges[i]), float(edges[i + 1])) for i in range(n)]


def _make_wall_boxes(
    flow_axis: str,
    dam_x: float,
    dam_y: float,
    thickness: float,
    interval_start: float,
    interval_end: float,
    segment_length: float,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    embedment: float,
    common_crest_z: float,
    default_ground_z: float,
    kind: str,
) -> list[WallBox]:
    """
    Build terrain-anchored wall segments with a COMMON crest elevation.

    z0 = local terrain elevation - embedment  (terrain-anchored bottom)
    z1 = common_crest_z                       (same for ALL segments)

    NO fallback is applied. If z1 <= z0 for any segment, validation will fail.
    """
    boxes: list[WallBox] = []
    intervals = _split_interval(interval_start, interval_end, segment_length)
    if not intervals:
        return boxes

    expand = max(0.05, 0.25 * thickness)

    for s0, s1 in intervals:
        if flow_axis == "x":
            x0 = dam_x - thickness / 2.0
            x1 = dam_x + thickness / 2.0
            y0 = s0
            y1 = s1
        else:
            x0 = s0
            x1 = s1
            y0 = dam_y - thickness / 2.0
            y1 = dam_y + thickness / 2.0

        stats = _cell_stats(
            x, y, z,
            x0 - expand, x1 + expand,
            y0 - expand, y1 + expand,
        )

        if stats is None:
            ground_z = default_ground_z
            terrain_max = None
        else:
            ground_z = stats["median"]
            terrain_max = stats["max"]

        z0 = ground_z - embedment
        z1 = common_crest_z

        # NO FALLBACK. If z1 <= z0, validation will catch it.

        boxes.append(
            WallBox(
                x0=float(x0),
                x1=float(x1),
                y0=float(y0),
                y1=float(y1),
                z0=float(z0),
                z1=float(z1),
                ground_z=float(ground_z),
                cell_terrain_max=terrain_max,
                kind=kind,
            )
        )

    return boxes


def _make_reservoir_boxes(
    flow_axis: str,
    upstream_sign: float,
    dam_x: float,
    dam_y: float,
    thickness: float,
    span_start: float,
    span_end: float,
    reservoir_length: float,
    reservoir_segments: int,
    water_depth: float,
    fluid_bed_clearance: float,
    particle_spacing: float,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    default_ground_z: float,
) -> tuple[list[FluidBox], float | None]:
    """
    Build terrain-following reservoir segments with ONE common horizontal
    water-surface elevation.

    z0 = bed_z + fluid_bed_clearance
    z1 = common_water_surface_z  (same for ALL segments)

    NO fallback is applied. If z1 <= z0 for any segment, validation will fail.
    """
    if span_end <= span_start:
        return [], None

    intervals = _split_interval(
        span_start,
        span_end,
        (span_end - span_start) / max(1, reservoir_segments),
        max_segments=max(1, reservoir_segments),
    )

    if not intervals:
        return [], None

    # Determine flow-axis coordinates.
    if flow_axis == "x":
        upstream_face = dam_x + upstream_sign * thickness / 2.0
        if upstream_sign > 0:
            fluid_x0 = upstream_face + fluid_bed_clearance
            fluid_x1 = fluid_x0 + reservoir_length
        else:
            fluid_x1 = upstream_face - fluid_bed_clearance
            fluid_x0 = fluid_x1 - reservoir_length
    else:
        upstream_face = dam_y + upstream_sign * thickness / 2.0
        if upstream_sign > 0:
            fluid_y0 = upstream_face + fluid_bed_clearance
            fluid_y1 = fluid_y0 + reservoir_length
        else:
            fluid_y1 = upstream_face - fluid_bed_clearance
            fluid_y0 = fluid_y1 - reservoir_length

    # First pass: determine bed elevation for each segment.
    bed_elevations: list[float] = []
    terrain_maxes: list[float | None] = []

    for s0, s1 in intervals:
        if flow_axis == "x":
            x0, x1 = fluid_x0, fluid_x1
            y0, y1 = s0, s1
        else:
            x0, x1 = s0, s1
            y0, y1 = fluid_y0, fluid_y1

        stats = _cell_stats(x, y, z, x0, x1, y0, y1)
        if stats is None:
            bed_elevations.append(default_ground_z)
            terrain_maxes.append(None)
        else:
            bed_elevations.append(stats["max"])
            terrain_maxes.append(stats["max"])

    if not bed_elevations:
        return [], None

    # Common water surface: above every bed by at least water_depth.
    max_bed = max(bed_elevations)
    common_water_surface_z = max_bed + water_depth

    # Second pass: create boxes.
    boxes: list[FluidBox] = []

    for i, (s0, s1) in enumerate(intervals):
        if flow_axis == "x":
            x0, x1 = fluid_x0, fluid_x1
            y0, y1 = s0, s1
        else:
            x0, x1 = s0, s1
            y0, y1 = fluid_y0, fluid_y1

        bed_z = bed_elevations[i]
        terrain_max = terrain_maxes[i]

        z0 = bed_z + fluid_bed_clearance
        z1 = common_water_surface_z

        actual_depth = z1 - z0

        # NO FALLBACK. If actual_depth <= 0, validation will catch it.

        boxes.append(
            FluidBox(
                x0=float(x0),
                x1=float(x1),
                y0=float(y0),
                y1=float(y1),
                z0=float(z0),
                z1=float(z1),
                ground_z=float(bed_z),
                cell_terrain_max=terrain_max,
                water_surface_z=float(z1),
                water_depth=float(actual_depth),
            )
        )

    return boxes, common_water_surface_z


def _smoothstep(progress: float) -> float:
    progress = min(max(progress, 0.0), 1.0)
    return progress * progress * (3.0 - 2.0 * progress)


def _write_gate_motion(
    motion_path: Path,
    initial_x: float,
    initial_y: float,
    initial_z: float,
    breach_time: float,
    simulation_time: float,
    lift_duration: float,
    lift_distance: float,
    motion_steps: int,
) -> float:
    end_time = max(float(simulation_time), 0.0)
    start_open = max(float(breach_time), 0.0)

    points = [(0.0, 0.0)]

    if end_time <= MOTION_EPS:
        points = [(0.0, 0.0)]
        duration = MOTION_EPS
    else:
        if start_open > 0.0:
            hold_time = min(start_open, end_time)
            if hold_time > MOTION_EPS:
                points.append((hold_time, 0.0))

        if start_open < end_time and lift_duration > MOTION_EPS:
            lift_end = min(start_open + lift_duration, end_time)
            span = lift_end - start_open

            if span > MOTION_EPS:
                steps = max(1, int(motion_steps))
                for i in range(1, steps + 1):
                    t = start_open + span * (i / steps)
                    if t > end_time:
                        t = end_time
                    progress = (t - start_open) / lift_duration
                    z_offset = lift_distance * _smoothstep(progress)
                    points.append((t, z_offset))

        last_t, last_z_offset = points[-1]
        if end_time - last_t > MOTION_EPS:
            points.append((end_time, last_z_offset))

        cleaned = []
        for t, z_offset in points:
            if cleaned and abs(t - cleaned[-1][0]) <= MOTION_EPS:
                cleaned[-1] = (cleaned[-1][0], z_offset)
            else:
                cleaned.append((t, z_offset))

        points = cleaned
        duration = max(points[-1][0], MOTION_EPS)

    lines = []
    for t, z_offset in points:
        z = initial_z + z_offset
        lines.append(f"{t:.8f} {initial_x:.8f} {initial_y:.8f} {z:.8f}")

    motion_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return duration


def _drawbox_xml(box: WallBox | FluidBox, indent: str = "                    ") -> str:
    size_x = max(box.x1 - box.x0, 1e-6)
    size_y = max(box.y1 - box.y0, 1e-6)
    size_z = max(box.z1 - box.z0, 1e-6)

    return f"""{indent}<drawbox>
{indent}    <boxfill>
{indent}        solid
{indent}    </boxfill>
{indent}    <point x="{_f(box.x0)}" y="{_f(box.y0)}" z="{_f(box.z0)}" />
{indent}    <size x="{_f(size_x)}" y="{_f(size_y)}" z="{_f(size_z)}" />
{indent}</drawbox>

"""


def _build_motion_xml(motion_file: str, motion_duration: float) -> str:
    motion_attribute = quoteattr(motion_file)
    return f"""        <motion>
            <objreal ref="{BREACH_GATE_MK}">
                <begin mov="1" start="0"/>
                <mvfile id="1" duration="{motion_duration:.8f}">
                    <file name={motion_attribute} fields="4" fieldtime="0" fieldx="1" fieldy="2" fieldz="3"/>
                </mvfile>
            </objreal>
        </motion>"""


def _build_simulation_domain_xml(
    pointmin: tuple[float, float, float],
    pointmax: tuple[float, float, float],
) -> str:
    """
    Build a fully explicit numeric simulation domain.

    No "default" values are allowed for the terrain flood case.
    """

    return f"""            <simulationdomain>

                <posmin
                    x="{pointmin[0]:.10f}"
                    y="{pointmin[1]:.10f}"
                    z="{pointmin[2]:.10f}" />

                <posmax
                    x="{pointmax[0]:.10f}"
                    y="{pointmax[1]:.10f}"
                    z="{pointmax[2]:.10f}" />

            </simulationdomain>"""

def _build_xml(
    cfg: dict,
    geom: DamGeometry,
    reservoir: ReservoirGeometry,
    pointmin: tuple[float, float, float],
    pointmax: tuple[float, float, float],
    stl_reference: str,
    motion_reference: str | None,
    motion_duration: float | None,
) -> str:
    particle_spacing = float(cfg["particle_spacing"])
    simulation_time = float(cfg["simulation_time"])
    time_out = float(cfg["time_out"])

    stl_attribute = quoteattr(stl_reference)

    fixed_wall_xml = ""
    if geom.fixed_boxes:
        fixed_wall_xml = "                    <!-- Terrain-anchored fixed dam segments -->\n"
        fixed_wall_xml += f"                    <setmkbound mk=\"{FIXED_WALL_MK}\" />\n\n"
        for box in geom.fixed_boxes:
            fixed_wall_xml += _drawbox_xml(box)

    breach_wall_xml = ""
    if geom.breach_box is not None:
        breach_wall_xml = "                    <!-- Moving centered breach gate -->\n"
        breach_wall_xml += f"                    <setmkbound mk=\"{BREACH_GATE_MK}\" />\n\n"
        breach_wall_xml += _drawbox_xml(geom.breach_box)

    reservoir_xml = ""
    if reservoir.boxes:
        reservoir_xml = "                    <!-- Terrain-following upstream reservoir -->\n"
        reservoir_xml += "                    <setmkfluid mk=\"0\" />\n\n"
        for box in reservoir.boxes:
            reservoir_xml += _drawbox_xml(box)

    motion_xml = ""
    if (
        geom.breach_box is not None
        and motion_reference is not None
        and motion_duration is not None
    ):
        motion_xml = _build_motion_xml(motion_reference, motion_duration)

    simulation_domain_xml = _build_simulation_domain_xml(pointmin, pointmax)

    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<case>
    <casedef>
        <constantsdef>
            <gravity x="0" y="0" z="-9.81" comment="Gravitational acceleration" units_comment="m/s^2" />
            <rhop0 value="1000" comment="Reference density of the fluid" units_comment="kg/m^3" />
            <rhopgradient value="2" />
            <hswl value="0" auto="true" />
            <gamma value="7" />
            <speedsystem value="0" auto="true" />
            <coefsound value="20" />
            <speedsound value="0" auto="true" />
            <coefh value="1.0" />
            <_hdp value="2" />
            <cflnumber value="0.2" />
        </constantsdef>
        <mkconfig boundcount="240" fluidcount="9" />
        <geometry>
            <definition dp="{_f(particle_spacing)}" units_comment="simulation metres (scaled prototype units)">
                <pointmin x="{_f(pointmin[0])}" y="{_f(pointmin[1])}" z="{_f(pointmin[2])}" />
                <pointmax x="{_f(pointmax[0])}" y="{_f(pointmax[1])}" z="{_f(pointmax[2])}" />
            </definition>
            <commands>
                <mainlist>
                    <setshapemode>dp | bound</setshapemode>
                    <setdrawmode mode="full" />
                    <!-- Real DEM terrain surface remains fixed -->
                    <setmkbound mk="{TERRAIN_MK}" />
                    <drawfilestl file={stl_attribute} />

{fixed_wall_xml}
{breach_wall_xml}
{reservoir_xml}
                </mainlist>
            </commands>
        </geometry>

{motion_xml}
    </casedef>
    <execution>
        <parameters>
            <parameter key="SavePosDouble" value="0" />
            <parameter key="StepAlgorithm" value="1" />
            <parameter key="VerletSteps" value="40" />
            <parameter key="Kernel" value="1" />
            <parameter key="ViscoTreatment" value="1" />
            <parameter key="Visco" value="0.1" />
            <parameter key="ViscoBoundFactor" value="1" />
            <parameter key="DensityDT" value="2" />
            <parameter key="DensityDTvalue" value="0.1" />
            <parameter key="Shifting" value="0" />
            <parameter key="RigidAlgorithm" value="1" />
            <parameter key="CoefDtMin" value="0.05" />
            <parameter key="DtIni" value="0" />
            <parameter key="DtMin" value="0" />
            <parameter key="DtFixed" value="0" />
            <parameter key="DtFixedFile" value="NONE" />
            <parameter key="DtAllParticles" value="0" />
            <parameter key="TimeMax" value="{_f(simulation_time)}" />
            <parameter key="TimeOut" value="{_f(time_out)}" />
            <parameter key="PartsOutMax" value="1" />
            <parameter key="RhopOutMin" value="700" />
            <parameter key="RhopOutMax" value="1300" />

{simulation_domain_xml}

        </parameters>
    </execution>
</case>
"""

def _bounds_from_boxes(boxes: list[dict]) -> dict | None:
    """
    Compute combined XYZ bounds from a list of generated boxes.
    """

    if not boxes:
        return None

    return {
        "x_min": min(float(box["x0"]) for box in boxes),
        "x_max": max(float(box["x1"]) for box in boxes),
        "y_min": min(float(box["y0"]) for box in boxes),
        "y_max": max(float(box["y1"]) for box in boxes),
        "z_min": min(float(box["z0"]) for box in boxes),
        "z_max": max(float(box["z1"]) for box in boxes),
    }


def _parse_simulation_domain_xml(xml_path: Path) -> dict:
    """
    Parse <simulationdomain> from generated XML.

    Fails if any coordinate is missing, non-numeric, or set to "default".
    """

    errors: list[str] = []
    default_free = True
    pointmin = None
    pointmax = None

    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        return {
            "file": str(xml_path),
            "errors": [f"XML parse error: {exc}"],
            "default_free": False,
            "pointmin": None,
            "pointmax": None,
        }

    root = tree.getroot()
    simulation_domain = root.find(".//simulationdomain")

    if simulation_domain is None:
        return {
            "file": str(xml_path),
            "errors": ["Generated XML does not contain <simulationdomain>"],
            "default_free": False,
            "pointmin": None,
            "pointmax": None,
        }

    posmin_element = simulation_domain.find("posmin")
    posmax_element = simulation_domain.find("posmax")

    def parse_point(element, label: str) -> tuple[float, float, float] | None:
        nonlocal default_free

        if element is None:
            errors.append(f"<simulationdomain> is missing <{label}>")
            default_free = False
            return None

        values = []

        for axis in ("x", "y", "z"):
            raw = element.get(axis)

            if raw is None:
                errors.append(f"{label} is missing the {axis} attribute")
                default_free = False
                values.append(None)
                continue

            text = raw.strip()

            if text.lower() == "default":
                errors.append(f"{label} {axis} is set to 'default'")
                default_free = False
                values.append(None)
                continue

            try:
                values.append(float(text))
            except ValueError:
                errors.append(
                    f"{label} {axis} is not numeric: '{raw}'"
                )
                default_free = False
                values.append(None)

        if any(value is None for value in values):
            return None

        return (
            float(values[0]),
            float(values[1]),
            float(values[2]),
        )

    pointmin = parse_point(posmin_element, "posmin")
    pointmax = parse_point(posmax_element, "posmax")

    if pointmin is not None and pointmax is not None:
        axis_names = ("x", "y", "z")

        for i, axis in enumerate(axis_names):
            if not pointmin[i] < pointmax[i]:
                errors.append(
                    f"posmin {axis} is not less than posmax {axis}: "
                    f"{pointmin[i]} >= {pointmax[i]}"
                )

    return {
        "file": str(xml_path),
        "errors": errors,
        "default_free": default_free,
        "pointmin": pointmin,
        "pointmax": pointmax,
    }


def _validate_simulation_domain_xml(
    xml_path: Path,
    required_bounds: dict,
) -> dict:
    """
    Validate that the generated XML simulation domain:

    - contains no 'default' values
    - is fully numeric
    - has posmin < posmax on all axes
    - contains terrain, reservoir, dam/breach, and lifted-breach bounds
    """

    parsed = _parse_simulation_domain_xml(xml_path)

    errors = list(parsed["errors"])
    pointmin = parsed["pointmin"]
    pointmax = parsed["pointmax"]

    contains = {}

    if pointmin is not None and pointmax is not None:
        eps = 1e-6

        for name, bounds in required_bounds.items():
            if bounds is None:
                contains[name] = None
                continue

            ok = (
                bounds["x_min"] >= pointmin[0] - eps
                and bounds["x_max"] <= pointmax[0] + eps
                and bounds["y_min"] >= pointmin[1] - eps
                and bounds["y_max"] <= pointmax[1] + eps
                and bounds["z_min"] >= pointmin[2] - eps
                and bounds["z_max"] <= pointmax[2] + eps
            )

            contains[name] = ok

            if not ok:
                errors.append(
                    f"simulation domain does not fully contain {name}"
                )
    else:
        for name in required_bounds:
            contains[name] = False

    return {
        "file": str(xml_path),
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "default_free": parsed["default_free"],
        "pointmin": list(pointmin) if pointmin is not None else None,
        "pointmax": list(pointmax) if pointmax is not None else None,
        "contains": contains,
    }


def _validate_geometry(
    cfg: dict,
    geom: DamGeometry,
    reservoir: ReservoirGeometry,
    pointmin: tuple[float, float, float],
    pointmax: tuple[float, float, float],
    motion_path: Path,
    standalone_xml: Path,
    runner_xml: Path,
    simulation_time: float,
) -> dict:
    errors: list[str] = []
    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        if condition:
            checks += 1
        else:
            errors.append(message)

    fluid_clearance = float(cfg.get("fluid_bed_clearance", 0.02))
    breach_enabled = bool(cfg.get("breach_enabled", False))

    # --- Dam wall validation ---

    wall_boxes = list(geom.fixed_boxes)
    if geom.breach_box is not None:
        wall_boxes.append(geom.breach_box)

    check(len(wall_boxes) > 0, "Dam wall has at least one wall segment")

    # All dam crest elevations must be identical within 1e-6.
    wall_crests = [box.z1 for box in wall_boxes]
    if wall_crests:
        crest_spread = max(wall_crests) - min(wall_crests)
        check(
            crest_spread <= 1e-6,
            f"All dam crests identical (spread={crest_spread:.9f})",
        )
        if geom.crest_z is not None:
            check(
                all(abs(c - geom.crest_z) <= 1e-6 for c in wall_crests),
                "All dam crests equal dam_crest_z",
            )

    for i, box in enumerate(wall_boxes):
        check(
            box.z0 < box.z1,
            f"Dam segment {i} bottom is below crest (z0={box.z0:.6f}, z1={box.z1:.6f})",
        )
        check(
            box.z0 <= box.ground_z + 1e-3,
            f"Dam segment {i} bottom is anchored at/below local terrain",
        )

    # --- Reservoir validation ---

    check(len(reservoir.boxes) > 0, "Reservoir has at least one fluid segment")

    if geom.flow_axis == "x":
        upstream_face = geom.center_x + geom.upstream_sign * geom.thickness / 2.0
    else:
        upstream_face = geom.center_y + geom.upstream_sign * geom.thickness / 2.0

    # All reservoir water surfaces must be identical within 1e-6.
    water_surfaces = [box.z1 for box in reservoir.boxes]
    if water_surfaces:
        ws_spread = max(water_surfaces) - min(water_surfaces)
        check(
            ws_spread <= 1e-6,
            f"All water surfaces identical (spread={ws_spread:.9f})",
        )
        if reservoir.water_surface_z is not None:
            check(
                all(abs(ws - reservoir.water_surface_z) <= 1e-6 for ws in water_surfaces),
                "All water surfaces equal common_water_surface_z",
            )

    for i, box in enumerate(reservoir.boxes):
        check(
            box.water_depth > 1e-6,
            f"Reservoir segment {i} has positive water depth ({box.water_depth:.6f})",
        )

        # Reservoir contacts upstream dam face.
        if geom.flow_axis == "x":
            if geom.upstream_sign > 0:
                gap = box.x0 - upstream_face
            else:
                gap = upstream_face - box.x1
        else:
            if geom.upstream_sign > 0:
                gap = box.y0 - upstream_face
            else:
                gap = upstream_face - box.y1

        check(
            -1e-3 <= gap <= fluid_clearance + 1e-3,
            f"Reservoir segment {i} is immediately upstream of dam face",
        )

        # Reservoir does not overlap terrain.
        if box.cell_terrain_max is not None:
            check(
                box.z0 >= box.cell_terrain_max + fluid_clearance - 1e-3,
                f"Reservoir segment {i} does not overlap local terrain",
            )

    # --- Breach validation ---

    if breach_enabled:
        check(geom.breach_box is not None, "Moving breach box exists")
        check(geom.breach_center is not None, "Breach center exists")
        check(geom.breach_width is not None, "Breach width exists")

        if (
            geom.breach_center is not None
            and geom.span_start is not None
            and geom.span_end is not None
        ):
            dam_center = (geom.span_start + geom.span_end) / 2.0
            check(
                abs(geom.breach_center - dam_center) <= 1e-6,
                "Breach is centered on dam centerline",
            )

        if (
            geom.fixed_left_width is not None
            and geom.fixed_right_width is not None
        ):
            check(
                geom.fixed_left_width > 1e-3,
                "Fixed dam segment remains on left side of breach",
            )
            check(
                geom.fixed_right_width > 1e-3,
                "Fixed dam segment remains on right side of breach",
            )

        if geom.breach_box is not None and geom.gate_lift_distance is not None:
            initial_z0 = geom.breach_box.z0
            initial_z1 = geom.breach_box.z1
            final_z0 = initial_z0 + geom.gate_lift_distance
            final_z1 = initial_z1 + geom.gate_lift_distance
            eps = 1e-6

            check(
                geom.breach_box.x0 >= pointmin[0] - eps
                and geom.breach_box.x1 <= pointmax[0] + eps
                and geom.breach_box.y0 >= pointmin[1] - eps
                and geom.breach_box.y1 <= pointmax[1] + eps
                and initial_z0 >= pointmin[2] - eps
                and initial_z1 <= pointmax[2] + eps,
                "Initial moving breach stays inside simulation domain",
            )

            check(
                geom.breach_box.x0 >= pointmin[0] - eps
                and geom.breach_box.x1 <= pointmax[0] + eps
                and geom.breach_box.y0 >= pointmin[1] - eps
                and geom.breach_box.y1 <= pointmax[1] + eps
                and final_z0 >= pointmin[2] - eps
                and final_z1 <= pointmax[2] + eps,
                "Final lifted moving breach stays inside simulation domain",
            )

        check(motion_path.exists(), "Gate motion file exists")

        if motion_path.exists():
            try:
                text = motion_path.read_text(encoding="utf-8")
                rows = []
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) != 4:
                        continue
                    try:
                        t, x, y, z = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
                    except ValueError:
                        continue
                    if math.isfinite(t) and math.isfinite(x) and math.isfinite(y) and math.isfinite(z):
                        rows.append((t, x, y, z))

                check(len(rows) >= 2, "Motion file has at least two rows")

                if len(rows) >= 2 and geom.breach_box is not None:
                    times = [r[0] for r in rows]
                    xs = [r[1] for r in rows]
                    ys = [r[2] for r in rows]
                    zs = [r[3] for r in rows]

                    check(abs(times[0]) <= MOTION_EPS, "Motion starts at t=0")

                    monotonic = all(
                        times[i] >= times[i - 1] - MOTION_EPS
                        for i in range(1, len(times))
                    )
                    check(monotonic, "Motion time is monotonic")

                    check(
                        all(abs(x - geom.breach_box.x0) <= 1e-4 for x in xs),
                        "Motion X remains constant",
                    )
                    check(
                        all(abs(y - geom.breach_box.y0) <= 1e-4 for y in ys),
                        "Motion Y remains constant",
                    )

                    z_non_decreasing = all(
                        zs[i] >= zs[i - 1] - 1e-6
                        for i in range(1, len(zs))
                    )
                    check(z_non_decreasing, "Motion Z is non-decreasing")

                    expected_final_z = geom.breach_box.z0 + geom.gate_lift_distance
                    check(
                        abs(zs[-1] - expected_final_z) <= 1e-3,
                        "Motion final Z equals initial Z + actual lift distance",
                    )

                    check(
                        all(z <= pointmax[2] + 1e-6 for z in zs),
                        "Motion Z positions remain inside simulation domain",
                    )

            except OSError as exc:
                check(False, f"Unable to read motion file: {exc}")

    else:
        check(
            not motion_path.exists(),
            "Motion file is absent when breach is disabled",
        )

    # XML well-formedness.
    for xml_path in (standalone_xml, runner_xml):
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            check(root.tag == "case", f"{xml_path.name} root tag is <case>")
        except ET.ParseError as exc:
            check(False, f"{xml_path.name} XML parse error: {exc}")

    return {
        "status": "pass" if not errors else "fail",
        "checks_passed": checks,
        "errors": errors,
        "dualsphysics_run_attempted": False,
    }


def _print_summary(summary: dict) -> None:
    print("=" * 78)
    print("HADR_TerrainChouldari redesigned terrain breach summary")
    print("=" * 78)

    print(f"Case name: {summary['case_name']}")
    print(f"Validation status: {summary['validation']['status']}")
    print(f"Validation checks passed: {summary['validation']['checks_passed']}")

    sd_explicit = summary.get("simulation_domain_explicit", False)
    sd_pointmin = summary.get("simulation_domain_pointmin")
    sd_pointmax = summary.get("simulation_domain_pointmax")
    sd_default_free = summary.get("simulation_domain_default_free", False)

    print(f"Simulation domain explicit: {sd_explicit}")

    if sd_pointmin:
        print(
            "Simulation domain pointmin: "
            f"({_fmt(sd_pointmin[0])}, {_fmt(sd_pointmin[1])}, {_fmt(sd_pointmin[2])})"
        )
    else:
        print("Simulation domain pointmin: None")

    if sd_pointmax:
        print(
            "Simulation domain pointmax: "
            f"({_fmt(sd_pointmax[0])}, {_fmt(sd_pointmax[1])}, {_fmt(sd_pointmax[2])})"
        )
    else:
        print("Simulation domain pointmax: None")

    if sd_default_free:
        print("Simulation domain contains no 'default' values.")
    else:
        print("Simulation domain still contains invalid or default values.")

    print("DualSPHysics run attempted: False")
    print()

    upstream = summary["upstream"]
    print("Upstream estimate:")
    print(f"  flow axis: {upstream['flow_axis']}")
    print(f"  upstream sign: {upstream['upstream_sign']:+.0f}")
    print(f"  method: {upstream['method']}")
    print()

    dam = summary["dam"]
    print("Dam:")
    print(f"  center: x={_fmt(dam['center_x'])}, y={_fmt(dam['center_y'])}, z={_fmt(dam['center_z'])}")
    print(f"  centerline endpoints: {dam['centerline_endpoints']}")
    print(f"  span: {_fmt(dam['span_start'])} to {_fmt(dam['span_end'])}")
    print(f"  thickness: {_fmt(dam['thickness'])}")
    print(f"  common crest_z: {_fmt(dam['crest_z'])}")
    print(f"  actual wall z1 min/max: {_fmt(dam['wall_z1_min'])} to {_fmt(dam['wall_z1_max'])}")
    print(f"  wall bottom range: {_fmt(dam['wall_bottom_min'])} to {_fmt(dam['wall_bottom_max'])}")
    print(f"  local terrain range: {_fmt(dam['local_terrain_min'])} to {_fmt(dam['local_terrain_max'])}")
    print(f"  fixed left width: {_fmt(dam['fixed_left_width'])}")
    print(f"  fixed right width: {_fmt(dam['fixed_right_width'])}")
    print(f"  fixed segment count: {dam['fixed_segment_count']}")
    print()

    reservoir = summary["reservoir"]
    print("Reservoir:")
    print(f"  bounds x: {_fmt(reservoir['x_min'])} to {_fmt(reservoir['x_max'])}")
    print(f"  bounds y: {_fmt(reservoir['y_min'])} to {_fmt(reservoir['y_max'])}")
    print(f"  bounds z: {_fmt(reservoir['z_min'])} to {_fmt(reservoir['z_max'])}")
    print(f"  common water_surface_z: {_fmt(reservoir['water_surface_z'])}")
    print(f"  actual fluid z1 min/max: {_fmt(reservoir['fluid_z1_min'])} to {_fmt(reservoir['fluid_z1_max'])}")
    print(f"  minimum water depth: {_fmt(reservoir['water_depth_min'])}")
    print(f"  maximum water depth: {_fmt(reservoir['water_depth_max'])}")
    print(f"  fluid segment count: {reservoir['fluid_segment_count']}")
    print()

    breach = summary["breach"]
    print("Breach:")
    print(f"  enabled: {breach['enabled']}")
    if breach["enabled"]:
        print(f"  center: {_fmt(breach['center'])}")
        print(f"  width: {_fmt(breach['width'])}")
        print(f"  breach time: {_fmt(breach['breach_time'])}")
        print(f"  lift duration: {_fmt(breach['lift_duration'])}")
        print(f"  extra lift above crest: {_fmt(breach['extra_lift_above_crest'])}")
        print(f"  actual gate lift: {_fmt(breach['actual_gate_lift'])}")
        print(f"  gate initial Z: {_fmt(breach['gate_initial_z'])}")
        print(f"  gate final Z: {_fmt(breach['gate_final_z'])}")
        print(f"  gate final top Z: {_fmt(breach['gate_top_final_z'])}")
        print(f"  motion file: {breach['motion_file']}")
    print()

    print("Outputs:")
    outputs = summary["outputs"]
    print(f"  standalone XML: {outputs['definition_xml']}")
    print(f"  runner XML: {outputs['runner_definition_xml']}")
    print(f"  summary JSON: {outputs['summary_json']}")
    print()

    if summary["validation"]["errors"]:
        print("Validation errors:")
        for error in summary["validation"]["errors"]:
            print(f"  ERROR: {error}")
    else:
        print("Validation passed. No geometry validation errors detected.")

    print()
    print("Next step: inspect this summary. Do NOT run DualSPHysics until approved.")


def generate_case(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    if not config_path.exists():
        raise TerrainCaseError(f"Config file not found: {config_path}")

    cfg = json.loads(config_path.read_text(encoding="utf-8"))

    case_name = str(cfg.get("case_name", "HADR_TerrainChouldari"))

    particle_spacing = _positive(cfg, "particle_spacing", 0.10)
    simulation_time = _positive(cfg, "simulation_time", 0.75)
    time_out = _positive(cfg, "time_out", 0.05)

    domain_margin_xy = _non_negative(cfg, "domain_margin_xy", 1.0)
    domain_margin_z_bottom = _non_negative(cfg, "domain_margin_z_bottom", 0.5)
    domain_margin_z_top = _non_negative(cfg, "domain_margin_z_top", 1.0)

    terrain = _load_terrain(cfg)

    x = terrain["x"]
    y = terrain["y"]
    z = terrain["z"]

    dam_x = terrain["dam_x"]
    dam_y = terrain["dam_y"]
    dam_z = terrain["dam_z"]

    terrain_x_min = float(np.min(x))
    terrain_x_max = float(np.max(x))
    terrain_y_min = float(np.min(y))
    terrain_y_max = float(np.max(y))
    terrain_z_min = float(np.min(z))
    terrain_z_max = float(np.max(z))

    dam_search_radius = float(cfg.get("dam_search_radius", 8.0))

    force_axis = cfg.get("force_upstream_axis")
    force_sign = cfg.get("force_upstream_sign")

    if force_axis in {"x", "y"}:
        flow_axis = str(force_axis)
        upstream_sign = -1.0 if force_sign is not None and float(force_sign) < 0 else 1.0
        upstream_method = "forced-by-config"
    else:
        flow_axis, upstream_sign, upstream_method = _estimate_upstream_axis(
            x, y, z, dam_x, dam_y, dam_search_radius,
        )

    span_rel_min, span_rel_max = _estimate_dam_span(
        x, y, z, dam_x, dam_y, flow_axis, cfg, particle_spacing,
    )

    if flow_axis == "x":
        dam_axis_center = dam_y
        endpoints = [
            [dam_x, dam_y + span_rel_min],
            [dam_x, dam_y + span_rel_max],
        ]
    else:
        dam_axis_center = dam_x
        endpoints = [
            [dam_x + span_rel_min, dam_y],
            [dam_x + span_rel_max, dam_y],
        ]

    span_start = dam_axis_center + span_rel_min
    span_end = dam_axis_center + span_rel_max

    local_stats = _cell_stats(
        x, y, z,
        dam_x - dam_search_radius, dam_x + dam_search_radius,
        dam_y - dam_search_radius, dam_y + dam_search_radius,
    )

    local_terrain_min = local_stats["min"] if local_stats else terrain_z_min
    local_terrain_max = local_stats["max"] if local_stats else terrain_z_max

    center_stats = _cell_stats(
        x, y, z,
        dam_x - max(particle_spacing, 0.25),
        dam_x + max(particle_spacing, 0.25),
        dam_y - max(particle_spacing, 0.25),
        dam_y + max(particle_spacing, 0.25),
    )

    dam_center_z = center_stats["median"] if center_stats else dam_z

    dam_thickness = _positive(cfg, "dam_thickness", 0.30)
    dam_crest_height = _positive(cfg, "dam_crest_height", 0.60)
    dam_embedment = _non_negative(cfg, "dam_embedment", 0.10)
    dam_segment_length = _positive(cfg, "dam_segment_length", 0.50)

    # Common dam crest elevation derived from dam center terrain reference.
    dam_crest_z = dam_center_z + dam_crest_height

    breach_enabled_cfg = bool(cfg.get("breach_enabled", True))
    breach_width_cfg = _non_negative(cfg, "breach_width", 1.0)
    breach_time = _non_negative(cfg, "breach_time", 0.25)
    breach_lift_duration = _positive(cfg, "breach_lift_duration", 0.30)
    breach_lift_distance_extra = _non_negative(cfg, "breach_lift_distance", 0.50)
    breach_motion_steps = int(cfg.get("breach_motion_steps", 50))

    if breach_motion_steps <= 0:
        raise TerrainCaseError("breach_motion_steps must be positive")

    if breach_time + breach_lift_duration > simulation_time + 1e-6:
        raise TerrainCaseError(
            "breach_time + breach_lift_duration must be <= simulation_time"
        )

    span_width = span_end - span_start
    minimum_side_width = max(0.25, 2.0 * particle_spacing)
    maximum_breach_width = max(0.10, span_width - 2.0 * minimum_side_width)

    breach_enabled = breach_enabled_cfg
    breach_width = min(max(breach_width_cfg, 0.0), maximum_breach_width)

    if breach_enabled and breach_width <= 1e-3:
        breach_enabled = False

    breach_center = dam_axis_center
    breach_start = breach_center - breach_width / 2.0
    breach_end = breach_center + breach_width / 2.0

    fixed_left_width = breach_start - span_start
    fixed_right_width = span_end - breach_end

    if not breach_enabled:
        breach_width = 0.0
        breach_start = breach_center
        breach_end = breach_center
        fixed_left_width = span_width / 2.0
        fixed_right_width = span_width / 2.0

    fixed_boxes: list[WallBox] = []

    if breach_enabled:
        if fixed_left_width > 1e-3:
            fixed_boxes.extend(
                _make_wall_boxes(
                    flow_axis=flow_axis,
                    dam_x=dam_x,
                    dam_y=dam_y,
                    thickness=dam_thickness,
                    interval_start=span_start,
                    interval_end=breach_start,
                    segment_length=dam_segment_length,
                    x=x, y=y, z=z,
                    embedment=dam_embedment,
                    common_crest_z=dam_crest_z,
                    default_ground_z=dam_center_z,
                    kind="fixed",
                )
            )

        if fixed_right_width > 1e-3:
            fixed_boxes.extend(
                _make_wall_boxes(
                    flow_axis=flow_axis,
                    dam_x=dam_x,
                    dam_y=dam_y,
                    thickness=dam_thickness,
                    interval_start=breach_end,
                    interval_end=span_end,
                    segment_length=dam_segment_length,
                    x=x, y=y, z=z,
                    embedment=dam_embedment,
                    common_crest_z=dam_crest_z,
                    default_ground_z=dam_center_z,
                    kind="fixed",
                )
            )

        breach_boxes = _make_wall_boxes(
            flow_axis=flow_axis,
            dam_x=dam_x,
            dam_y=dam_y,
            thickness=dam_thickness,
            interval_start=breach_start,
            interval_end=breach_end,
            segment_length=max(breach_width, 0.1),
            x=x, y=y, z=z,
            embedment=dam_embedment,
            common_crest_z=dam_crest_z,
            default_ground_z=dam_center_z,
            kind="breach",
        )

        breach_box = breach_boxes[0] if breach_boxes else None
    else:
        fixed_boxes.extend(
            _make_wall_boxes(
                flow_axis=flow_axis,
                dam_x=dam_x,
                dam_y=dam_y,
                thickness=dam_thickness,
                interval_start=span_start,
                interval_end=span_end,
                segment_length=dam_segment_length,
                x=x, y=y, z=z,
                embedment=dam_embedment,
                common_crest_z=dam_crest_z,
                default_ground_z=dam_center_z,
                kind="fixed",
            )
        )
        breach_box = None

    gate_initial_z = None
    gate_final_z = None
    gate_top_final_z = None
    actual_gate_lift = None

    if breach_enabled and breach_box is not None:
        gate_height = breach_box.z1 - breach_box.z0
        actual_gate_lift = gate_height + breach_lift_distance_extra
        gate_initial_z = breach_box.z0
        gate_final_z = breach_box.z0 + actual_gate_lift
        gate_top_final_z = breach_box.z1 + actual_gate_lift

    reservoir_length = _positive(cfg, "reservoir_length", 3.0)
    reservoir_water_depth = _positive(cfg, "reservoir_water_depth", 0.50)
    reservoir_segments = int(cfg.get("reservoir_segments", 6))
    fluid_bed_clearance = _non_negative(cfg, "fluid_bed_clearance", 0.02)

    if reservoir_segments <= 0:
        reservoir_segments = 1

    reservoir_boxes, common_water_surface_z = _make_reservoir_boxes(
        flow_axis=flow_axis,
        upstream_sign=upstream_sign,
        dam_x=dam_x,
        dam_y=dam_y,
        thickness=dam_thickness,
        span_start=span_start,
        span_end=span_end,
        reservoir_length=reservoir_length,
        reservoir_segments=reservoir_segments,
        water_depth=reservoir_water_depth,
        fluid_bed_clearance=fluid_bed_clearance,
        particle_spacing=particle_spacing,
        x=x, y=y, z=z,
        default_ground_z=dam_center_z,
    )

    reservoir = ReservoirGeometry(boxes=reservoir_boxes)

    if reservoir_boxes:
        reservoir.x_min = min(box.x0 for box in reservoir_boxes)
        reservoir.x_max = max(box.x1 for box in reservoir_boxes)
        reservoir.y_min = min(box.y0 for box in reservoir_boxes)
        reservoir.y_max = max(box.y1 for box in reservoir_boxes)
        reservoir.z_min = min(box.z0 for box in reservoir_boxes)
        reservoir.z_max = max(box.z1 for box in reservoir_boxes)
        reservoir.water_surface_z = common_water_surface_z
        reservoir.water_depth_min = min(box.water_depth for box in reservoir_boxes)
        reservoir.water_depth_max = max(box.water_depth for box in reservoir_boxes)

    wall_bottom_values = [box.z0 for box in fixed_boxes]
    wall_z1_values = [box.z1 for box in fixed_boxes]
    if breach_box is not None:
        wall_bottom_values.append(breach_box.z0)
        wall_z1_values.append(breach_box.z1)

    fluid_z1_values = [box.z1 for box in reservoir_boxes]

    dam_geometry = DamGeometry(
        flow_axis=flow_axis,
        upstream_sign=upstream_sign,
        upstream_method=upstream_method,
        center_x=dam_x,
        center_y=dam_y,
        center_z=dam_center_z,
        span_start=span_start,
        span_end=span_end,
        endpoints=endpoints,
        thickness=dam_thickness,
        crest_z=dam_crest_z,
        wall_bottom_min=min(wall_bottom_values) if wall_bottom_values else None,
        wall_bottom_max=max(wall_bottom_values) if wall_bottom_values else None,
        local_terrain_min=local_terrain_min,
        local_terrain_max=local_terrain_max,
        fixed_boxes=fixed_boxes,
        breach_box=breach_box,
        breach_center=breach_center if breach_enabled else None,
        breach_width=breach_width if breach_enabled else None,
        fixed_left_width=fixed_left_width if breach_enabled else None,
        fixed_right_width=fixed_right_width if breach_enabled else None,
        gate_initial_z=gate_initial_z,
        gate_final_z=gate_final_z,
        gate_top_final_z=gate_top_final_z,
        gate_lift_distance=actual_gate_lift,
    )

    # Domain bounds.
    x_min_candidates = [terrain_x_min]
    x_max_candidates = [terrain_x_max]
    y_min_candidates = [terrain_y_min]
    y_max_candidates = [terrain_y_max]
    z_min_candidates = [terrain_z_min]
    z_max_candidates = [terrain_z_max]

    for box in fixed_boxes:
        x_min_candidates.append(box.x0)
        x_max_candidates.append(box.x1)
        y_min_candidates.append(box.y0)
        y_max_candidates.append(box.y1)
        z_min_candidates.append(box.z0)
        z_max_candidates.append(box.z1)

    if breach_box is not None:
        x_min_candidates.append(breach_box.x0)
        x_max_candidates.append(breach_box.x1)
        y_min_candidates.append(breach_box.y0)
        y_max_candidates.append(breach_box.y1)
        z_min_candidates.append(breach_box.z0)
        z_max_candidates.append(breach_box.z1)
        if gate_top_final_z is not None:
            z_max_candidates.append(gate_top_final_z)

    for box in reservoir_boxes:
        x_min_candidates.append(box.x0)
        x_max_candidates.append(box.x1)
        y_min_candidates.append(box.y0)
        y_max_candidates.append(box.y1)
        z_min_candidates.append(box.z0)
        z_max_candidates.append(box.z1)

    pointmin = (
        min(x_min_candidates) - domain_margin_xy,
        min(y_min_candidates) - domain_margin_xy,
        min(z_min_candidates) - domain_margin_z_bottom,
    )

    pointmax = (
        max(x_max_candidates) + domain_margin_xy,
        max(y_max_candidates) + domain_margin_xy,
        max(z_max_candidates) + domain_margin_z_top,
    )

    

    # STL handling.
    stl_source = _resolve_path(cfg["terrain_stl"])
    if not stl_source.exists():
        raise TerrainCaseError(f"Terrain STL not found: {stl_source}")

    terrain_dir = CASE_DIR / str(cfg.get("terrain_output_subdir", "terrain"))
    terrain_dir.mkdir(parents=True, exist_ok=True)

    terrain_stl_copy = terrain_dir / stl_source.name
    if stl_source.resolve() != terrain_stl_copy.resolve():
        shutil.copyfile(stl_source, terrain_stl_copy)

    stl_copy = CASE_DIR / stl_source.name
    if terrain_stl_copy.resolve() != stl_copy.resolve():
        shutil.copyfile(terrain_stl_copy, stl_copy)

    standalone_stl_reference = stl_copy.name
    runner_stl_reference = stl_copy.resolve().as_posix()

    # Motion file.
    motion_path = CASE_DIR / f"{case_name}_gate_motion.txt"
    motion_duration = None

    if breach_enabled and breach_box is not None and actual_gate_lift is not None:
        motion_duration = _write_gate_motion(
            motion_path=motion_path,
            initial_x=breach_box.x0,
            initial_y=breach_box.y0,
            initial_z=breach_box.z0,
            breach_time=breach_time,
            simulation_time=simulation_time,
            lift_duration=breach_lift_duration,
            lift_distance=actual_gate_lift,
            motion_steps=breach_motion_steps,
        )
        standalone_motion_reference = motion_path.name
        runner_motion_reference = motion_path.resolve().as_posix()
    else:
        if motion_path.exists():
            motion_path.unlink()
        standalone_motion_reference = None
        runner_motion_reference = None

    standalone_xml = _build_xml(
        cfg=cfg,
        geom=dam_geometry,
        reservoir=reservoir,
        pointmin=pointmin,
        pointmax=pointmax,
        stl_reference=standalone_stl_reference,
        motion_reference=standalone_motion_reference,
        motion_duration=motion_duration,
    )
    runner_xml = _build_xml(
        cfg=cfg,
        geom=dam_geometry,
        reservoir=reservoir,
        pointmin=pointmin,
        pointmax=pointmax,
        stl_reference=runner_stl_reference,
        motion_reference=runner_motion_reference,
        motion_duration=motion_duration,
    )

    definition_name = str(cfg.get("definition_name", f"{case_name}_Def.xml"))
    runner_definition_name = str(
        cfg.get("runner_definition_name", f"{case_name}_runner_Def.xml")
    )
    summary_name = str(cfg.get("summary_name", "terrain_case_summary.json"))

    definition_path = CASE_DIR / definition_name
    runner_definition_path = CASE_DIR / runner_definition_name
    summary_path = CASE_DIR / summary_name

    definition_path.write_text(standalone_xml, encoding="utf-8")
    runner_definition_path.write_text(runner_xml, encoding="utf-8")

    # --- Simulation Domain Validation ---
    wall_boxes_all = list(fixed_boxes)
    if breach_box is not None:
        wall_boxes_all.append(breach_box)

    # Build dictionary representations for bounds calculation
    wall_boxes_dicts = [
        {"x0": b.x0, "x1": b.x1, "y0": b.y0, "y1": b.y1, "z0": b.z0, "z1": b.z1}
        for b in wall_boxes_all
    ]
    reservoir_boxes_dicts = [
        {"x0": b.x0, "x1": b.x1, "y0": b.y0, "y1": b.y1, "z0": b.z0, "z1": b.z1}
        for b in reservoir_boxes
    ]
    
    breach_final_box_dict = None
    if breach_enabled and breach_box is not None and actual_gate_lift is not None:
        breach_final_box_dict = {
            "x0": breach_box.x0, "x1": breach_box.x1,
            "y0": breach_box.y0, "y1": breach_box.y1,
            "z0": breach_box.z0 + actual_gate_lift,
            "z1": breach_box.z1 + actual_gate_lift,
        }

    terrain_bounds = {
        "x_min": terrain_x_min,
        "x_max": terrain_x_max,
        "y_min": terrain_y_min,
        "y_max": terrain_y_max,
        "z_min": terrain_z_min,
        "z_max": terrain_z_max,
    }

    required_domain_bounds = {
        "terrain": terrain_bounds,
        "reservoir": _bounds_from_boxes(reservoir_boxes_dicts),
        "dam_breach_initial": _bounds_from_boxes(wall_boxes_dicts),
        "final_lifted_breach": (
            _bounds_from_boxes([breach_final_box_dict])
            if breach_final_box_dict is not None
            else None
        ),
    }

    domain_validation_standalone = _validate_simulation_domain_xml(
        definition_path,
        required_domain_bounds,
    )

    domain_validation_runner = _validate_simulation_domain_xml(
        runner_definition_path,
        required_domain_bounds,
    )

    validation = _validate_geometry(
        cfg=cfg,
        geom=dam_geometry,
        reservoir=reservoir,
        pointmin=pointmin,
        pointmax=pointmax,
        motion_path=motion_path,
        standalone_xml=definition_path,
        runner_xml=runner_definition_path,
        simulation_time=simulation_time,
    )

    domain_errors = []
    domain_errors.extend(domain_validation_standalone.get("errors", []))
    domain_errors.extend(domain_validation_runner.get("errors", []))

    if domain_errors:
        validation["errors"].extend(domain_errors)
        validation["status"] = "fail"

    validation["simulation_domain_standalone"] = domain_validation_standalone
    validation["simulation_domain_runner"] = domain_validation_runner

    moving_bounds_initial = None
    moving_bounds_final = None

    if breach_enabled and breach_box is not None and actual_gate_lift is not None:
        moving_bounds_initial = {
            "x0": breach_box.x0, "x1": breach_box.x1,
            "y0": breach_box.y0, "y1": breach_box.y1,
            "z0": breach_box.z0, "z1": breach_box.z1,
        }
        moving_bounds_final = {
            "x0": breach_box.x0, "x1": breach_box.x1,
            "y0": breach_box.y0, "y1": breach_box.y1,
            "z0": breach_box.z0 + actual_gate_lift,
            "z1": breach_box.z1 + actual_gate_lift,
        }

    summary = {
        "case_name": case_name,
        "status": "experimental_terrain_breach_redesign",
        "limitations": [
            "Terrain source is Copernicus GLO-30 DSM data.",
            "Terrain coordinates are numerically scaled prototype coordinates.",
            "The dam/reservoir geometry is terrain-anchored but still experimental.",
            "This is not a validated physical flood model.",
            "Hydrological calibration is still required.",
        ],
        "inputs": {
            "terrain_npz": str(terrain["npz_path"]),
            "terrain_stl_source": str(stl_source),
            "terrain_stl_copy": str(stl_copy),
        },
        "simulation": {
            "particle_spacing": particle_spacing,
            "simulation_time": simulation_time,
            "time_out": time_out,
        },
        "upstream": {
            "flow_axis": flow_axis,
            "upstream_sign": upstream_sign,
            "method": upstream_method,
        },
        "dam": {
            "center_x": dam_x,
            "center_y": dam_y,
            "center_z": dam_center_z,
            "centerline_endpoints": endpoints,
            "span_start": span_start,
            "span_end": span_end,
            "thickness": dam_thickness,
            "crest_z": dam_crest_z,
            "wall_z1_min": min(wall_z1_values) if wall_z1_values else None,
            "wall_z1_max": max(wall_z1_values) if wall_z1_values else None,
            "wall_bottom_min": dam_geometry.wall_bottom_min,
            "wall_bottom_max": dam_geometry.wall_bottom_max,
            "local_terrain_min": local_terrain_min,
            "local_terrain_max": local_terrain_max,
            "fixed_left_width": fixed_left_width if breach_enabled else None,
            "fixed_right_width": fixed_right_width if breach_enabled else None,
            "fixed_segment_count": len(fixed_boxes),
        },
        "reservoir": {
            "x_min": reservoir.x_min,
            "x_max": reservoir.x_max,
            "y_min": reservoir.y_min,
            "y_max": reservoir.y_max,
            "z_min": reservoir.z_min,
            "z_max": reservoir.z_max,
            "water_surface_z": reservoir.water_surface_z,
            "fluid_z1_min": min(fluid_z1_values) if fluid_z1_values else None,
            "fluid_z1_max": max(fluid_z1_values) if fluid_z1_values else None,
            "water_depth_min": reservoir.water_depth_min,
            "water_depth_max": reservoir.water_depth_max,
            "fluid_segment_count": len(reservoir_boxes),
        },
        "breach": {
            "enabled": breach_enabled,
            "center": breach_center if breach_enabled else None,
            "width": breach_width if breach_enabled else None,
            "breach_time": breach_time if breach_enabled else None,
            "lift_duration": breach_lift_duration if breach_enabled else None,
            "extra_lift_above_crest": breach_lift_distance_extra if breach_enabled else None,
            "actual_gate_lift": actual_gate_lift,
            "gate_initial_z": gate_initial_z,
            "gate_final_z": gate_final_z,
            "gate_top_final_z": gate_top_final_z,
            "moving_bounds_initial": moving_bounds_initial,
            "moving_bounds_final": moving_bounds_final,
            "motion_file": str(motion_path) if breach_enabled else None,
            "motion_duration": motion_duration,
        },
        "bounds": {
            "terrain": {
                "x_min": terrain_x_min, "x_max": terrain_x_max,
                "y_min": terrain_y_min, "y_max": terrain_y_max,
                "z_min": terrain_z_min, "z_max": terrain_z_max,
            },
            "pointmin": list(pointmin),
            "pointmax": list(pointmax),
        },
        "simulation_domain_explicit": True,
        "simulation_domain_pointmin": list(pointmin),
        "simulation_domain_pointmax": list(pointmax),
        "simulation_domain_default_free": bool(
            domain_validation_standalone.get("default_free", False)
            and domain_validation_runner.get("default_free", False)
        ),
        "simulation_domain_validation": {
            "standalone": domain_validation_standalone,
            "runner": domain_validation_runner,
        },
        "validation": validation,
        "outputs": {
            "definition_xml": str(definition_path),
            "runner_definition_xml": str(runner_definition_path),
            "summary_json": str(summary_path),
        },
    }

    summary_path.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    _print_summary(summary)

    return summary


def main() -> int:
    try:
        summary = generate_case()
    except TerrainCaseError as exc:
        print(f"ERROR: {exc}")
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    validation = summary.get("validation", {})
    if validation.get("status") != "pass":
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())