"""
Generate the experimental HADR_TerrainChouldari DualSPHysics case.

This script creates a new terrain-based DualSPHysics case from the
intermediate DEM-derived terrain representation.

It does NOT:
- generate SPH particles directly
- run GenCase
- run DualSPHysics
- modify the existing synthetic dam-break case
- modify backend scenario behavior
- modify the frontend
- modify Delft3D integration

IMPORTANT LIMITATIONS:
- Terrain source is Copernicus GLO-30 DSM data.
- Terrain coordinates are already numerically scaled by the prototype scale.
- This is an integration test, not a validated physical flood model.
- Hydrological calibration is still required.
- The final SIH model will later incorporate hydrological data, satellite data,
  SPH/Delft3D comparison, and GIS outputs.
"""

from __future__ import annotations

import json
import math
import shutil
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np


CASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CASE_DIR.parents[2]
DEFAULT_CONFIG_PATH = CASE_DIR / "terrain_case_config.json"

BREACH_GATE_MK = 200
MOTION_EPS = 1e-6


class TerrainCaseError(Exception):
    """Base exception for terrain case generation errors."""


@dataclass
class TerrainCaseGeometry:
    scale: float
    terrain_samples: int

    terrain_x_min: float
    terrain_x_max: float
    terrain_y_min: float
    terrain_y_max: float
    terrain_z_min: float
    terrain_z_max: float

    dam_x: float
    dam_y: float
    dam_z: float
    dam_nearest_distance: float
    dam_coordinate_source: str

    upstream_x: float
    upstream_y: float
    upstream_axis: str
    upstream_sign: float
    upstream_method: str
    upstream_points: int
    upstream_mean_elevation: float | None

    reservoir_center_x: float
    reservoir_center_y: float
    reservoir_x0: float
    reservoir_x1: float
    reservoir_y0: float
    reservoir_y1: float
    reservoir_length: float
    reservoir_width: float
    reservoir_depth: float
    reservoir_base_z: float
    reservoir_top_z: float
    reservoir_terrain_points: int
    reservoir_terrain_min_z: float | None
    reservoir_terrain_max_z: float | None
    reservoir_terrain_mean_z: float | None

    wall_enabled: bool
    wall_thickness: float | None
    wall_side_margin: float | None
    wall_x0: float | None
    wall_x1: float | None
    wall_y0: float | None
    wall_y1: float | None
    wall_base_z: float | None
    wall_top_z: float | None

    pointmin_x: float
    pointmin_y: float
    pointmin_z: float
    pointmax_x: float
    pointmax_y: float
    pointmax_z: float


def _resolve_path(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _positive(cfg: dict, key: str) -> float:
    value = float(cfg[key])
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


def _nearest_z(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    target_x: float,
    target_y: float,
) -> tuple[float, float]:
    distances_sq = (x - target_x) ** 2 + (y - target_y) ** 2
    idx = int(np.argmin(distances_sq))
    return float(z[idx]), float(math.sqrt(distances_sq[idx]))


def _box_stats(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
) -> dict | None:
    mask = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
    count = int(mask.sum())

    if count == 0:
        return None

    values = z[mask]

    return {
        "count": count,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
    }


def _parse_ascii_stl_bounds(path: Path) -> dict | None:
    """
    Lightweight ASCII STL bounds reader.
    """

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    min_x = math.inf
    min_y = math.inf
    min_z = math.inf
    max_x = -math.inf
    max_y = -math.inf
    max_z = -math.inf

    found = False

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped.startswith("vertex"):
            continue

        parts = stripped.split()
        if len(parts) < 4:
            continue

        try:
            vx = float(parts[1])
            vy = float(parts[2])
            vz = float(parts[3])
        except ValueError:
            continue

        found = True

        min_x = min(min_x, vx)
        min_y = min(min_y, vy)
        min_z = min(min_z, vz)

        max_x = max(max_x, vx)
        max_y = max(max_y, vy)
        max_z = max(max_z, vz)

    if not found:
        return None

    return {
        "x0": float(min_x),
        "x1": float(max_x),
        "y0": float(min_y),
        "y1": float(max_y),
        "z0": float(min_z),
        "z1": float(max_z),
    }


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

    dam_z, dam_nearest_distance = _nearest_z(x, y, z, dam_x, dam_y)

    return {
        "npz_path": npz_path,
        "x": x,
        "y": y,
        "z": z,
        "scale": scale,
        "dam_x": dam_x,
        "dam_y": dam_y,
        "dam_z": dam_z,
        "dam_nearest_distance": dam_nearest_distance,
        "dam_coordinate_source": dam_coordinate_source,
    }


def _estimate_upstream_direction(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    dam_x: float,
    dam_y: float,
    radius: float,
    inner_radius: float,
    default_direction: tuple[float, float],
) -> tuple[tuple[float, float], str, int, float | None]:
    norm = math.hypot(default_direction[0], default_direction[1])
    if norm <= 0.0:
        default_direction = (1.0, 0.0)
    else:
        default_direction = (
            default_direction[0] / norm,
            default_direction[1] / norm,
        )

    dx = x - dam_x
    dy = y - dam_y
    dist = np.hypot(dx, dy)

    base = (dist > inner_radius) & (dist <= radius)

    if not np.any(base):
        return default_direction, "fallback-no-local-terrain", 0, None

    directions = [
        (math.cos(i * math.pi / 4.0), math.sin(i * math.pi / 4.0))
        for i in range(8)
    ]

    best_dir = default_direction
    best_score: float | None = None
    best_count = 0
    method = "sector-mean-elevation"

    for ux, uy in directions:
        projection = dx * ux + dy * uy
        selected = base & (projection >= 0.5 * dist)
        count = int(selected.sum())

        if count >= 3:
            score = float(np.mean(z[selected]))
            if best_score is None or score > best_score:
                best_score = score
                best_dir = (ux, uy)
                best_count = count

    if best_count > 0:
        return best_dir, method, best_count, best_score

    best_score = None
    best_count = 0
    method = "weighted-mean-elevation"

    for ux, uy in directions:
        projection = dx * ux + dy * uy
        weights = np.where(
            base,
            np.maximum(projection / np.maximum(dist, 1e-9), 0.0),
            0.0,
        )
        weight_sum = float(np.sum(weights))

        if weight_sum > 1e-9:
            score = float(np.sum(weights * z) / weight_sum)
            count = int(np.sum(weights > 0.0))

            if best_score is None or score > best_score:
                best_score = score
                best_dir = (ux, uy)
                best_count = count

    if best_count > 0:
        return best_dir, method, best_count, best_score

    return default_direction, "fallback-default-direction", 0, None


def _reservoir_footprint(
    dam_x: float,
    dam_y: float,
    axis: str,
    sign: float,
    offset: float,
    length: float,
    width: float,
    terrain_bounds: tuple[float, float, float, float],
    edge_margin: float,
) -> tuple[float, float, float, float, float, float]:
    if axis == "x":
        center_x = dam_x + sign * offset
        center_y = dam_y
        half_x = length / 2.0
        half_y = width / 2.0
    else:
        center_x = dam_x
        center_y = dam_y + sign * offset
        half_x = width / 2.0
        half_y = length / 2.0

    min_x, max_x, min_y, max_y = terrain_bounds

    allowed_min_x = min_x + edge_margin
    allowed_max_x = max_x - edge_margin
    allowed_min_y = min_y + edge_margin
    allowed_max_y = max_y - edge_margin

    if allowed_max_x > allowed_min_x:
        span_x = 2.0 * half_x
        allowed_span_x = allowed_max_x - allowed_min_x

        if span_x >= allowed_span_x:
            center_x = (allowed_min_x + allowed_max_x) / 2.0
        else:
            center_x = min(
                max(center_x, allowed_min_x + half_x),
                allowed_max_x - half_x,
            )

    if allowed_max_y > allowed_min_y:
        span_y = 2.0 * half_y
        allowed_span_y = allowed_max_y - allowed_min_y

        if span_y >= allowed_span_y:
            center_y = (allowed_min_y + allowed_max_y) / 2.0
        else:
            center_y = min(
                max(center_y, allowed_min_y + half_y),
                allowed_max_y - half_y,
            )

    x0 = center_x - half_x
    x1 = center_x + half_x
    y0 = center_y - half_y
    y1 = center_y + half_y

    return center_x, center_y, x0, x1, y0, y1


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


def _build_simulation_domain_xml(simulation_posmax_z: float | None) -> str:
    if simulation_posmax_z is None:
        return """            <simulationdomain>

                <posmin
                    x="default"
                    y="default"
                    z="default" />

                <posmax
                    x="default"
                    y="default"
                    z="default + 50%" />

            </simulationdomain>"""

    return f"""            <simulationdomain>

                <posmin
                    x="default"
                    y="default"
                    z="default" />

                <posmax
                    x="default"
                    y="default"
                    z="{_f(simulation_posmax_z)}" />

            </simulationdomain>"""


def _build_wall_fragment(
    geom: TerrainCaseGeometry,
    particle_spacing: float,
    breach_enabled: bool = False,
) -> str:
    if not geom.wall_enabled:
        return ""

    wall_x0 = geom.wall_x0
    wall_x1 = geom.wall_x1
    wall_y0 = geom.wall_y0
    wall_y1 = geom.wall_y1
    wall_base_z = geom.wall_base_z
    wall_top_z = geom.wall_top_z

    if (
        wall_x0 is None
        or wall_x1 is None
        or wall_y0 is None
        or wall_y1 is None
        or wall_base_z is None
        or wall_top_z is None
    ):
        return ""

    size_x = max(wall_x1 - wall_x0, particle_spacing)
    size_y = max(wall_y1 - wall_y0, particle_spacing)
    size_z = max(wall_top_z - wall_base_z, particle_spacing)

    if breach_enabled:
        wall_comment = """                    <!-- Experimental moving breach gate -->
                    <!-- Prototype timing only; not observed Chouldari failure data -->"""
        mk = BREACH_GATE_MK
    else:
        wall_comment = """                    <!-- Temporary fixed test dam wall -->
                    <!-- Experimental containment only, not the final dam or breach -->"""
        mk = 1

    return f"""{wall_comment}

                    <setmkbound mk="{mk}" />

                    <drawbox>

                        <boxfill>
                            solid
                        </boxfill>

                        <point
                            x="{_f(wall_x0)}"
                            y="{_f(wall_y0)}"
                            z="{_f(wall_base_z)}" />

                        <size
                            x="{_f(size_x)}"
                            y="{_f(size_y)}"
                            z="{_f(size_z)}" />

                    </drawbox>

"""


def _build_xml(
    stl_file: str,
    motion_file: str | None,
    cfg: dict,
    geom: TerrainCaseGeometry,
    breach_enabled: bool,
    motion_duration: float | None,
    simulation_posmax_z: float | None,
) -> str:
    particle_spacing = float(cfg["particle_spacing"])
    simulation_time = float(cfg["simulation_time"])
    time_out = float(cfg["time_out"])

    reservoir_x = geom.reservoir_x0
    reservoir_y = geom.reservoir_y0
    reservoir_z = geom.reservoir_base_z

    reservoir_size_x = max(
        geom.reservoir_x1 - geom.reservoir_x0,
        particle_spacing,
    )
    reservoir_size_y = max(
        geom.reservoir_y1 - geom.reservoir_y0,
        particle_spacing,
    )
    reservoir_size_z = max(
        geom.reservoir_depth,
        particle_spacing,
    )

    wall_xml = _build_wall_fragment(
        geom,
        particle_spacing,
        breach_enabled=breach_enabled,
    )
    stl_attribute = quoteattr(stl_file)

    motion_xml = ""
    if (
        breach_enabled
        and motion_file is not None
        and motion_duration is not None
    ):
        motion_xml = _build_motion_xml(motion_file, motion_duration)

    simulation_domain_xml = _build_simulation_domain_xml(simulation_posmax_z)

    # Conditional comment to accurately reflect terrain vs gate state
    if breach_enabled:
        terrain_comment = """                    <!-- Experimental DEM terrain surface -->
                    <!-- Source: Copernicus GLO-30 DSM -->
                    <!-- Coordinates are already numerically scaled -->
                    <!-- Terrain is fixed; experimental breach gate is moving -->"""
    else:
        terrain_comment = """                    <!-- Experimental DEM terrain surface -->
                    <!-- Source: Copernicus GLO-30 DSM -->
                    <!-- Coordinates are already numerically scaled -->
                    <!-- Fixed boundary only; no motion is applied -->"""

    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<case>

    <casedef>

        <constantsdef>

            <gravity
                x="0"
                y="0"
                z="-9.81"
                comment="Gravitational acceleration"
                units_comment="m/s^2" />

            <rhop0
                value="1000"
                comment="Reference density of the fluid"
                units_comment="kg/m^3" />

            <rhopgradient value="2" />

            <hswl
                value="0"
                auto="true" />

            <gamma value="7" />

            <speedsystem
                value="0"
                auto="true" />

            <coefsound value="20" />

            <speedsound
                value="0"
                auto="true" />

            <coefh value="1.0" />

            <_hdp value="2" />

            <cflnumber value="0.2" />

        </constantsdef>

        <mkconfig
            boundcount="240"
            fluidcount="9" />

        <geometry>

            <definition
                dp="{_f(particle_spacing)}"
                units_comment="simulation metres (scaled prototype units)">

                <pointmin
                    x="{_f(geom.pointmin_x)}"
                    y="{_f(geom.pointmin_y)}"
                    z="{_f(geom.pointmin_z)}" />

                <pointmax
                    x="{_f(geom.pointmax_x)}"
                    y="{_f(geom.pointmax_y)}"
                    z="{_f(geom.pointmax_z)}" />

            </definition>

            <commands>

                <mainlist>

                    <setshapemode>
                        dp | bound
                    </setshapemode>

                    <setdrawmode mode="full" />

{terrain_comment}

                    <setmkbound mk="0" />

                    <drawfilestl file={stl_attribute} />

{wall_xml}                    <!-- Simple preliminary reservoir volume -->

                    <setmkfluid mk="0" />

                    <drawbox>

                        <boxfill>
                            solid
                        </boxfill>

                        <point
                            x="{_f(reservoir_x)}"
                            y="{_f(reservoir_y)}"
                            z="{_f(reservoir_z)}" />

                        <size
                            x="{_f(reservoir_size_x)}"
                            y="{_f(reservoir_size_y)}"
                            z="{_f(reservoir_size_z)}" />

                    </drawbox>

                </mainlist>

            </commands>

        </geometry>

{motion_xml}
    </casedef>

    <execution>

        <parameters>

            <parameter
                key="SavePosDouble"
                value="0" />

            <parameter
                key="StepAlgorithm"
                value="1" />

            <parameter
                key="VerletSteps"
                value="40" />

            <parameter
                key="Kernel"
                value="1" />

            <parameter
                key="ViscoTreatment"
                value="1" />

            <parameter
                key="Visco"
                value="0.1" />

            <parameter
                key="ViscoBoundFactor"
                value="1" />

            <parameter
                key="DensityDT"
                value="2" />

            <parameter
                key="DensityDTvalue"
                value="0.1" />

            <parameter
                key="Shifting"
                value="0" />

            <parameter
                key="RigidAlgorithm"
                value="1" />

            <parameter
                key="CoefDtMin"
                value="0.05" />

            <parameter
                key="DtIni"
                value="0" />

            <parameter
                key="DtMin"
                value="0" />

            <parameter
                key="DtFixed"
                value="0" />

            <parameter
                key="DtFixedFile"
                value="NONE" />

            <parameter
                key="DtAllParticles"
                value="0" />

            <parameter
                key="TimeMax"
                value="{_f(simulation_time)}" />

            <parameter
                key="TimeOut"
                value="{_f(time_out)}" />

            <parameter
                key="PartsOutMax"
                value="1" />

            <parameter
                key="RhopOutMin"
                value="700" />

            <parameter
                key="RhopOutMax"
                value="1300" />

{simulation_domain_xml}

        </parameters>

    </execution>

</case>
"""


def _validate_xml_file(
    path: Path,
    expected_stl_reference: str,
    resolve_base: Path,
    label: str,
    errors: list[str],
    check,
) -> None:
    exists = path.exists()
    check(exists, f"{label} XML exists: {path}")

    if not exists:
        return

    check(path.stat().st_size > 0, f"{label} XML is non-empty")

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        check(False, f"{label} XML parse error: {exc}")
        return

    check(True, f"{label} XML is well-formed")

    root = tree.getroot()
    check(root.tag == "case", f"{label} root tag is <case>")

    required_paths = [
        "casedef",
        "casedef/constantsdef",
        "casedef/constantsdef/gravity",
        "casedef/constantsdef/rhop0",
        "casedef/mkconfig",
        "casedef/geometry",
        "casedef/geometry/definition",
        "casedef/geometry/definition/pointmin",
        "casedef/geometry/definition/pointmax",
        "casedef/geometry/commands",
        "casedef/geometry/commands/mainlist",
        "execution",
        "execution/parameters",
        "execution/parameters/simulationdomain",
    ]

    for required in required_paths:
        element = root.find(required)
        short_name = required.split("/")[-1]
        check(
            element is not None,
            f"{label} XML contains required element <{short_name}>",
        )

    drawboxes = root.findall(".//drawbox")
    check(
        len(drawboxes) >= 1,
        f"{label} XML contains at least one <drawbox>",
    )

    stl_element = root.find(".//drawfilestl")
    check(
        stl_element is not None,
        f"{label} XML contains <drawfilestl>",
    )

    if stl_element is not None:
        file_attribute = stl_element.get("file")

        check(
            file_attribute == expected_stl_reference,
            f"{label} XML STL reference matches expected path",
        )

        if file_attribute:
            resolved = resolve_base / file_attribute
            check(
                resolved.exists(),
                f"{label} XML STL reference resolves: {resolved}",
            )

            if resolved.exists():
                check(
                    resolved.stat().st_size > 0,
                    f"{label} XML STL file is non-empty",
                )


def _check_xml_motion(
    path: Path,
    expected_motion_reference: str | None,
    resolve_base: Path,
    label: str,
    breach_enabled: bool,
    errors: list[str],
    check,
) -> None:
    if not path.exists():
        check(False, f"{label} XML does not exist: {path}")
        return

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        check(False, f"{label} XML parse error while checking motion: {exc}")
        return

    root = tree.getroot()
    motion = root.find(".//motion")

    if not breach_enabled:
        check(
            motion is None,
            f"{label} XML contains no motion when breach is disabled",
        )
        return

    check(
        motion is not None,
        f"{label} XML contains <motion> for breach gate",
    )

    if motion is None:
        return

    objreal = motion.find("objreal")
    check(
        objreal is not None,
        f"{label} XML motion contains <objreal>",
    )

    if objreal is not None:
        check(
            objreal.get("ref") == str(BREACH_GATE_MK),
            f"{label} XML motion objreal ref is {BREACH_GATE_MK}",
        )

    mvfile = motion.find(".//mvfile")
    check(
        mvfile is not None,
        f"{label} XML motion contains <mvfile>",
    )

    file_element = motion.find(".//file")
    check(
        file_element is not None,
        f"{label} XML motion contains <file>",
    )

    if file_element is not None:
        motion_name = file_element.get("name")

        check(
            motion_name == expected_motion_reference,
            f"{label} XML motion file reference matches expected path",
        )

        if motion_name:
            resolved = resolve_base / motion_name
            check(
                resolved.exists(),
                f"{label} XML motion file resolves: {resolved}",
            )

            if resolved.exists():
                check(
                    resolved.stat().st_size > 0,
                    f"{label} XML motion file is non-empty",
                )


def _validate_motion_file(
    motion_path: Path,
    geom: TerrainCaseGeometry,
    expected_initial_x: float,
    expected_initial_y: float,
    expected_initial_z: float,
    breach_time: float,
    lift_duration: float,
    lift_distance: float,
    errors: list[str],
    check,
) -> None:
    exists = motion_path.exists()
    check(exists, f"Gate motion file exists: {motion_path}")

    if not exists:
        return

    check(
        motion_path.stat().st_size > 0,
        f"Gate motion file is non-empty: {motion_path}",
    )

    try:
        text = motion_path.read_text(encoding="utf-8")
    except OSError as exc:
        check(False, f"Unable to read gate motion file: {exc}")
        return

    rows: list[list[float]] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) != 4:
            check(False, f"Motion file line does not have 4 columns: {line}")
            continue

        try:
            t = float(parts[0])
            x = float(parts[1])
            y = float(parts[2])
            z = float(parts[3])
        except ValueError:
            check(False, f"Motion file line contains non-numeric values: {line}")
            continue

        if not (
            math.isfinite(t)
            and math.isfinite(x)
            and math.isfinite(y)
            and math.isfinite(z)
        ):
            check(False, f"Motion file line contains non-finite values: {line}")
            continue

        rows.append([t, x, y, z])

    check(len(rows) >= 2, "Motion file contains at least two rows")

    if len(rows) < 2:
        return

    times = [row[0] for row in rows]
    xs = [row[1] for row in rows]
    ys = [row[2] for row in rows]
    zs = [row[3] for row in rows]

    check(abs(times[0]) <= MOTION_EPS, "Motion file starts at t=0")

    monotonic = all(
        times[i] >= times[i - 1] - MOTION_EPS
        for i in range(1, len(times))
    )
    check(monotonic, "Motion file time values are monotonic")

    x_constant = all(
        abs(x - expected_initial_x) <= 1e-5
        for x in xs
    )
    check(x_constant, "Motion file X remains constant")

    y_constant = all(
        abs(y - expected_initial_y) <= 1e-5
        for y in ys
    )
    check(y_constant, "Motion file Y remains constant")

    z_non_decreasing = all(
        zs[i] >= zs[i - 1] - 1e-6
        for i in range(1, len(zs))
    )
    check(z_non_decreasing, "Motion file Z is non-decreasing")

    check(
        abs(zs[0] - expected_initial_z) <= 1e-5,
        "Motion file initial Z matches gate initial Z",
    )

    expected_final_z = expected_initial_z + lift_distance
    check(
        abs(zs[-1] - expected_final_z) <= 1e-3,
        "Motion file final Z matches gate initial Z + lift distance",
    )

    check(
        zs[-1] > zs[0] + MOTION_EPS,
        "Motion file final Z is greater than initial Z",
    )

    eps = 1e-6
    inside = all(
        geom.pointmin_x - eps <= row[1] <= geom.pointmax_x + eps
        and geom.pointmin_y - eps <= row[2] <= geom.pointmax_y + eps
        and geom.pointmin_z - eps <= row[3] <= geom.pointmax_z + eps
        for row in rows
    )
    check(inside, "All motion positions lie inside the simulation domain")


def _validate_breach(
    cfg: dict,
    geom: TerrainCaseGeometry,
    motion_path: Path,
    standalone_xml: Path,
    runner_xml: Path,
    standalone_motion_reference: str | None,
    runner_motion_reference: str | None,
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

    breach_enabled_raw = cfg.get("breach_enabled", False)
    check(
        isinstance(breach_enabled_raw, bool),
        "breach_enabled must be a boolean",
    )

    breach_enabled = bool(breach_enabled_raw)

    if not breach_enabled:
        check(
            not motion_path.exists(),
            "Gate motion file is absent when breach is disabled",
        )

        _check_xml_motion(
            path=standalone_xml,
            expected_motion_reference=None,
            resolve_base=CASE_DIR,
            label="Standalone",
            breach_enabled=False,
            errors=errors,
            check=check,
        )

        _check_xml_motion(
            path=runner_xml,
            expected_motion_reference=None,
            resolve_base=CASE_DIR,
            label="Runner",
            breach_enabled=False,
            errors=errors,
            check=check,
        )

        return {
            "status": "pass" if not errors else "fail",
            "checks_passed": checks,
            "errors": errors,
        }

    breach_time = float(cfg.get("breach_time", 0.0))
    lift_duration = float(cfg.get("breach_lift_duration", 0.0))
    lift_distance = float(cfg.get("breach_lift_distance", 0.0))
    motion_steps = int(cfg.get("breach_motion_steps", 0))

    check(breach_time >= 0.0, "breach_time must be >= 0")
    check(lift_duration > 0.0, "breach_lift_duration must be > 0")
    check(lift_distance > 0.0, "breach_lift_distance must be > 0")
    check(motion_steps > 0, "breach_motion_steps must be > 0")

    check(
        breach_time + lift_duration <= simulation_time + 1e-6,
        "breach_time + breach_lift_duration must be <= simulation_time",
    )

    check(
        geom.wall_enabled,
        "test_dam_wall must be enabled when breach_enabled is true",
    )

    wall_available = (
        geom.wall_x0 is not None
        and geom.wall_x1 is not None
        and geom.wall_y0 is not None
        and geom.wall_y1 is not None
        and geom.wall_base_z is not None
        and geom.wall_top_z is not None
    )

    check(
        wall_available,
        "Gate wall geometry is available for breach motion",
    )

    if wall_available:
        eps = 1e-6

        initial_bounds = {
            "x0": geom.wall_x0,
            "x1": geom.wall_x1,
            "y0": geom.wall_y0,
            "y1": geom.wall_y1,
            "z0": geom.wall_base_z,
            "z1": geom.wall_top_z,
        }

        final_bounds = {
            "x0": geom.wall_x0,
            "x1": geom.wall_x1,
            "y0": geom.wall_y0,
            "y1": geom.wall_y1,
            "z0": geom.wall_base_z + lift_distance,
            "z1": geom.wall_top_z + lift_distance,
        }

        pointmin = (geom.pointmin_x, geom.pointmin_y, geom.pointmin_z)
        pointmax = (geom.pointmax_x, geom.pointmax_y, geom.pointmax_z)

        def check_bounds(name: str, bounds: dict) -> None:
            check(
                bounds["x0"] >= pointmin[0] - eps
                and bounds["x1"] <= pointmax[0] + eps,
                f"{name} x bounds fit inside simulation domain",
            )
            check(
                bounds["y0"] >= pointmin[1] - eps
                and bounds["y1"] <= pointmax[1] + eps,
                f"{name} y bounds fit inside simulation domain",
            )
            check(
                bounds["z0"] >= pointmin[2] - eps
                and bounds["z1"] <= pointmax[2] + eps,
                f"{name} z bounds fit inside simulation domain",
            )

        check_bounds("initial gate", initial_bounds)
        check_bounds("final lifted gate", final_bounds)

        _validate_motion_file(
            motion_path=motion_path,
            geom=geom,
            expected_initial_x=geom.wall_x0,
            expected_initial_y=geom.wall_y0,
            expected_initial_z=geom.wall_base_z,
            breach_time=breach_time,
            lift_duration=lift_duration,
            lift_distance=lift_distance,
            errors=errors,
            check=check,
        )

    _check_xml_motion(
        path=standalone_xml,
        expected_motion_reference=standalone_motion_reference,
        resolve_base=CASE_DIR,
        label="Standalone",
        breach_enabled=True,
        errors=errors,
        check=check,
    )

    _check_xml_motion(
        path=runner_xml,
        expected_motion_reference=runner_motion_reference,
        resolve_base=CASE_DIR,
        label="Runner",
        breach_enabled=True,
        errors=errors,
        check=check,
    )

    return {
        "status": "pass" if not errors else "fail",
        "checks_passed": checks,
        "errors": errors,
    }


def _validate_case(
    cfg: dict,
    geom: TerrainCaseGeometry,
    terrain_stl_bounds: dict,
    stl_copy: Path,
    standalone_xml: Path,
    runner_xml: Path,
    standalone_stl_reference: str,
    runner_stl_reference: str,
) -> dict:
    errors: list[str] = []
    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        if condition:
            checks += 1
        else:
            errors.append(message)

    particle_spacing = float(cfg["particle_spacing"])
    simulation_time = float(cfg["simulation_time"])
    time_out = float(cfg["time_out"])

    check(
        math.isfinite(particle_spacing) and particle_spacing > 0.0,
        "particle_spacing must be positive",
    )
    check(
        math.isfinite(simulation_time) and simulation_time > 0.0,
        "simulation_time must be positive",
    )
    check(
        math.isfinite(time_out) and time_out > 0.0,
        "time_out must be positive",
    )

    check(
        geom.pointmin_x < geom.pointmax_x,
        "pointmin_x must be less than pointmax_x",
    )
    check(
        geom.pointmin_y < geom.pointmax_y,
        "pointmin_y must be less than pointmax_y",
    )
    check(
        geom.pointmin_z < geom.pointmax_z,
        "pointmin_z must be less than pointmax_z",
    )

    check(
        geom.reservoir_base_z < geom.reservoir_top_z,
        "reservoir_base_z must be below reservoir_top_z",
    )

    if geom.wall_enabled:
        check(
            geom.wall_x0 is not None
            and geom.wall_x1 is not None
            and geom.wall_y0 is not None
            and geom.wall_y1 is not None
            and geom.wall_base_z is not None
            and geom.wall_top_z is not None,
            "test dam wall geometry must be defined when wall is enabled",
        )

        if (
            geom.wall_base_z is not None
            and geom.wall_top_z is not None
        ):
            check(
                geom.wall_base_z < geom.wall_top_z,
                "wall_base_z must be below wall_top_z",
            )

    check(
        stl_copy.exists(),
        f"Terrain STL copy exists: {stl_copy}",
    )

    if stl_copy.exists():
        check(
            stl_copy.stat().st_size > 0,
            f"Terrain STL copy is non-empty: {stl_copy}",
        )

    eps = 1e-6
    pointmin = (geom.pointmin_x, geom.pointmin_y, geom.pointmin_z)
    pointmax = (geom.pointmax_x, geom.pointmax_y, geom.pointmax_z)

    def check_bounds(name: str, bounds: dict | None) -> None:
        if bounds is None:
            check(False, f"{name} bounds unavailable")
            return

        check(
            bounds["x0"] >= pointmin[0] - eps
            and bounds["x1"] <= pointmax[0] + eps,
            f"{name} x bounds fit inside pointmin/pointmax",
        )
        check(
            bounds["y0"] >= pointmin[1] - eps
            and bounds["y1"] <= pointmax[1] + eps,
            f"{name} y bounds fit inside pointmin/pointmax",
        )
        check(
            bounds["z0"] >= pointmin[2] - eps
            and bounds["z1"] <= pointmax[2] + eps,
            f"{name} z bounds fit inside pointmin/pointmax",
        )

    check_bounds("terrain STL", terrain_stl_bounds)

    reservoir_bounds = {
        "x0": geom.reservoir_x0,
        "x1": geom.reservoir_x1,
        "y0": geom.reservoir_y0,
        "y1": geom.reservoir_y1,
        "z0": geom.reservoir_base_z,
        "z1": geom.reservoir_top_z,
    }
    check_bounds("reservoir", reservoir_bounds)

    wall_bounds = None
    if geom.wall_enabled:
        wall_bounds = {
            "x0": geom.wall_x0,
            "x1": geom.wall_x1,
            "y0": geom.wall_y0,
            "y1": geom.wall_y1,
            "z0": geom.wall_base_z,
            "z1": geom.wall_top_z,
        }
        check_bounds("test dam wall", wall_bounds)

    check(
        geom.reservoir_top_z <= geom.pointmax_z - eps,
        "reservoir_top_z must be below simulation pointmax_z",
    )

    if geom.wall_enabled and geom.wall_top_z is not None:
        check(
            geom.wall_top_z <= geom.pointmax_z - eps,
            "wall_top_z must be below simulation pointmax_z",
        )

    _validate_xml_file(
        path=standalone_xml,
        expected_stl_reference=standalone_stl_reference,
        resolve_base=CASE_DIR,
        label="Standalone",
        errors=errors,
        check=check,
    )

    _validate_xml_file(
        path=runner_xml,
        expected_stl_reference=runner_stl_reference,
        resolve_base=CASE_DIR,
        label="Runner",
        errors=errors,
        check=check,
    )

    return {
        "status": "pass" if not errors else "fail",
        "checks_passed": checks,
        "errors": errors,
        "dualsphysics_run_attempted": False,
    }


def _print_summary(summary: dict) -> None:
    geometry = summary["geometry"]
    bounds = summary["bounds"]
    validation = summary["validation"]
    gap = summary["reservoir_vertical_gap"]

    print("=" * 78)
    print("HADR_TerrainChouldari generated case summary")
    print("=" * 78)

    print(f"Case name: {summary['case_name']}")
    print(f"Case status: {summary['status']}")
    print(f"Validation status: {validation['status']}")
    print(f"Validation checks passed: {validation['checks_passed']}")
    print("DualSPHysics run attempted: False")
    print()

    print("Terrain STL bounds:")
    terrain_bounds = bounds["terrain_stl"]
    print(f"  x: {_fmt(terrain_bounds['x0'])} to {_fmt(terrain_bounds['x1'])}")
    print(f"  y: {_fmt(terrain_bounds['y0'])} to {_fmt(terrain_bounds['y1'])}")
    print(f"  z: {_fmt(terrain_bounds['z0'])} to {_fmt(terrain_bounds['z1'])}")
    print(f"  bounds source: {validation.get('terrain_stl_bounds_source', 'unknown')}")
    print()

    print("Simulation domain:")
    pointmin = bounds["pointmin"]
    pointmax = bounds["pointmax"]
    print(f"  pointmin: x={_fmt(pointmin[0])}, y={_fmt(pointmin[1])}, z={_fmt(pointmin[2])}")
    print(f"  pointmax: x={_fmt(pointmax[0])}, y={_fmt(pointmax[1])}, z={_fmt(pointmax[2])}")
    print()

    print("Dam position:")
    print(f"  x: {_fmt(geometry['dam_x'])}")
    print(f"  y: {_fmt(geometry['dam_y'])}")
    print(f"  z: {_fmt(geometry['dam_z'])}")
    print(f"  coordinate source: {geometry['dam_coordinate_source']}")
    print()

    print("Upstream estimate:")
    print(f"  axis: {geometry['upstream_axis']}")
    print(f"  sign: {geometry['upstream_sign']:+.0f}")
    print(f"  method: {geometry['upstream_method']}")
    print(f"  points used: {geometry['upstream_points']}")
    print(f"  mean elevation: {_fmt(geometry['upstream_mean_elevation'])}")
    print()

    reservoir = bounds["reservoir"]
    print("Reservoir:")
    print(f"  x0 x1: {_fmt(reservoir['x0'])} {_fmt(reservoir['x1'])}")
    print(f"  y0 y1: {_fmt(reservoir['y0'])} {_fmt(reservoir['y1'])}")
    print(f"  base_z top_z: {_fmt(reservoir['base_z'])} {_fmt(reservoir['top_z'])}")
    print(f"  depth: {_fmt(geometry['reservoir_depth'])}")
    print(f"  local terrain points: {geometry['reservoir_terrain_points']}")
    print(f"  local terrain min: {_fmt(geometry['reservoir_terrain_min_z'])}")
    print(f"  local terrain mean: {_fmt(geometry['reservoir_terrain_mean_z'])}")
    print(f"  local terrain max: {_fmt(geometry['reservoir_terrain_max_z'])}")
    print()

    print("Reservoir vertical gap:")
    print(f"  reservoir base: {_fmt(gap['reservoir_base_z'])}")
    print(f"  gap above local terrain max: {_fmt(gap['gap_above_local_max'])}")
    print(f"  gap above local terrain mean: {_fmt(gap['gap_above_local_mean'])}")
    print(f"  gap above local terrain min: {_fmt(gap['gap_above_local_min'])}")
    print(f"  note: {gap['note']}")
    print()

    wall = bounds.get("wall")
    if wall is None:
        print("Test dam wall: disabled")
    else:
        print("Test dam wall:")
        print(f"  x0 x1: {_fmt(wall['x0'])} {_fmt(wall['x1'])}")
        print(f"  y0 y1: {_fmt(wall['y0'])} {_fmt(wall['y1'])}")
        print(f"  base_z top_z: {_fmt(wall['base_z'])} {_fmt(wall['top_z'])}")
        print("  note: temporary experimental containment wall only")
    print()

    breach = summary.get("breach")
    if breach is not None:
        print("Breach:")
        print(f"  enabled: {breach['enabled']}")

        if breach["enabled"]:
            print(f"  breach time: {_fmt(breach['breach_time'])}")
            print(f"  lift duration: {_fmt(breach['lift_duration'])}")
            print(f"  lift distance: {_fmt(breach['lift_distance'])}")
            print(f"  motion steps: {breach['motion_steps']}")
            print(f"  gate mk: {breach['gate_mk']}")
            print(f"  gate initial X: {_fmt(breach['gate_initial_x'])}")
            print(f"  gate initial Y: {_fmt(breach['gate_initial_y'])}")
            print(f"  gate initial Z: {_fmt(breach['gate_initial_z'])}")
            print(f"  gate final Z: {_fmt(breach['gate_final_z'])}")
            print(f"  gate final top Z: {_fmt(breach['gate_final_top_z'])}")
            print(f"  motion file: {breach['motion_file']}")
            print(f"  motion duration: {_fmt(breach['motion_duration'])}")
        else:
            print("  motion file: None")

        print(f"  note: {breach['note']}")
        print()

    print("Outputs:")
    outputs = summary["outputs"]
    print(f"  standalone XML: {outputs['definition_xml']}")
    print(f"  runner XML: {outputs['runner_definition_xml']}")
    print(f"  summary JSON: {outputs['summary_json']}")
    print()

    if validation["errors"]:
        print("Validation errors:")
        for error in validation["errors"]:
            print(f"  ERROR: {error}")
    else:
        print("Validation passed. No geometry or XML validation errors detected.")

    print()
    print("Next step: inspect this summary. Do NOT run DualSPHysics until approved.")


def generate_case(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    if not config_path.exists():
        raise TerrainCaseError(f"Config file not found: {config_path}")

    cfg = json.loads(config_path.read_text(encoding="utf-8"))

    case_name = str(cfg.get("case_name", "HADR_TerrainChouldari"))

    particle_spacing = _positive(cfg, "particle_spacing")
    simulation_time = _positive(cfg, "simulation_time")
    time_out = _positive(cfg, "time_out")

    water_depth = _positive(cfg, "reservoir_water_depth")
    reservoir_length = _positive(cfg, "reservoir_length")
    reservoir_width = _positive(cfg, "reservoir_width")
    reservoir_offset = _positive(cfg, "reservoir_offset_from_dam")
    reservoir_base_clearance = _non_negative(
        cfg, "reservoir_base_clearance", 0.02
    )

    upstream_radius = _positive(cfg, "upstream_search_radius")
    upstream_inner_radius = _non_negative(cfg, "upstream_inner_radius", 1.0)

    domain_margin_xy = _non_negative(cfg, "domain_margin_xy", 1.0)
    domain_margin_z_bottom = _non_negative(cfg, "domain_margin_z_bottom", 0.5)
    domain_margin_z_top = _non_negative(cfg, "domain_margin_z_top", 1.0)

    wall_enabled = bool(cfg.get("test_dam_wall", True))
    wall_thickness = (
        _positive(cfg, "test_dam_wall_thickness") if wall_enabled else None
    )
    wall_side_margin = _non_negative(cfg, "test_dam_wall_side_margin", 0.5)
    wall_base_clearance = _non_negative(cfg, "test_dam_wall_base_clearance", 0.2)
    wall_top_clearance = _non_negative(cfg, "test_dam_wall_top_clearance", 0.2)

    breach_enabled_raw = cfg.get("breach_enabled", False)
    if not isinstance(breach_enabled_raw, bool):
        raise TerrainCaseError("breach_enabled must be a boolean")

    breach_enabled = breach_enabled_raw

    if breach_enabled:
        breach_time = _non_negative(cfg, "breach_time", 0.0)
        breach_lift_duration = _positive(cfg, "breach_lift_duration")
        breach_lift_distance = _positive(cfg, "breach_lift_distance")

        try:
            breach_motion_steps = int(cfg.get("breach_motion_steps", 50))
        except (TypeError, ValueError) as exc:
            raise TerrainCaseError("breach_motion_steps must be an integer") from exc

        if breach_motion_steps <= 0:
            raise TerrainCaseError("breach_motion_steps must be positive")

        if not wall_enabled:
            raise TerrainCaseError(
                "test_dam_wall must be true when breach_enabled is true"
            )

        if breach_time + breach_lift_duration > simulation_time + 1e-6:
            raise TerrainCaseError(
                "breach_time + breach_lift_duration must be <= simulation_time"
            )
    else:
        breach_time = 0.0
        breach_lift_duration = None
        breach_lift_distance = None
        breach_motion_steps = None

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

    force_axis = cfg.get("force_upstream_axis")
    force_sign = cfg.get("force_upstream_sign")

    if force_axis in {"x", "y"}:
        axis = str(force_axis)
        sign = -1.0 if force_sign is not None and float(force_sign) < 0 else 1.0

        if axis == "x":
            upstream_vector = (sign, 0.0)
        else:
            upstream_vector = (0.0, sign)

        upstream_method = "forced-by-config"
        upstream_points = 0
        upstream_mean_elevation = None
    else:
        default_direction = (
            float(cfg.get("fallback_upstream_x", 1.0)),
            float(cfg.get("fallback_upstream_y", 0.0)),
        )

        upstream_vector, upstream_method, upstream_points, upstream_mean_elevation = (
            _estimate_upstream_direction(
                x=x,
                y=y,
                z=z,
                dam_x=dam_x,
                dam_y=dam_y,
                radius=upstream_radius,
                inner_radius=upstream_inner_radius,
                default_direction=default_direction,
            )
        )

        ux, uy = upstream_vector

        if abs(ux) >= abs(uy):
            axis = "x"
            sign = 1.0 if ux >= 0.0 else -1.0
        else:
            axis = "y"
            sign = 1.0 if uy >= 0.0 else -1.0

    minimum_offset = reservoir_length / 2.0
    if wall_enabled and wall_thickness is not None:
        minimum_offset += wall_thickness

    minimum_offset += 2.0 * particle_spacing
    reservoir_offset = max(reservoir_offset, minimum_offset)

    edge_margin = max(2.0 * particle_spacing, 0.5)

    (
        reservoir_center_x,
        reservoir_center_y,
        reservoir_x0,
        reservoir_x1,
        reservoir_y0,
        reservoir_y1,
    ) = _reservoir_footprint(
        dam_x=dam_x,
        dam_y=dam_y,
        axis=axis,
        sign=sign,
        offset=reservoir_offset,
        length=reservoir_length,
        width=reservoir_width,
        terrain_bounds=(
            terrain_x_min,
            terrain_x_max,
            terrain_y_min,
            terrain_y_max,
        ),
        edge_margin=edge_margin,
    )

    reservoir_stats = _box_stats(
        x,
        y,
        z,
        reservoir_x0,
        reservoir_x1,
        reservoir_y0,
        reservoir_y1,
    )

    if reservoir_stats is None:
        distances = np.hypot(x - reservoir_center_x, y - reservoir_center_y)
        selected = distances <= max(2.0 * particle_spacing, 2.0)

        if np.any(selected):
            values = z[selected]
            reservoir_stats = {
                "count": int(selected.sum()),
                "min": float(np.min(values)),
                "max": float(np.max(values)),
                "mean": float(np.mean(values)),
            }

    if reservoir_stats is None:
        base_terrain_z = dam_z
        reservoir_terrain_points = 0
        reservoir_terrain_min_z = None
        reservoir_terrain_max_z = None
        reservoir_terrain_mean_z = None
    else:
        base_terrain_z = reservoir_stats["max"]
        reservoir_terrain_points = reservoir_stats["count"]
        reservoir_terrain_min_z = reservoir_stats["min"]
        reservoir_terrain_max_z = reservoir_stats["max"]
        reservoir_terrain_mean_z = reservoir_stats["mean"]

    reservoir_base_z = base_terrain_z + reservoir_base_clearance
    reservoir_top_z = reservoir_base_z + water_depth

    wall_x0 = wall_x1 = wall_y0 = wall_y1 = None
    wall_base_z = wall_top_z = None

    if wall_enabled and wall_thickness is not None:
        if axis == "x":
            wall_x0 = dam_x - wall_thickness / 2.0
            wall_x1 = dam_x + wall_thickness / 2.0
            wall_y0 = min(reservoir_y0, dam_y) - wall_side_margin
            wall_y1 = max(reservoir_y1, dam_y) + wall_side_margin
        else:
            wall_y0 = dam_y - wall_thickness / 2.0
            wall_y1 = dam_y + wall_thickness / 2.0
            wall_x0 = min(reservoir_x0, dam_x) - wall_side_margin
            wall_x1 = max(reservoir_x1, dam_x) + wall_side_margin

        wall_stats = _box_stats(
            x,
            y,
            z,
            wall_x0 - wall_side_margin,
            wall_x1 + wall_side_margin,
            wall_y0 - wall_side_margin,
            wall_y1 + wall_side_margin,
        )

        local_min_candidates = [dam_z]

        if wall_stats is not None:
            local_min_candidates.append(wall_stats["min"])

        if reservoir_terrain_min_z is not None:
            local_min_candidates.append(reservoir_terrain_min_z)

        wall_base_z = min(local_min_candidates) - wall_base_clearance
        wall_top_z = reservoir_top_z + wall_top_clearance

        minimum_wall_height = 2.0 * particle_spacing
        wall_height = max(wall_top_z - wall_base_z, minimum_wall_height)
        wall_top_z = wall_base_z + wall_height

    motion_path = CASE_DIR / f"{case_name}_gate_motion.txt"

    motion_duration = None
    gate_initial_z = None
    gate_final_z = None
    gate_final_top_z = None

    breach_domain_headroom = max(domain_margin_z_top, 0.2)

    if (
        breach_enabled
        and wall_base_z is not None
        and wall_top_z is not None
        and breach_lift_distance is not None
    ):
        gate_initial_z = wall_base_z
        gate_final_z = wall_base_z + breach_lift_distance
        gate_height = wall_top_z - wall_base_z
        gate_final_top_z = gate_final_z + gate_height

    pointmin_x_candidates = [terrain_x_min, reservoir_x0]
    pointmax_x_candidates = [terrain_x_max, reservoir_x1]
    pointmin_y_candidates = [terrain_y_min, reservoir_y0]
    pointmax_y_candidates = [terrain_y_max, reservoir_y1]
    pointmin_z_candidates = [terrain_z_min, reservoir_base_z]
    pointmax_z_candidates = [terrain_z_max, reservoir_top_z]

    if wall_enabled and None not in (
        wall_x0,
        wall_x1,
        wall_y0,
        wall_y1,
        wall_base_z,
        wall_top_z,
    ):
        pointmin_x_candidates.extend([wall_x0, wall_x1])
        pointmax_x_candidates.extend([wall_x0, wall_x1])
        pointmin_y_candidates.extend([wall_y0, wall_y1])
        pointmax_y_candidates.extend([wall_y0, wall_y1])
        pointmin_z_candidates.append(wall_base_z)
        pointmax_z_candidates.append(wall_top_z)

    if gate_final_top_z is not None:
        pointmax_z_candidates.append(
            gate_final_top_z + breach_domain_headroom
        )

    pointmin_x = min(pointmin_x_candidates) - domain_margin_xy
    pointmax_x = max(pointmax_x_candidates) + domain_margin_xy
    pointmin_y = min(pointmin_y_candidates) - domain_margin_xy
    pointmax_y = max(pointmax_y_candidates) + domain_margin_xy
    pointmin_z = min(pointmin_z_candidates) - domain_margin_z_bottom
    pointmax_z = max(pointmax_z_candidates) + domain_margin_z_top

    geom = TerrainCaseGeometry(
        scale=terrain["scale"],
        terrain_samples=int(x.size),
        terrain_x_min=terrain_x_min,
        terrain_x_max=terrain_x_max,
        terrain_y_min=terrain_y_min,
        terrain_y_max=terrain_y_max,
        terrain_z_min=terrain_z_min,
        terrain_z_max=terrain_z_max,
        dam_x=dam_x,
        dam_y=dam_y,
        dam_z=dam_z,
        dam_nearest_distance=terrain["dam_nearest_distance"],
        dam_coordinate_source=terrain["dam_coordinate_source"],
        upstream_x=float(upstream_vector[0]),
        upstream_y=float(upstream_vector[1]),
        upstream_axis=axis,
        upstream_sign=sign,
        upstream_method=upstream_method,
        upstream_points=upstream_points,
        upstream_mean_elevation=upstream_mean_elevation,
        reservoir_center_x=reservoir_center_x,
        reservoir_center_y=reservoir_center_y,
        reservoir_x0=reservoir_x0,
        reservoir_x1=reservoir_x1,
        reservoir_y0=reservoir_y0,
        reservoir_y1=reservoir_y1,
        reservoir_length=reservoir_length,
        reservoir_width=reservoir_width,
        reservoir_depth=water_depth,
        reservoir_base_z=reservoir_base_z,
        reservoir_top_z=reservoir_top_z,
        reservoir_terrain_points=reservoir_terrain_points,
        reservoir_terrain_min_z=reservoir_terrain_min_z,
        reservoir_terrain_max_z=reservoir_terrain_max_z,
        reservoir_terrain_mean_z=reservoir_terrain_mean_z,
        wall_enabled=wall_enabled,
        wall_thickness=wall_thickness,
        wall_side_margin=wall_side_margin if wall_enabled else None,
        wall_x0=wall_x0,
        wall_x1=wall_x1,
        wall_y0=wall_y0,
        wall_y1=wall_y1,
        wall_base_z=wall_base_z,
        wall_top_z=wall_top_z,
        pointmin_x=pointmin_x,
        pointmin_y=pointmin_y,
        pointmin_z=pointmin_z,
        pointmax_x=pointmax_x,
        pointmax_y=pointmax_y,
        pointmax_z=pointmax_z,
    )

    stl_source = _resolve_path(cfg["terrain_stl"])

    if not stl_source.exists():
        raise TerrainCaseError(f"Terrain STL not found: {stl_source}")

    # Keep the existing terrain/ copy for reference.
    terrain_dir = CASE_DIR / str(cfg.get("terrain_output_subdir", "terrain"))
    terrain_dir.mkdir(parents=True, exist_ok=True)

    terrain_stl_copy = terrain_dir / stl_source.name

    if stl_source.resolve() != terrain_stl_copy.resolve():
        shutil.copyfile(stl_source, terrain_stl_copy)

    # GenCase copies referenced external files more reliably when they are
    # located beside the case-definition XML.
    #
    # For the standalone case, reference the STL by filename only.
    stl_copy = CASE_DIR / stl_source.name

    if terrain_stl_copy.resolve() != stl_copy.resolve():
        shutil.copyfile(terrain_stl_copy, stl_copy)

    standalone_stl_reference = stl_copy.name
    runner_stl_reference = stl_copy.resolve().as_posix()

    parsed_stl_bounds = _parse_ascii_stl_bounds(stl_copy)
    if parsed_stl_bounds is None:
        terrain_stl_bounds = {
            "x0": terrain_x_min,
            "x1": terrain_x_max,
            "y0": terrain_y_min,
            "y1": terrain_y_max,
            "z0": terrain_z_min,
            "z1": terrain_z_max,
        }
        terrain_stl_bounds_source = "npz_bounds_assumed_for_stl"
    else:
        terrain_stl_bounds = parsed_stl_bounds
        terrain_stl_bounds_source = "ascii_stl_parsed"

    standalone_motion_reference = None
    runner_motion_reference = None
    simulation_posmax_z = None

    if breach_enabled:
        if (
            wall_x0 is None
            or wall_y0 is None
            or wall_base_z is None
            or breach_lift_duration is None
            or breach_lift_distance is None
            or breach_motion_steps is None
        ):
            raise TerrainCaseError(
                "Breach gate geometry is not available for motion generation"
            )

        motion_duration = _write_gate_motion(
            motion_path=motion_path,
            initial_x=wall_x0,
            initial_y=wall_y0,
            initial_z=wall_base_z,
            breach_time=breach_time,
            simulation_time=simulation_time,
            lift_duration=breach_lift_duration,
            lift_distance=breach_lift_distance,
            motion_steps=breach_motion_steps,
        )

        standalone_motion_reference = motion_path.name
        runner_motion_reference = motion_path.resolve().as_posix()

        simulation_posmax_z = geom.pointmax_z

    else:
        if motion_path.exists():
            motion_path.unlink()

    standalone_xml = _build_xml(
        stl_file=standalone_stl_reference,
        motion_file=standalone_motion_reference,
        cfg=cfg,
        geom=geom,
        breach_enabled=breach_enabled,
        motion_duration=motion_duration,
        simulation_posmax_z=simulation_posmax_z,
    )

    runner_xml = _build_xml(
        stl_file=runner_stl_reference,
        motion_file=runner_motion_reference,
        cfg=cfg,
        geom=geom,
        breach_enabled=breach_enabled,
        motion_duration=motion_duration,
        simulation_posmax_z=simulation_posmax_z,
    )

    definition_name = str(
        cfg.get("definition_name", f"{case_name}_Def.xml")
    )
    runner_definition_name = str(
        cfg.get("runner_definition_name", f"{case_name}_runner_Def.xml")
    )
    summary_name = str(cfg.get("summary_name", "terrain_case_summary.json"))

    definition_path = CASE_DIR / definition_name
    runner_definition_path = CASE_DIR / runner_definition_name
    summary_path = CASE_DIR / summary_name

    definition_path.write_text(standalone_xml, encoding="utf-8")
    runner_definition_path.write_text(runner_xml, encoding="utf-8")

    validation = _validate_case(
        cfg=cfg,
        geom=geom,
        terrain_stl_bounds=terrain_stl_bounds,
        stl_copy=stl_copy,
        standalone_xml=definition_path,
        runner_xml=runner_definition_path,
        standalone_stl_reference=standalone_stl_reference,
        runner_stl_reference=runner_stl_reference,
    )
    validation["terrain_stl_bounds_source"] = terrain_stl_bounds_source

    breach_validation = _validate_breach(
        cfg=cfg,
        geom=geom,
        motion_path=motion_path,
        standalone_xml=definition_path,
        runner_xml=runner_definition_path,
        standalone_motion_reference=standalone_motion_reference,
        runner_motion_reference=runner_motion_reference,
        simulation_time=simulation_time,
    )

    validation["checks_passed"] += breach_validation["checks_passed"]
    validation["errors"].extend(breach_validation["errors"])

    if breach_validation["errors"]:
        validation["status"] = "fail"

    validation["breach_validation"] = breach_validation

    def vertical_gap_value(reference: float | None) -> float | None:
        if reference is None:
            return None
        return reservoir_base_z - reference

    reservoir_vertical_gap = {
        "reservoir_base_z": reservoir_base_z,
        "local_terrain_points": reservoir_terrain_points,
        "local_terrain_min": reservoir_terrain_min_z,
        "local_terrain_mean": reservoir_terrain_mean_z,
        "local_terrain_max": reservoir_terrain_max_z,
        "gap_above_local_min": vertical_gap_value(reservoir_terrain_min_z),
        "gap_above_local_mean": vertical_gap_value(reservoir_terrain_mean_z),
        "gap_above_local_max": vertical_gap_value(reservoir_terrain_max_z),
        "note": (
            "Integration-test reservoir base is set above local terrain maximum. "
            "This is not the final hydraulic water-surface definition."
        ),
    }

    wall_bounds = None
    if geom.wall_enabled:
        wall_bounds = {
            "x0": geom.wall_x0,
            "x1": geom.wall_x1,
            "y0": geom.wall_y0,
            "y1": geom.wall_y1,
            "base_z": geom.wall_base_z,
            "top_z": geom.wall_top_z,
        }

    breach_summary = {
        "enabled": breach_enabled,
        "breach_time": breach_time if breach_enabled else 0.0,
        "lift_duration": breach_lift_duration,
        "lift_distance": breach_lift_distance,
        "motion_steps": breach_motion_steps,
        "gate_mk": BREACH_GATE_MK if breach_enabled else None,
        "gate_initial_x": wall_x0 if breach_enabled else None,
        "gate_initial_y": wall_y0 if breach_enabled else None,
        "gate_initial_z": gate_initial_z,
        "gate_final_z": gate_final_z,
        "gate_final_top_z": gate_final_top_z,
        "motion_file": str(motion_path) if breach_enabled else None,
        "motion_duration": motion_duration,
        "note": (
            "Prototype breach assumptions only. "
            "These are not observed Chouldari failure parameters."
        ),
    }

    summary = {
        "case_name": case_name,
        "status": "experimental_integration_test",
        "limitations": [
            "Terrain source is Copernicus GLO-30 DSM data.",
            "Terrain coordinates are numerically scaled prototype coordinates.",
            "This is not a validated physical flood model.",
            "Hydrological calibration is still required.",
            "The terrain STL is a surface only; it has no artificial box, bottom, or side walls.",
            "The temporary test dam wall is only an experimental containment feature.",
            "Final SIH modelling will combine terrain, hydrological observations, satellite data, SPH/Delft3D comparison, and GIS outputs.",
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
        "bounds": {
            "terrain_stl": terrain_stl_bounds,
            "pointmin": [
                geom.pointmin_x,
                geom.pointmin_y,
                geom.pointmin_z,
            ],
            "pointmax": [
                geom.pointmax_x,
                geom.pointmax_y,
                geom.pointmax_z,
            ],
            "reservoir": {
                "x0": geom.reservoir_x0,
                "x1": geom.reservoir_x1,
                "y0": geom.reservoir_y0,
                "y1": geom.reservoir_y1,
                "base_z": geom.reservoir_base_z,
                "top_z": geom.reservoir_top_z,
            },
            "wall": wall_bounds,
        },
        "reservoir_vertical_gap": reservoir_vertical_gap,
        "breach": breach_summary,
        "geometry": asdict(geom),
        "validation": validation,
        "outputs": {
            "definition_xml": str(definition_path),
            "runner_definition_xml": str(runner_definition_path),
            "summary_json": str(summary_path),
        },
    }

    if breach_enabled:
        summary["limitations"].append(
            "breach_time, breach_lift_duration and breach_lift_distance are prototype assumptions, "
            "not observed Chouldari failure parameters."
        )

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