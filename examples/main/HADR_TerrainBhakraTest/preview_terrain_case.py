"""
Debug geometry preview for HADR_TerrainChouldari.

This script visualizes the generated experimental terrain breach geometry:

- real DEM terrain surface
- terrain-derived dam centerline
- fixed dam wall segments
- centered moving breach gate
- initial and lifted breach positions
- terrain-following reservoir segments
- common reservoir water surface
- estimated upstream/downstream direction
- dam coordinate
- simulation-domain bounds

This is a debugging preview only.

It does NOT:
- run GenCase
- run DualSPHysics
- modify terrain
- modify generated geometry
- modify backend code
- modify frontend code
- modify Delft3D integration
- claim that the dam or reservoir is physically validated

The preview reads the already generated:
- terrain_case_summary.json
- HADR_TerrainChouldari_Def.xml
and the terrain NPZ/STL inputs.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from matplotlib.collections import PolyCollection
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle


CASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = CASE_DIR / "terrain_case_config.json"

if str(CASE_DIR) not in sys.path:
    sys.path.insert(0, str(CASE_DIR))

import generate_terrain_case as gt


class PreviewError(Exception):
    """Base exception for geometry preview errors."""


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

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


def _as_float(value) -> float | None:
    if value is None:
        return None

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None

    if math.isfinite(numeric):
        return numeric

    return None


def _load_json(path: Path, description: str) -> dict:
    if not path.exists():
        raise PreviewError(f"{description} not found: {path}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PreviewError(f"{description} is not valid JSON: {exc}") from exc


def load_ascii_stl_triangles(path: Path | None) -> list[tuple[tuple[float, float, float], ...]] | None:
    """
    Lightweight ASCII STL triangle reader for preview rendering only.
    """

    if path is None or not path.exists():
        return None

    triangles = []
    current = []

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("vertex"):
            parts = stripped.split()
            if len(parts) >= 4:
                try:
                    current.append(
                        (
                            float(parts[1]),
                            float(parts[2]),
                            float(parts[3]),
                        )
                    )
                except ValueError:
                    pass

        elif stripped.startswith("endfacet"):
            if len(current) == 3:
                triangles.append(tuple(current))
            current = []

    return triangles or None


# ----------------------------------------------------------------------
# Parse generated case XML
# ----------------------------------------------------------------------

def parse_drawbox(element) -> dict | None:
    point = element.find("point")
    size = element.find("size")

    if point is None or size is None:
        return None

    try:
        x0 = float(point.get("x"))
        y0 = float(point.get("y"))
        z0 = float(point.get("z"))

        sx = float(size.get("x"))
        sy = float(size.get("y"))
        sz = float(size.get("z"))
    except (TypeError, ValueError):
        return None

    if not all(math.isfinite(v) for v in (x0, y0, z0, sx, sy, sz)):
        return None

    return {
        "x0": x0,
        "x1": x0 + sx,
        "y0": y0,
        "y1": y0 + sy,
        "z0": z0,
        "z1": z0 + sz,
    }


def parse_point_element(root, xpath: str) -> tuple[float, float, float] | None:
    element = root.find(xpath)
    if element is None:
        return None

    x = _as_float(element.get("x"))
    y = _as_float(element.get("y"))
    z = _as_float(element.get("z"))

    if x is None or y is None or z is None:
        return None

    return (x, y, z)


def parse_case_xml(xml_path: Path) -> dict:
    if not xml_path.exists():
        raise PreviewError(
            f"Generated case XML not found: {xml_path}\n"
            "Run generate_terrain_case.py first."
        )

    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        raise PreviewError(f"Unable to parse generated XML: {exc}") from exc

    root = tree.getroot()

    pointmin = parse_point_element(root, ".//geometry/definition/pointmin")
    pointmax = parse_point_element(root, ".//geometry/definition/pointmax")

    mainlist = root.find(".//mainlist")
    if mainlist is None:
        raise PreviewError(f"XML does not contain <mainlist>: {xml_path}")

    current_kind = None
    stl_file = None
    fixed_boxes = []
    reservoir_boxes = []
    breach_box = None

    for child in mainlist:
        tag = child.tag

        if tag == "setmkbound":
            mk = child.get("mk")

            if mk == str(gt.FIXED_WALL_MK):
                current_kind = "fixed"
            elif mk == str(gt.BREACH_GATE_MK):
                current_kind = "breach"
            else:
                current_kind = "other_bound"

        elif tag == "setmkfluid":
            current_kind = "fluid"

        elif tag == "drawfilestl":
            stl_file = child.get("file")

        elif tag == "drawbox":
            box = parse_drawbox(child)
            if box is None:
                continue

            if current_kind == "fixed":
                fixed_boxes.append(box)
            elif current_kind == "breach":
                breach_box = box
            elif current_kind == "fluid":
                reservoir_boxes.append(box)

    return {
        "xml_path": xml_path,
        "stl_file": stl_file,
        "pointmin": pointmin,
        "pointmax": pointmax,
        "fixed_boxes": fixed_boxes,
        "breach_box": breach_box,
        "reservoir_boxes": reservoir_boxes,
    }


# ----------------------------------------------------------------------
# Geometry extraction / checks
# ----------------------------------------------------------------------

def build_geometry(cfg: dict, summary: dict, terrain: dict, parsed: dict) -> dict:
    x = terrain["x"]
    y = terrain["y"]
    z = terrain["z"]

    dam_summary = summary.get("dam", {})
    reservoir_summary = summary.get("reservoir", {})
    breach_summary = summary.get("breach", {})
    upstream_summary = summary.get("upstream", {})
    bounds_summary = summary.get("bounds", {})

    dam_x = _as_float(dam_summary.get("center_x"))
    dam_y = _as_float(dam_summary.get("center_y"))
    dam_z = _as_float(dam_summary.get("center_z"))

    if dam_x is None:
        dam_x = float(terrain["dam_x"])
    if dam_y is None:
        dam_y = float(terrain["dam_y"])
    if dam_z is None:
        dam_z = float(terrain["dam_z"])

    flow_axis = upstream_summary.get("flow_axis")
    upstream_sign = _as_float(upstream_summary.get("upstream_sign"))
    upstream_method = upstream_summary.get("method")

    if flow_axis not in {"x", "y"} or upstream_sign is None:
        flow_axis, upstream_sign, upstream_method = gt._estimate_upstream_axis(
            x,
            y,
            z,
            dam_x,
            dam_y,
            float(cfg.get("dam_search_radius", 8.0)),
        )

    upstream_sign = 1.0 if upstream_sign >= 0 else -1.0

    thickness = _as_float(dam_summary.get("thickness"))
    if thickness is None:
        thickness = float(cfg.get("dam_thickness", 0.30))

    fixed_boxes = parsed["fixed_boxes"]
    breach_box = parsed["breach_box"]
    reservoir_boxes = parsed["reservoir_boxes"]

    wall_boxes = list(fixed_boxes)
    if breach_box is not None:
        wall_boxes.append(breach_box)

    endpoints = dam_summary.get("centerline_endpoints")

    if not endpoints or len(endpoints) != 2:
        if wall_boxes:
            if flow_axis == "x":
                s_min = min(box["y0"] for box in wall_boxes)
                s_max = max(box["y1"] for box in wall_boxes)
                endpoints = [[dam_x, s_min], [dam_x, s_max]]
            else:
                s_min = min(box["x0"] for box in wall_boxes)
                s_max = max(box["x1"] for box in wall_boxes)
                endpoints = [[s_min, dam_y], [s_max, dam_y]]
        else:
            endpoints = None

    span_start = _as_float(dam_summary.get("span_start"))
    span_end = _as_float(dam_summary.get("span_end"))

    if endpoints is not None and (span_start is None or span_end is None):
        if flow_axis == "x":
            span_start = float(endpoints[0][1])
            span_end = float(endpoints[1][1])
        else:
            span_start = float(endpoints[0][0])
            span_end = float(endpoints[1][0])

    dam_span_width = None
    if span_start is not None and span_end is not None:
        dam_span_width = abs(span_end - span_start)

    crest_z = _as_float(dam_summary.get("crest_z"))

    wall_z1_values = [box["z1"] for box in wall_boxes]
    wall_z0_values = [box["z0"] for box in wall_boxes]

    if crest_z is None and wall_z1_values:
        crest_z = max(wall_z1_values)

    wall_z1_min = min(wall_z1_values) if wall_z1_values else None
    wall_z1_max = max(wall_z1_values) if wall_z1_values else None
    wall_z0_min = min(wall_z0_values) if wall_z0_values else None
    wall_z0_max = max(wall_z0_values) if wall_z0_values else None

    water_surface_z = _as_float(reservoir_summary.get("water_surface_z"))

    fluid_z1_values = [box["z1"] for box in reservoir_boxes]
    fluid_z0_values = [box["z0"] for box in reservoir_boxes]

    if water_surface_z is None and fluid_z1_values:
        water_surface_z = max(fluid_z1_values)

    fluid_z1_min = min(fluid_z1_values) if fluid_z1_values else None
    fluid_z1_max = max(fluid_z1_values) if fluid_z1_values else None
    fluid_z0_min = min(fluid_z0_values) if fluid_z0_values else None
    fluid_z0_max = max(fluid_z0_values) if fluid_z0_values else None

    water_depth_values = [
        box["z1"] - box["z0"]
        for box in reservoir_boxes
    ]

    water_depth_min = min(water_depth_values) if water_depth_values else None
    water_depth_max = max(water_depth_values) if water_depth_values else None

    breach_enabled = bool(breach_summary.get("enabled", False)) and breach_box is not None

    breach_center = _as_float(breach_summary.get("center"))
    if breach_center is None and breach_box is not None:
        if flow_axis == "x":
            breach_center = (breach_box["y0"] + breach_box["y1"]) / 2.0
        else:
            breach_center = (breach_box["x0"] + breach_box["x1"]) / 2.0

    breach_width = _as_float(breach_summary.get("width"))
    if breach_width is None and breach_box is not None:
        if flow_axis == "x":
            breach_width = breach_box["y1"] - breach_box["y0"]
        else:
            breach_width = breach_box["x1"] - breach_box["x0"]

    actual_gate_lift = _as_float(breach_summary.get("actual_gate_lift"))

    if actual_gate_lift is None:
        initial_bounds = breach_summary.get("moving_bounds_initial")
        final_bounds = breach_summary.get("moving_bounds_final")

        if initial_bounds and final_bounds:
            z0_initial = _as_float(initial_bounds.get("z0"))
            z0_final = _as_float(final_bounds.get("z0"))

            if z0_initial is not None and z0_final is not None:
                actual_gate_lift = z0_final - z0_initial

    breach_final_box = None
    if breach_box is not None and actual_gate_lift is not None:
        breach_final_box = dict(breach_box)
        breach_final_box["z0"] = breach_box["z0"] + actual_gate_lift
        breach_final_box["z1"] = breach_box["z1"] + actual_gate_lift

    fixed_left_width = _as_float(dam_summary.get("fixed_left_width"))
    fixed_right_width = _as_float(dam_summary.get("fixed_right_width"))

    if (
        breach_enabled
        and breach_box is not None
        and span_start is not None
        and span_end is not None
    ):
        if flow_axis == "x":
            computed_left = breach_box["y0"] - span_start
            computed_right = span_end - breach_box["y1"]
        else:
            computed_left = breach_box["x0"] - span_start
            computed_right = span_end - breach_box["x1"]

        if fixed_left_width is None:
            fixed_left_width = computed_left
        if fixed_right_width is None:
            fixed_right_width = computed_right

    terrain_x_min = float(np.min(x))
    terrain_x_max = float(np.max(x))
    terrain_y_min = float(np.min(y))
    terrain_y_max = float(np.max(y))
    terrain_z_min = float(np.min(z))
    terrain_z_max = float(np.max(z))

    local_terrain_min = _as_float(dam_summary.get("local_terrain_min"))
    local_terrain_max = _as_float(dam_summary.get("local_terrain_max"))

    if local_terrain_min is None:
        local_terrain_min = terrain_z_min
    if local_terrain_max is None:
        local_terrain_max = terrain_z_max

    if flow_axis == "x":
        upstream_vector = (upstream_sign, 0.0)
        dam_flow_coord = dam_x
        dam_axis_coord = dam_y
    else:
        upstream_vector = (0.0, upstream_sign)
        dam_flow_coord = dam_y
        dam_axis_coord = dam_x

    arrow_length = max(
        3.0,
        0.20 * max(terrain_x_max - terrain_x_min, terrain_y_max - terrain_y_min),
    )

    flow_arrow_start = (
        dam_x + upstream_vector[0] * arrow_length / 2.0,
        dam_y + upstream_vector[1] * arrow_length / 2.0,
    )

    flow_arrow_end = (
        dam_x - upstream_vector[0] * arrow_length / 2.0,
        dam_y - upstream_vector[1] * arrow_length / 2.0,
    )

    feature_top_candidates = [
        value
        for value in (
            crest_z,
            water_surface_z,
            local_terrain_max,
        )
        if value is not None
    ]

    feature_top = max(feature_top_candidates) if feature_top_candidates else terrain_z_max
    flow_arrow_z = feature_top + 0.30

    reservoir_length = float(cfg.get("reservoir_length", 3.0))
    fluid_bed_clearance = float(cfg.get("fluid_bed_clearance", 0.02))

    upstream_face = dam_flow_coord + upstream_sign * thickness / 2.0

    reservoir_gaps = []
    reservoir_checks = []

    for i, box in enumerate(reservoir_boxes):
        if flow_axis == "x":
            if upstream_sign > 0:
                gap = box["x0"] - upstream_face
            else:
                gap = upstream_face - box["x1"]
        else:
            if upstream_sign > 0:
                gap = box["y0"] - upstream_face
            else:
                gap = upstream_face - box["y1"]

        reservoir_gaps.append(gap)

        stats = gt._cell_stats(
            x,
            y,
            z,
            box["x0"],
            box["x1"],
            box["y0"],
            box["y1"],
        )

        cell_min = stats["min"] if stats else None
        cell_max = stats["max"] if stats else None

        reservoir_checks.append(
            {
                "segment": i,
                "box": box,
                "gap_to_upstream_dam_face": gap,
                "terrain_cell_min": cell_min,
                "terrain_cell_max": cell_max,
                "fluid_bottom_z": box["z0"],
                "fluid_top_z": box["z1"],
                "water_depth": box["z1"] - box["z0"],
                "clearance_above_cell_max": (
                    box["z0"] - cell_max if cell_max is not None else None
                ),
                "clearance_above_cell_min": (
                    box["z0"] - cell_min if cell_min is not None else None
                ),
                "overlaps_terrain": bool(
                    cell_max is not None and cell_max > box["z0"] + 1e-3
                ),
            }
        )

    dam_checks = []
    wall_expand = max(0.05, 0.25 * thickness)

    for i, box in enumerate(wall_boxes):
        stats = gt._cell_stats(
            x,
            y,
            z,
            box["x0"] - wall_expand,
            box["x1"] + wall_expand,
            box["y0"] - wall_expand,
            box["y1"] + wall_expand,
        )

        cell_min = stats["min"] if stats else None
        cell_median = stats["median"] if stats else None
        cell_max = stats["max"] if stats else None

        dam_checks.append(
            {
                "segment": i,
                "kind": "breach" if box is breach_box else "fixed",
                "box": box,
                "terrain_cell_min": cell_min,
                "terrain_cell_median": cell_median,
                "terrain_cell_max": cell_max,
                "bottom_minus_median": (
                    box["z0"] - cell_median if cell_median is not None else None
                ),
                "crest_minus_cell_max": (
                    box["z1"] - cell_max if cell_max is not None else None
                ),
                "bottom_below_median": bool(
                    cell_median is not None and box["z0"] <= cell_median + 1e-3
                ),
                "crest_above_local_terrain_max": bool(
                    cell_max is not None and box["z1"] >= cell_max - 1e-3
                ),
            }
        )

    stl_path = None
    if parsed.get("stl_file"):
        candidate = Path(parsed["stl_file"])
        if not candidate.is_absolute():
            candidate = CASE_DIR / candidate
        if candidate.exists():
            stl_path = candidate

    if stl_path is None:
        stl_path = gt._resolve_path(cfg["terrain_stl"])

    triangles = load_ascii_stl_triangles(stl_path)

    warnings = []

    if wall_z1_min is not None and wall_z1_max is not None:
        if abs(wall_z1_max - wall_z1_min) > 1e-6:
            warnings.append(
                "Dam crest is not common: "
                f"wall z1 min={wall_z1_min:.6f}, max={wall_z1_max:.6f}"
            )

    if fluid_z1_min is not None and fluid_z1_max is not None:
        if abs(fluid_z1_max - fluid_z1_min) > 1e-6:
            warnings.append(
                "Reservoir water surface is not common: "
                f"fluid z1 min={fluid_z1_min:.6f}, max={fluid_z1_max:.6f}"
            )

    if water_depth_min is not None and water_depth_min <= 0.0:
        warnings.append(
            f"Minimum reservoir water depth is non-positive: {water_depth_min:.6f}"
        )

    if reservoir_gaps:
        gap_min = min(reservoir_gaps)
        gap_max = max(reservoir_gaps)

        if gap_min < -1e-3:
            warnings.append(
                f"Reservoir overlaps upstream dam face by {-gap_min:.6f} m"
            )

        if gap_max > fluid_bed_clearance + 1e-3:
            warnings.append(
                "Reservoir is detached from upstream dam face: "
                f"maximum gap={gap_max:.6f} m"
            )

    if any(check["overlaps_terrain"] for check in reservoir_checks):
        warnings.append(
            "One or more reservoir segments overlap sampled terrain."
        )

    floating_gaps = [
        check["clearance_above_cell_min"]
        for check in reservoir_checks
        if check["clearance_above_cell_min"] is not None
    ]

    if floating_gaps:
        max_float_gap = max(floating_gaps)
        if max_float_gap > 0.25:
            warnings.append(
                "Reservoir bottom floats above the lowest local terrain in at least one segment: "
                f"maximum gap above cell min = {max_float_gap:.6f} m"
            )

    if any(not check["crest_above_local_terrain_max"] for check in dam_checks):
        warnings.append(
            "One or more dam segments have a crest below the sampled local terrain maximum."
        )

    if any(not check["bottom_below_median"] for check in dam_checks):
        warnings.append(
            "One or more dam segments are not anchored below the sampled local terrain median."
        )

    if breach_enabled:
        dam_axis_center = dam_axis_coord

        if breach_center is not None and abs(breach_center - dam_axis_center) > 1e-6:
            warnings.append(
                "Breach is not centered on dam centerline: "
                f"breach_center={breach_center:.6f}, dam_center={dam_axis_center:.6f}"
            )

        if fixed_left_width is not None and fixed_left_width <= 1e-3:
            warnings.append("No meaningful fixed dam width remains on the left side.")

        if fixed_right_width is not None and fixed_right_width <= 1e-3:
            warnings.append("No meaningful fixed dam width remains on the right side.")

        if breach_final_box is not None and parsed["pointmax"] is not None:
            if breach_final_box["z1"] > parsed["pointmax"][2] + 1e-6:
                warnings.append(
                    "Final lifted breach top exceeds simulation pointmax_z."
                )

    return {
        "terrain_x": x,
        "terrain_y": y,
        "terrain_z": z,
        "triangles": triangles,
        "stl_path": stl_path,
        "terrain_bounds": {
            "x_min": terrain_x_min,
            "x_max": terrain_x_max,
            "y_min": terrain_y_min,
            "y_max": terrain_y_max,
            "z_min": terrain_z_min,
            "z_max": terrain_z_max,
        },
        "pointmin": parsed["pointmin"],
        "pointmax": parsed["pointmax"],
        "flow_axis": flow_axis,
        "upstream_sign": upstream_sign,
        "upstream_method": upstream_method,
        "dam_x": dam_x,
        "dam_y": dam_y,
        "dam_z": dam_z,
        "dam_thickness": thickness,
        "dam_centerline_endpoints": endpoints,
        "span_start": span_start,
        "span_end": span_end,
        "dam_span_width": dam_span_width,
        "crest_z": crest_z,
        "wall_z1_min": wall_z1_min,
        "wall_z1_max": wall_z1_max,
        "wall_z0_min": wall_z0_min,
        "wall_z0_max": wall_z0_max,
        "local_terrain_min": local_terrain_min,
        "local_terrain_max": local_terrain_max,
        "fixed_boxes": fixed_boxes,
        "breach_box": breach_box,
        "breach_final_box": breach_final_box,
        "breach_enabled": breach_enabled,
        "breach_center": breach_center,
        "breach_width": breach_width,
        "actual_gate_lift": actual_gate_lift,
        "fixed_left_width": fixed_left_width,
        "fixed_right_width": fixed_right_width,
        "reservoir_boxes": reservoir_boxes,
        "reservoir_length": reservoir_length,
        "water_surface_z": water_surface_z,
        "fluid_z0_min": fluid_z0_min,
        "fluid_z0_max": fluid_z0_max,
        "fluid_z1_min": fluid_z1_min,
        "fluid_z1_max": fluid_z1_max,
        "water_depth_min": water_depth_min,
        "water_depth_max": water_depth_max,
        "reservoir_gaps": reservoir_gaps,
        "reservoir_checks": reservoir_checks,
        "dam_checks": dam_checks,
        "flow_arrow_start": flow_arrow_start,
        "flow_arrow_end": flow_arrow_end,
        "flow_arrow_z": flow_arrow_z,
        "arrow_length": arrow_length,
        "warnings": warnings,
    }


# ----------------------------------------------------------------------
# PNG preview
# ----------------------------------------------------------------------

def draw_section(
    ax,
    geom: dict,
    horizontal_axis: str,
    band_axis: str,
    band_center: float,
    band_half: float,
    h_center: float,
    h_half: float,
    include_reservoir: bool,
    title: str,
    show_flow: bool,
) -> None:
    tx = geom["terrain_x"]
    ty = geom["terrain_y"]
    tz = geom["terrain_z"]

    h_all = tx if horizontal_axis == "x" else ty
    band_all = ty if band_axis == "y" else tx

    mask = (
        (np.abs(band_all - band_center) <= band_half)
        & (np.abs(h_all - h_center) <= h_half)
    )

    h_vals: list[float] = []
    z_vals: list[float] = []

    if np.any(mask):
        h_band = h_all[mask]
        z_band = tz[mask]

        ax.scatter(
            h_band,
            z_band,
            s=8,
            color="saddlebrown",
            alpha=0.70,
            zorder=3,
            label="Terrain points in band",
        )

        h_vals.extend(h_band.tolist())
        z_vals.extend(z_band.tolist())

    def h_interval(box: dict) -> tuple[float, float]:
        if horizontal_axis == "x":
            return box["x0"], box["x1"]
        return box["y0"], box["y1"]

    def band_interval(box: dict) -> tuple[float, float]:
        if band_axis == "y":
            return box["y0"], box["y1"]
        return box["x0"], box["x1"]

    def overlaps_band(box: dict) -> bool:
        b0, b1 = band_interval(box)
        return b0 <= band_center + band_half and b1 >= band_center - band_half

    selected_fixed = [box for box in geom["fixed_boxes"] if overlaps_band(box)]
    selected_breach = (
        geom["breach_box"]
        if geom["breach_box"] is not None and overlaps_band(geom["breach_box"])
        else None
    )
    selected_final = (
        geom["breach_final_box"]
        if geom["breach_final_box"] is not None and overlaps_band(geom["breach_final_box"])
        else None
    )

    selected_reservoir = []
    if include_reservoir:
        selected_reservoir = [
            box for box in geom["reservoir_boxes"] if overlaps_band(box)
        ]

    for i, box in enumerate(selected_reservoir):
        h0, h1 = h_interval(box)
        ax.add_patch(
            Rectangle(
                (h0, box["z0"]),
                h1 - h0,
                box["z1"] - box["z0"],
                facecolor="tab:blue",
                edgecolor="tab:blue",
                alpha=0.35,
                zorder=5,
                label="Reservoir segment" if i == 0 else None,
            )
        )

        h_vals.extend([h0, h1])
        z_vals.extend([box["z0"], box["z1"]])

    for i, box in enumerate(selected_fixed):
        h0, h1 = h_interval(box)
        ax.add_patch(
            Rectangle(
                (h0, box["z0"]),
                h1 - h0,
                box["z1"] - box["z0"],
                facecolor="gray",
                edgecolor="black",
                alpha=0.85,
                zorder=6,
                label="Fixed dam segment" if i == 0 else None,
            )
        )

        h_vals.extend([h0, h1])
        z_vals.extend([box["z0"], box["z1"]])

    if selected_breach is not None:
        h0, h1 = h_interval(selected_breach)
        ax.add_patch(
            Rectangle(
                (h0, selected_breach["z0"]),
                h1 - h0,
                selected_breach["z1"] - selected_breach["z0"],
                facecolor="red",
                edgecolor="darkred",
                alpha=0.90,
                zorder=7,
                label="Moving breach initial",
            )
        )

        h_vals.extend([h0, h1])
        z_vals.extend([selected_breach["z0"], selected_breach["z1"]])

    if selected_final is not None:
        h0, h1 = h_interval(selected_final)
        ax.add_patch(
            Rectangle(
                (h0, selected_final["z0"]),
                h1 - h0,
                selected_final["z1"] - selected_final["z0"],
                facecolor="none",
                edgecolor="orange",
                linestyle="--",
                linewidth=1.8,
                zorder=8,
                label="Moving breach final",
            )
        )

        h_vals.extend([h0, h1])
        z_vals.extend([selected_final["z0"], selected_final["z1"]])

    if (
        include_reservoir
        and selected_reservoir
        and geom["water_surface_z"] is not None
    ):
        h_min = min(h_interval(box)[0] for box in selected_reservoir)
        h_max = max(h_interval(box)[1] for box in selected_reservoir)

        ax.plot(
            [h_min, h_max],
            [geom["water_surface_z"], geom["water_surface_z"]],
            color="blue",
            linewidth=2.0,
            zorder=9,
            label=f"Water surface z={geom['water_surface_z']:.4f}",
        )

    wall_selected = list(selected_fixed)
    if selected_breach is not None:
        wall_selected.append(selected_breach)

    if wall_selected and geom["crest_z"] is not None:
        h_min = min(h_interval(box)[0] for box in wall_selected)
        h_max = max(h_interval(box)[1] for box in wall_selected)

        ax.plot(
            [h_min, h_max],
            [geom["crest_z"], geom["crest_z"]],
            color="black",
            linestyle="--",
            linewidth=1.6,
            zorder=9,
            label=f"Dam crest z={geom['crest_z']:.4f}",
        )

    dam_h = geom["dam_x"] if horizontal_axis == "x" else geom["dam_y"]

    ax.axvline(
        dam_h,
        color="red",
        linestyle=":",
        linewidth=1.2,
        alpha=0.7,
        zorder=4,
    )

    if show_flow:
        arrow_z = geom["flow_arrow_z"]
        upstream_h = dam_h + geom["upstream_sign"] * geom["arrow_length"] / 2.0
        downstream_h = dam_h - geom["upstream_sign"] * geom["arrow_length"] / 2.0

        ax.annotate(
            "",
            xy=(downstream_h, arrow_z),
            xytext=(upstream_h, arrow_z),
            arrowprops=dict(
                arrowstyle="-|>",
                color="magenta",
                linewidth=2.0,
            ),
        )

        ax.text(
            upstream_h,
            arrow_z + 0.03,
            "UPSTREAM",
            color="magenta",
            ha="center",
            fontsize=8,
        )

        ax.text(
            downstream_h,
            arrow_z + 0.03,
            "DOWNSTREAM",
            color="magenta",
            ha="center",
            fontsize=8,
        )

        h_vals.extend([upstream_h, downstream_h])
        z_vals.append(arrow_z)

    if h_vals:
        h_min = min(h_vals)
        h_max = max(h_vals)
        h_margin = max(0.25, 0.05 * (h_max - h_min))
        ax.set_xlim(h_min - h_margin, h_max + h_margin)

    if z_vals:
        z_min = min(z_vals)
        z_max = max(z_vals)
        z_margin = max(0.25, 0.05 * (z_max - z_min))
        ax.set_ylim(z_min - z_margin, z_max + z_margin)

    ax.set_title(title, fontsize=10)
    ax.set_xlabel(
        "X (simulation m)" if horizontal_axis == "x" else "Y (simulation m)"
    )
    ax.set_ylabel("Z (simulation m)")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7, loc="upper right")


def create_png(geom: dict, out_path: Path, case_name: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(16, 13))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1.0], hspace=0.28, wspace=0.22)

    ax_plan = fig.add_subplot(gs[0, :])
    ax_flow = fig.add_subplot(gs[1, 0])
    ax_dam = fig.add_subplot(gs[1, 1])

    bounds = geom["terrain_bounds"]

    x_margin = max(0.5, 0.05 * (bounds["x_max"] - bounds["x_min"]))
    y_margin = max(0.5, 0.05 * (bounds["y_max"] - bounds["y_min"]))

    ax_plan.set_xlim(bounds["x_min"] - x_margin, bounds["x_max"] + x_margin)
    ax_plan.set_ylim(bounds["y_min"] - y_margin, bounds["y_max"] + y_margin)

    z_min = bounds["z_min"]
    z_max = bounds["z_max"]

    triangles = geom.get("triangles")

    if triangles:
        norm = Normalize(vmin=z_min, vmax=z_max)
        cmap = plt.get_cmap("terrain")

        polys = []
        colors = []

        for tri in triangles:
            mean_z = (tri[0][2] + tri[1][2] + tri[2][2]) / 3.0

            polys.append(
                [
                    (tri[0][0], tri[0][1]),
                    (tri[1][0], tri[1][1]),
                    (tri[2][0], tri[2][1]),
                ]
            )
            colors.append(cmap(norm(mean_z)))

        pc = PolyCollection(
            polys,
            facecolors=colors,
            edgecolors="none",
            alpha=0.95,
            zorder=1,
        )

        ax_plan.add_collection(pc)

        sm = ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        fig.colorbar(sm, ax=ax_plan, shrink=0.80, label="Terrain elevation z_sim")
    else:
        sc = ax_plan.scatter(
            geom["terrain_x"],
            geom["terrain_y"],
            c=geom["terrain_z"],
            cmap="terrain",
            s=8,
            edgecolors="none",
            alpha=0.9,
            zorder=1,
        )
        fig.colorbar(sc, ax=ax_plan, label="Terrain elevation z_sim")

    if geom["pointmin"] is not None and geom["pointmax"] is not None:
        pmin = geom["pointmin"]
        pmax = geom["pointmax"]

        ax_plan.add_patch(
            Rectangle(
                (pmin[0], pmin[1]),
                pmax[0] - pmin[0],
                pmax[1] - pmin[1],
                fill=False,
                edgecolor="dimgray",
                linestyle=":",
                linewidth=1.4,
                zorder=2,
            )
        )

    for box in geom["reservoir_boxes"]:
        ax_plan.add_patch(
            Rectangle(
                (box["x0"], box["y0"]),
                box["x1"] - box["x0"],
                box["y1"] - box["y0"],
                facecolor="tab:blue",
                edgecolor="tab:blue",
                alpha=0.35,
                zorder=5,
            )
        )

    for box in geom["fixed_boxes"]:
        ax_plan.add_patch(
            Rectangle(
                (box["x0"], box["y0"]),
                box["x1"] - box["x0"],
                box["y1"] - box["y0"],
                facecolor="gray",
                edgecolor="black",
                alpha=0.85,
                zorder=6,
            )
        )

    breach = geom["breach_box"]
    if breach is not None:
        ax_plan.add_patch(
            Rectangle(
                (breach["x0"], breach["y0"]),
                breach["x1"] - breach["x0"],
                breach["y1"] - breach["y0"],
                facecolor="red",
                edgecolor="darkred",
                alpha=0.95,
                zorder=7,
            )
        )

        if geom["flow_axis"] == "x":
            cx = (breach["x0"] + breach["x1"]) / 2.0

            ax_plan.annotate(
                "",
                xy=(cx, breach["y0"]),
                xytext=(cx, breach["y1"]),
                arrowprops=dict(arrowstyle="<->", color="red", lw=1.4),
            )

            ax_plan.text(
                cx,
                breach["y1"] + 0.15,
                f"breach {_fmt(geom['breach_width'])} m",
                color="red",
                ha="center",
                fontsize=8,
                zorder=10,
            )
        else:
            cy = (breach["y0"] + breach["y1"]) / 2.0

            ax_plan.annotate(
                "",
                xy=(breach["x0"], cy),
                xytext=(breach["x1"], cy),
                arrowprops=dict(arrowstyle="<->", color="red", lw=1.4),
            )

            ax_plan.text(
                breach["x1"] + 0.15,
                cy,
                f"breach {_fmt(geom['breach_width'])} m",
                color="red",
                ha="left",
                va="center",
                fontsize=8,
                zorder=10,
            )

    endpoints = geom["dam_centerline_endpoints"]
    if endpoints and len(endpoints) == 2:
        ax_plan.plot(
            [endpoints[0][0], endpoints[1][0]],
            [endpoints[0][1], endpoints[1][1]],
            color="black",
            linestyle="--",
            linewidth=1.6,
            zorder=8,
        )

    ax_plan.plot(
        [geom["dam_x"]],
        [geom["dam_y"]],
        marker="*",
        color="red",
        markeredgecolor="black",
        markersize=16,
        zorder=10,
    )

    ax_plan.annotate(
        "",
        xy=geom["flow_arrow_end"],
        xytext=geom["flow_arrow_start"],
        arrowprops=dict(
            arrowstyle="-|>",
            color="magenta",
            linewidth=2.2,
        ),
    )

    ux, uy = (
        (geom["upstream_sign"], 0.0)
        if geom["flow_axis"] == "x"
        else (0.0, geom["upstream_sign"])
    )

    px, py = -uy, ux
    text_offset = max(0.35, geom["arrow_length"] * 0.10)

    ax_plan.text(
        geom["flow_arrow_start"][0] + px * text_offset,
        geom["flow_arrow_start"][1] + py * text_offset,
        "UPSTREAM",
        color="magenta",
        fontsize=9,
        ha="center",
        va="center",
        zorder=11,
    )

    ax_plan.text(
        geom["flow_arrow_end"][0] + px * text_offset,
        geom["flow_arrow_end"][1] + py * text_offset,
        "DOWNSTREAM",
        color="magenta",
        fontsize=9,
        ha="center",
        va="center",
        zorder=11,
    )

    ax_plan.set_title(
        "Plan view: DEM terrain, generated dam, breach, reservoir and flow direction",
        fontsize=11,
    )
    ax_plan.set_xlabel("X (simulation m)")
    ax_plan.set_ylabel("Y (simulation m)")
    ax_plan.set_aspect("equal")
    ax_plan.grid(True, alpha=0.25)

    legend_handles = [
        Line2D([], [], marker="s", color="none", markerfacecolor="saddlebrown", markersize=7, label="DEM terrain"),
        Patch(facecolor="gray", edgecolor="black", alpha=0.85, label="Fixed dam segments"),
        Patch(facecolor="red", edgecolor="darkred", alpha=0.95, label="Moving breach initial"),
        Patch(facecolor="none", edgecolor="orange", linestyle="--", label="Moving breach final"),
        Patch(facecolor="tab:blue", edgecolor="tab:blue", alpha=0.35, label="Reservoir segments"),
        Line2D([], [], color="blue", lw=2, label=f"Water surface z={_fmt(geom['water_surface_z'])}"),
        Line2D([], [], color="black", lw=1.6, ls="--", label=f"Dam crest z={_fmt(geom['crest_z'])}"),
        Line2D([], [], color="magenta", lw=2, marker=">", label="Downstream direction"),
        Line2D([], [], color="red", marker="*", ls="none", markersize=12, label="Dam center"),
    ]

    ax_plan.legend(handles=legend_handles, loc="upper right", fontsize=8)

    if geom["flow_axis"] == "x":
        flow_band_center = geom["dam_y"]
        flow_band_half = max(
            0.75,
            geom["dam_thickness"] * 2.0,
            (geom["breach_width"] or 0.0) / 2.0 + 0.50,
        )
        flow_h_center = geom["dam_x"]
        flow_h_half = max(
            geom["reservoir_length"] + geom["dam_thickness"] + 2.0,
            4.0,
        )

        dam_band_center = geom["dam_x"]
        dam_band_half = max(0.75, geom["dam_thickness"] * 2.0)
        dam_h_center = geom["dam_y"]
        dam_h_half = max((geom["dam_span_width"] or 2.0) / 2.0 + 2.0, 3.0)

        flow_horizontal_axis = "x"
        flow_band_axis = "y"
        dam_horizontal_axis = "y"
        dam_band_axis = "x"
    else:
        flow_band_center = geom["dam_x"]
        flow_band_half = max(
            0.75,
            geom["dam_thickness"] * 2.0,
            (geom["breach_width"] or 0.0) / 2.0 + 0.50,
        )
        flow_h_center = geom["dam_y"]
        flow_h_half = max(
            geom["reservoir_length"] + geom["dam_thickness"] + 2.0,
            4.0,
        )

        dam_band_center = geom["dam_y"]
        dam_band_half = max(0.75, geom["dam_thickness"] * 2.0)
        dam_h_center = geom["dam_x"]
        dam_h_half = max((geom["dam_span_width"] or 2.0) / 2.0 + 2.0, 3.0)

        flow_horizontal_axis = "y"
        flow_band_axis = "x"
        dam_horizontal_axis = "x"
        dam_band_axis = "y"

    draw_section(
        ax=ax_flow,
        geom=geom,
        horizontal_axis=flow_horizontal_axis,
        band_axis=flow_band_axis,
        band_center=flow_band_center,
        band_half=flow_band_half,
        h_center=flow_h_center,
        h_half=flow_h_half,
        include_reservoir=True,
        title="Flow-axis section through dam center",
        show_flow=True,
    )

    draw_section(
        ax=ax_dam,
        geom=geom,
        horizontal_axis=dam_horizontal_axis,
        band_axis=dam_band_axis,
        band_center=dam_band_center,
        band_half=dam_band_half,
        h_center=dam_h_center,
        h_half=dam_h_half,
        include_reservoir=False,
        title="Dam-axis section: wall anchoring and breach",
        show_flow=False,
    )

    fig.suptitle(
        f"{case_name} terrain breach geometry debug preview",
        fontsize=14,
    )

    fig.text(
        0.5,
        0.01,
        (
            "Debug preview only. Copernicus GLO-30 DSM terrain, scaled prototype geometry. "
            "Not a physically validated dam, reservoir, breach, or flood model."
        ),
        ha="center",
        fontsize=8,
        color="dimgray",
    )

    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


# ----------------------------------------------------------------------
# VTK preview
# ----------------------------------------------------------------------

def write_vtk(geom: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    points: list[tuple[float, float, float]] = []
    elevations: list[float] = []
    categories: list[int] = []

    vertices: list[int] = []
    lines: list[tuple[int, int]] = []
    polygons: list[tuple[int, ...]] = []

    def add_point(x: float, y: float, z: float, category: int) -> int:
        points.append((float(x), float(y), float(z)))
        elevations.append(float(z))
        categories.append(int(category))
        return len(points) - 1

    def add_box_edges(box: dict, category: int, z_shift: float = 0.0) -> None:
        x0 = box["x0"]
        x1 = box["x1"]
        y0 = box["y0"]
        y1 = box["y1"]
        z0 = box["z0"] + z_shift
        z1 = box["z1"] + z_shift

        idxs = [
            add_point(x0, y0, z0, category),
            add_point(x1, y0, z0, category),
            add_point(x1, y1, z0, category),
            add_point(x0, y1, z0, category),
            add_point(x0, y0, z1, category),
            add_point(x1, y0, z1, category),
            add_point(x1, y1, z1, category),
            add_point(x0, y1, z1, category),
        ]

        edge_pairs = [
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        ]

        for a, b in edge_pairs:
            lines.append((idxs[a], idxs[b]))

    triangles = geom.get("triangles")

    if triangles:
        for tri in triangles:
            idxs = [
                add_point(tri[0][0], tri[0][1], tri[0][2], 0),
                add_point(tri[1][0], tri[1][1], tri[1][2], 0),
                add_point(tri[2][0], tri[2][1], tri[2][2], 0),
            ]
            polygons.append(tuple(idxs))
    else:
        for xi, yi, zi in zip(geom["terrain_x"], geom["terrain_y"], geom["terrain_z"]):
            vertices.append(add_point(xi, yi, zi, 0))

    if geom["pointmin"] is not None and geom["pointmax"] is not None:
        pmin = geom["pointmin"]
        pmax = geom["pointmax"]

        add_box_edges(
            {
                "x0": pmin[0],
                "x1": pmax[0],
                "y0": pmin[1],
                "y1": pmax[1],
                "z0": pmin[2],
                "z1": pmax[2],
            },
            category=8,
        )

    endpoints = geom["dam_centerline_endpoints"]
    crest_z = geom["crest_z"]

    if endpoints and len(endpoints) == 2 and crest_z is not None:
        i0 = add_point(endpoints[0][0], endpoints[0][1], crest_z, 7)
        i1 = add_point(endpoints[1][0], endpoints[1][1], crest_z, 7)
        lines.append((i0, i1))

    for box in geom["fixed_boxes"]:
        add_box_edges(box, 1)

    if geom["breach_box"] is not None:
        add_box_edges(geom["breach_box"], 2)

    if geom["breach_final_box"] is not None:
        add_box_edges(geom["breach_final_box"], 3)

    for box in geom["reservoir_boxes"]:
        add_box_edges(box, 4)

        z_ws = box["z1"]

        idxs = [
            add_point(box["x0"], box["y0"], z_ws, 5),
            add_point(box["x1"], box["y0"], z_ws, 5),
            add_point(box["x1"], box["y1"], z_ws, 5),
            add_point(box["x0"], box["y1"], z_ws, 5),
        ]

        polygons.append(tuple(idxs))

    start = geom["flow_arrow_start"]
    end = geom["flow_arrow_end"]
    z_arrow = geom["flow_arrow_z"]

    i_start = add_point(start[0], start[1], z_arrow, 6)
    i_end = add_point(end[0], end[1], z_arrow, 6)
    lines.append((i_start, i_end))

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)

    if length > 1e-9:
        ux = dx / length
        uy = dy / length

        px = -uy
        py = ux

        head_len = min(0.75, max(0.25, 0.25 * length))
        head_width = 0.35 * head_len

        base_x = end[0] - ux * head_len
        base_y = end[1] - uy * head_len

        p1 = add_point(base_x + px * head_width, base_y + py * head_width, z_arrow, 6)
        p2 = add_point(base_x - px * head_width, base_y - py * head_width, z_arrow, 6)

        lines.append((i_end, p1))
        lines.append((i_end, p2))

    with out_path.open("w", encoding="ascii", newline="\n") as file:
        file.write("# vtk DataFile Version 5.0\n")
        file.write("HADR_TerrainChouldari geometry debug preview\n")
        file.write("ASCII\n")
        file.write("DATASET POLYDATA\n")

        file.write(f"POINTS {len(points)} float\n")
        for px, py, pz in points:
            file.write(f"{px:.8f} {py:.8f} {pz:.8f}\n")

        if vertices:
            file.write(f"VERTICES {len(vertices)} {2 * len(vertices)}\n")
            for idx in vertices:
                file.write(f"1 {idx}\n")

        if lines:
            file.write(f"LINES {len(lines)} {3 * len(lines)}\n")
            for i0, i1 in lines:
                file.write(f"2 {i0} {i1}\n")

        if polygons:
            total_size = sum(len(poly) + 1 for poly in polygons)
            file.write(f"POLYGONS {len(polygons)} {total_size}\n")
            for poly in polygons:
                indices = " ".join(str(idx) for idx in poly)
                file.write(f"{len(poly)} {indices}\n")

        file.write(f"POINT_DATA {len(points)}\n")

        file.write("SCALARS category int 1\n")
        file.write("LOOKUP_TABLE default\n")
        for category in categories:
            file.write(f"{category}\n")

        file.write("SCALARS elevation float 1\n")
        file.write("LOOKUP_TABLE default\n")
        for elevation in elevations:
            file.write(f"{elevation:.8f}\n")


# ----------------------------------------------------------------------
# Metadata
# ----------------------------------------------------------------------

def build_metadata(
    cfg: dict,
    summary: dict,
    geom: dict,
    parsed: dict,
    paths: dict,
) -> dict:
    reservoir_gaps = geom["reservoir_gaps"]

    return {
        "preview_type": "debug_geometry_preview",
        "disclaimer": (
            "Debug preview only. This is not a physically validated dam, reservoir, "
            "breach, or flood model. Terrain is Copernicus GLO-30 DSM data and has been "
            "numerically scaled for prototype SPH integration."
        ),
        "inputs": {
            "config": str(paths["config"]),
            "summary": str(paths["summary"]),
            "case_xml": str(paths["xml"]),
            "terrain_npz": str(geom.get("npz_path", cfg.get("terrain_npz"))),
            "terrain_stl": str(geom.get("stl_path")),
        },
        "outputs": {
            "png": str(paths["png"]),
            "vtk": str(paths["vtk"]),
            "json": str(paths["json"]),
        },
        "terrain_bounds": geom["terrain_bounds"],
        "simulation_domain": {
            "pointmin": parsed["pointmin"],
            "pointmax": parsed["pointmax"],
        },
        "upstream": {
            "flow_axis": geom["flow_axis"],
            "upstream_sign": geom["upstream_sign"],
            "method": geom["upstream_method"],
        },
        "dam": {
            "center_x": geom["dam_x"],
            "center_y": geom["dam_y"],
            "center_z": geom["dam_z"],
            "centerline_endpoints": geom["dam_centerline_endpoints"],
            "span_start": geom["span_start"],
            "span_end": geom["span_end"],
            "span_width": geom["dam_span_width"],
            "thickness": geom["dam_thickness"],
            "crest_z": geom["crest_z"],
            "actual_wall_z1_min": geom["wall_z1_min"],
            "actual_wall_z1_max": geom["wall_z1_max"],
            "wall_bottom_min": geom["wall_z0_min"],
            "wall_bottom_max": geom["wall_z0_max"],
            "local_terrain_min": geom["local_terrain_min"],
            "local_terrain_max": geom["local_terrain_max"],
            "fixed_left_width": geom["fixed_left_width"],
            "fixed_right_width": geom["fixed_right_width"],
            "fixed_segment_count": len(geom["fixed_boxes"]),
        },
        "breach": {
            "enabled": geom["breach_enabled"],
            "center": geom["breach_center"],
            "width": geom["breach_width"],
            "actual_gate_lift": geom["actual_gate_lift"],
            "initial_box": geom["breach_box"],
            "final_box": geom["breach_final_box"],
            "motion_file": summary.get("breach", {}).get("motion_file"),
            "breach_time": summary.get("breach", {}).get("breach_time"),
            "lift_duration": summary.get("breach", {}).get("lift_duration"),
        },
        "reservoir": {
            "segment_count": len(geom["reservoir_boxes"]),
            "x_min": min((box["x0"] for box in geom["reservoir_boxes"]), default=None),
            "x_max": max((box["x1"] for box in geom["reservoir_boxes"]), default=None),
            "y_min": min((box["y0"] for box in geom["reservoir_boxes"]), default=None),
            "y_max": max((box["y1"] for box in geom["reservoir_boxes"]), default=None),
            "z_min": geom["fluid_z0_min"],
            "z_max": geom["fluid_z1_max"],
            "water_surface_z": geom["water_surface_z"],
            "actual_fluid_z1_min": geom["fluid_z1_min"],
            "actual_fluid_z1_max": geom["fluid_z1_max"],
            "water_depth_min": geom["water_depth_min"],
            "water_depth_max": geom["water_depth_max"],
            "gap_to_upstream_dam_face_min": min(reservoir_gaps) if reservoir_gaps else None,
            "gap_to_upstream_dam_face_max": max(reservoir_gaps) if reservoir_gaps else None,
        },
        "terrain_contact_checks": {
            "reservoir": geom["reservoir_checks"],
            "dam": geom["dam_checks"],
        },
        "parsed_geometry": {
            "fixed_boxes": geom["fixed_boxes"],
            "breach_box": geom["breach_box"],
            "reservoir_boxes": geom["reservoir_boxes"],
        },
        "vtk_category_legend": {
            "0": "terrain",
            "1": "fixed dam wall",
            "2": "moving breach initial",
            "3": "moving breach final",
            "4": "reservoir volume",
            "5": "reservoir water surface",
            "6": "flow direction arrow",
            "7": "dam centerline at crest",
            "8": "simulation domain box",
        },
        "warnings": geom["warnings"],
        "summary_validation_status": summary.get("validation", {}).get("status"),
        "dualsphysics_run_attempted": False,
    }


# ----------------------------------------------------------------------
# Terminal report
# ----------------------------------------------------------------------

def print_report(geom: dict, metadata: dict, paths: dict) -> None:
    print("=" * 78)
    print("HADR_TerrainChouldari geometry debug preview")
    print("=" * 78)

    print("Purpose: visual debugging only. No GenCase or DualSPHysics run attempted.")
    print("DualSPHysics run attempted: False")
    print()

    print("Outputs:")
    print(f"  PNG: {paths['png']}")
    print(f"  VTK: {paths['vtk']}")
    print(f"  JSON metadata: {paths['json']}")
    print()

    print("Terrain bounds:")
    tb = geom["terrain_bounds"]
    print(f"  x: {_fmt(tb['x_min'])} to {_fmt(tb['x_max'])}")
    print(f"  y: {_fmt(tb['y_min'])} to {_fmt(tb['y_max'])}")
    print(f"  z: {_fmt(tb['z_min'])} to {_fmt(tb['z_max'])}")
    print()

    print("Upstream/downstream estimate:")
    print(f"  flow axis: {geom['flow_axis']}")
    print(f"  upstream sign: {geom['upstream_sign']:+.0f}")
    print(f"  method: {geom['upstream_method']}")
    print()

    print("Dam:")
    print(f"  center: x={_fmt(geom['dam_x'])}, y={_fmt(geom['dam_y'])}, z={_fmt(geom['dam_z'])}")
    print(f"  centerline endpoints: {geom['dam_centerline_endpoints']}")
    print(f"  span: {_fmt(geom['span_start'])} to {_fmt(geom['span_end'])}")
    print(f"  span width: {_fmt(geom['dam_span_width'])}")
    print(f"  thickness: {_fmt(geom['dam_thickness'])}")
    print(f"  common crest_z: {_fmt(geom['crest_z'])}")
    print(f"  actual wall z1 min/max: {_fmt(geom['wall_z1_min'])} to {_fmt(geom['wall_z1_max'])}")
    print(f"  wall bottom min/max: {_fmt(geom['wall_z0_min'])} to {_fmt(geom['wall_z0_max'])}")
    print(f"  local terrain min/max: {_fmt(geom['local_terrain_min'])} to {_fmt(geom['local_terrain_max'])}")
    print(f"  fixed left width: {_fmt(geom['fixed_left_width'])}")
    print(f"  fixed right width: {_fmt(geom['fixed_right_width'])}")
    print(f"  fixed segment count: {len(geom['fixed_boxes'])}")
    print()

    print("Reservoir:")
    res = metadata["reservoir"]
    print(f"  segment count: {res['segment_count']}")
    print(f"  bounds x: {_fmt(res['x_min'])} to {_fmt(res['x_max'])}")
    print(f"  bounds y: {_fmt(res['y_min'])} to {_fmt(res['y_max'])}")
    print(f"  bounds z: {_fmt(res['z_min'])} to {_fmt(res['z_max'])}")
    print(f"  common water_surface_z: {_fmt(res['water_surface_z'])}")
    print(f"  actual fluid z1 min/max: {_fmt(res['actual_fluid_z1_min'])} to {_fmt(res['actual_fluid_z1_max'])}")
    print(f"  minimum water depth: {_fmt(res['water_depth_min'])}")
    print(f"  maximum water depth: {_fmt(res['water_depth_max'])}")
    print(f"  gap to upstream dam face min/max: "
          f"{_fmt(res['gap_to_upstream_dam_face_min'])} to {_fmt(res['gap_to_upstream_dam_face_max'])}")
    print()

    print("Breach:")
    print(f"  enabled: {geom['breach_enabled']}")
    if geom["breach_enabled"]:
        print(f"  center: {_fmt(geom['breach_center'])}")
        print(f"  width: {_fmt(geom['breach_width'])}")
        print(f"  actual gate lift: {_fmt(geom['actual_gate_lift'])}")
        print(f"  initial box: {geom['breach_box']}")
        print(f"  final box: {geom['breach_final_box']}")
    print()

    if geom["warnings"]:
        print("Geometry warnings:")
        for warning in geom["warnings"]:
            print(f"  WARNING: {warning}")
        print()
    else:
        print("Geometry warnings: none detected by this preview.")
        print()

    print("Disclaimer:")
    print("  This is a debug preview of automatically generated prototype geometry.")
    print("  It is not a physically validated dam, reservoir, breach, or flood model.")


# ----------------------------------------------------------------------
# Main preview workflow
# ----------------------------------------------------------------------

def run_preview(
    config_path: Path,
    summary_path: Path | None = None,
    xml_path: Path | None = None,
    png_path: Path | None = None,
    vtk_path: Path | None = None,
    json_path: Path | None = None,
    write_vtk_output: bool = True,
) -> dict:
    cfg = _load_json(config_path, "Config file")

    case_name = str(cfg.get("case_name", "HADR_TerrainChouldari"))

    if summary_path is None:
        summary_path = CASE_DIR / str(cfg.get("summary_name", "terrain_case_summary.json"))

    summary = _load_json(summary_path, "terrain_case_summary.json")

    if xml_path is None:
        summary_xml = summary.get("outputs", {}).get("definition_xml")
        if summary_xml:
            xml_path = Path(summary_xml)
        else:
            xml_path = CASE_DIR / str(cfg.get("definition_name", f"{case_name}_Def.xml"))

    parsed = parse_case_xml(Path(xml_path))

    terrain = gt._load_terrain(cfg)
    terrain["npz_path"] = str(terrain["npz_path"])

    geom = build_geometry(cfg, summary, terrain, parsed)

    if png_path is None:
        png_path = CASE_DIR / f"{case_name}_geometry_preview.png"

    if json_path is None:
        json_path = CASE_DIR / f"{case_name}_geometry_preview_metadata.json"

    if write_vtk_output and vtk_path is None:
        vtk_path = CASE_DIR / f"{case_name}_geometry_preview.vtk"

    paths = {
        "config": config_path,
        "summary": summary_path,
        "xml": xml_path,
        "png": png_path,
        "vtk": vtk_path,
        "json": json_path,
    }

    create_png(geom, Path(png_path), case_name)

    if write_vtk_output and vtk_path is not None:
        write_vtk(geom, Path(vtk_path))

    metadata = build_metadata(cfg, summary, geom, parsed, paths)

    Path(json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(json_path).write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    print_report(geom, metadata, paths)

    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a debug preview of the HADR_TerrainChouldari generated "
            "dam/reservoir/breach geometry. Does not run GenCase or DualSPHysics."
        )
    )

    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to terrain_case_config.json",
    )

    parser.add_argument(
        "--summary",
        type=str,
        default=None,
        help="Path to terrain_case_summary.json",
    )

    parser.add_argument(
        "--xml",
        type=str,
        default=None,
        help="Path to generated standalone case XML",
    )

    parser.add_argument(
        "--output-png",
        type=str,
        default=None,
        help="Output PNG path",
    )

    parser.add_argument(
        "--output-vtk",
        type=str,
        default=None,
        help="Output VTK path",
    )

    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Output metadata JSON path",
    )

    parser.add_argument(
        "--no-vtk",
        action="store_true",
        help="Skip VTK output",
    )

    args = parser.parse_args()

    try:
        run_preview(
            config_path=Path(args.config),
            summary_path=Path(args.summary) if args.summary else None,
            xml_path=Path(args.xml) if args.xml else None,
            png_path=Path(args.output_png) if args.output_png else None,
            vtk_path=Path(args.output_vtk) if args.output_vtk else None,
            json_path=Path(args.output_json) if args.output_json else None,
            write_vtk_output=not args.no_vtk,
        )
        return 0

    except PreviewError as exc:
        print(f"ERROR: {exc}")
        return 1

    except gt.TerrainCaseError as exc:
        print(f"ERROR: {exc}")
        return 1

    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())