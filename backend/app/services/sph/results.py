"""Turn a DualSPHysics run directory into a browser-friendly result package.

Input:  ``<run>/<sim_id>/HADR_DamBreak_out`` as produced by SPHRunner
        (particles/PartFluid_*.vtk, HADR_DamBreak_Bound.vtk, Run.csv,
        RunPARTs.csv).

Output (written into the job's ``result/`` directory):

        manifest.json     frame index, bounds, metrics, time series
        frames.bin        per frame: float32 xyz (3*np) + float32 speed (np)
        terrain.bin       float32 xyz of the boundary/terrain particles
        flood.geojson     per-frame plan-view flood extent (only when the
                          terrain NPZ georegistration is available)
        analytics.csv     per-frame series for export

Every number is measured from the actual simulation output — nothing is
synthesised. Velocities/areas are reported in *model units* (the case is a
numerically scaled prototype), which is stated in the manifest.
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .vtk_particles import VtkParseError, read_vtk_polydata

RESULT_FORMAT = "hydrotwin-result-v1"


# ---------------------------------------------------------------------------
# small geometry helpers (no scipy dependency)
# ---------------------------------------------------------------------------

def convex_hull_ring(points: np.ndarray) -> np.ndarray:
    """Counter-clockwise closed convex hull of (N,2) points via monotone chain."""
    pts = sorted({(float(x), float(y)) for x, y in points})
    if len(pts) <= 2:
        return np.asarray(pts, dtype=np.float64).reshape(-1, 2)

    def cross(o: tuple, a: tuple, b: tuple) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    ring = lower[:-1] + upper[:-1]
    ring.append(ring[0])
    return np.asarray(ring, dtype=np.float64)


def polygon_area(ring: np.ndarray) -> float:
    """Shoelace area of a closed ring (N,2)."""
    if len(ring) < 4:
        return 0.0
    x, y = ring[:, 0], ring[:, 1]
    return float(abs(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])) * 0.5)


# ---------------------------------------------------------------------------
# solver metadata (Run.csv / RunPARTs.csv)
# ---------------------------------------------------------------------------

def _clean_number(value: str) -> float | None:
    value = value.strip().replace(",", "")
    try:
        return float(value)
    except ValueError:
        return None


def read_run_csv(sim_output: Path) -> dict[str, Any]:
    """Overall run facts from Run.csv (Np, Dp, physical time, ...)."""
    path = sim_output / "Run.csv"
    info: dict[str, Any] = {}
    if not path.exists():
        return info
    try:
        lines = [
            ln for ln in path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines() if ln.strip()
        ]
        if len(lines) < 2:
            return info
        header = lines[0].lstrip("#").split(";")
        values = lines[1].split(";")
        row = dict(zip(header, values))
        for key, out_key in (
            ("Np", "np"),
            ("Dp", "dp"),
            ("Nbound", "nbound"),
            ("Nfixed", "nfixed"),
            ("PartFiles", "part_files"),
            ("PartsOut", "parts_out"),
        ):
            if key in row:
                num = _clean_number(row[key])
                if num is not None:
                    info[out_key] = num
        for key in ("TSimul", "PhysicalTime"):
            if key in row:
                num = _clean_number(row[key])
                if num is not None:
                    info["physical_time"] = num
        if "Hardware" in row:
            info["hardware"] = row["Hardware"].strip().strip('"')
        if "Rcode-VersionInfo" in row:
            info["solver"] = row["Rcode-VersionInfo"].strip()
    except OSError:
        pass
    return info


def read_run_parts_csv(sim_output: Path) -> dict[int, float]:
    """Frame index -> simulated time [s] from RunPARTs.csv."""
    path = sim_output / "RunPARTs.csv"
    times: dict[int, float] = {}
    if not path.exists():
        return times
    try:
        lines = path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
        if not lines:
            return times
        header = [h.strip() for h in lines[0].split(";")]
        try:
            i_part = header.index("Part")
            i_time = header.index("TimeStep [s]")
        except ValueError:
            return times
        for line in lines[1:]:
            cells = line.split(";")
            if len(cells) <= max(i_part, i_time):
                continue
            part = _clean_number(cells[i_part])
            tval = _clean_number(cells[i_time])
            if part is not None and tval is not None:
                times[int(part)] = tval
    except OSError:
        pass
    return times


# ---------------------------------------------------------------------------
# georegistration (local SPH coordinates -> lon/lat)
# ---------------------------------------------------------------------------

def load_geo_transform(npz_path: Path | None) -> dict[str, Any] | None:
    """Read the terrain NPZ georegistration needed to place results on a map."""
    if npz_path is None or not npz_path.exists():
        return None
    try:
        data = np.load(npz_path)
        origin_x = float(np.asarray(data["origin_x"]).reshape(-1)[0])
        origin_y = float(np.asarray(data["origin_y"]).reshape(-1)[0])
        scale = float(np.asarray(data["scale"]).reshape(-1)[0])
        projected = str(np.asarray(data["projected_crs"]).reshape(-1)[0])
        source = str(np.asarray(data["source_crs"]).reshape(-1)[0])
        dam_lon = float(np.asarray(data["dam_lon"]).reshape(-1)[0])
        dam_lat = float(np.asarray(data["dam_lat"]).reshape(-1)[0])
    except Exception:
        return None
    if scale <= 0:
        return None
    return {
        "origin_x": origin_x,
        "origin_y": origin_y,
        "scale": scale,
        "projected_crs": projected,
        "source_crs": source,
        "dam_lon": dam_lon,
        "dam_lat": dam_lat,
    }


def local_to_lonlat(
    points: np.ndarray, geo: dict[str, Any]
) -> np.ndarray:
    """(N,2) local sim coordinates -> (N,2) lon/lat."""
    from rasterio.warp import transform

    x_m = geo["origin_x"] + points[:, 0] / geo["scale"]
    y_m = geo["origin_y"] + points[:, 1] / geo["scale"]
    lons, lats = transform(
        geo["projected_crs"], "EPSG:4326", x_m.tolist(), y_m.tolist()
    )
    return np.column_stack([lons, lats])


# ---------------------------------------------------------------------------
# processing
# ---------------------------------------------------------------------------

def _load_frame_list(sim_output: Path) -> list[Path]:
    particles = sim_output / "particles"
    if not particles.is_dir():
        raise FileNotFoundError(
            f"no particles directory in {sim_output} "
            "(expected PartFluid_*.vtk from PartVTK)"
        )
    frames = sorted(particles.glob("PartFluid_*.vtk"))
    if not frames:
        raise FileNotFoundError(f"no PartFluid_*.vtk frames in {particles}")
    return frames


def _frame_times(
    count: int,
    run_parts: dict[int, float],
    time_out: float | None,
) -> tuple[list[float], bool]:
    if len(run_parts) >= count and all(
        i in run_parts for i in range(count)
    ):
        return [run_parts[i] for i in range(count)], True
    if time_out and time_out > 0:
        return [round(i * time_out, 6) for i in range(count)], True
    return [float(i) for i in range(count)], False


def process_run(
    *,
    sim_output: Path,
    out_dir: Path,
    source: dict[str, Any],
    geo: dict[str, Any] | None = None,
    dam_summary: dict[str, Any] | None = None,
    time_out: float | None = None,
) -> dict[str, Any]:
    """Build the result package. Returns the manifest dict."""
    sim_output = Path(sim_output)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    frame_paths = _load_frame_list(sim_output)
    run_facts = read_run_csv(sim_output)
    run_parts = read_run_parts_csv(sim_output)
    times, times_known = _frame_times(len(frame_paths), run_parts, time_out)

    particle_spacing = run_facts.get("dp")
    if particle_spacing is None and time_out is None:
        particle_spacing = None

    upstream = (dam_summary or {}).get("upstream")
    dam_info = (dam_summary or {}).get("dam")

    flow_axis = "x"
    downstream_sign = 1.0
    if isinstance(upstream, dict):
        flow_axis = str(upstream.get("flow_axis") or "x")
        try:
            # upstream_sign = -1 means upstream is -x, i.e. downstream is +x.
            downstream_sign = -float(upstream.get("upstream_sign", 1.0) or 1.0)
        except (TypeError, ValueError):
            downstream_sign = 1.0
    axis_index = 1 if flow_axis == "y" else 0

    dam_center = None
    if isinstance(dam_info, dict):
        key = "center_y" if flow_axis == "y" else "center_x"
        if dam_info.get(key) is not None:
            dam_center = float(dam_info[key])

    frames_bin = out_dir / "frames.bin"
    terrain_bin = out_dir / "terrain.bin"

    counts: list[int] = []
    series_speed: list[float] = []
    series_area: list[float] = []
    series_extent: list[float] = []
    series_reach: list[float] = []

    fluid_min = np.array([math.inf, math.inf, math.inf])
    fluid_max = np.array([-math.inf, -math.inf, -math.inf])
    global_speed_max = 0.0
    peak = {"frame": 0, "speed": 0.0, "time": times[0]}

    hulls_local: list[np.ndarray] = []

    with frames_bin.open("wb") as out:
        for index, frame_path in enumerate(frame_paths):
            arrays = read_vtk_polydata(frame_path)
            pos = np.asarray(arrays["pos"], dtype=np.float32)
            if "Vel" in arrays:
                vel = np.asarray(arrays["Vel"], dtype=np.float32)
                speed = np.sqrt(
                    (vel * vel).sum(axis=1)
                ).astype(np.float32)
            else:
                speed = np.zeros(pos.shape[0], dtype=np.float32)

            counts.append(int(pos.shape[0]))
            if pos.shape[0]:
                fluid_min = np.minimum(fluid_min, pos.min(axis=0))
                fluid_max = np.maximum(fluid_max, pos.max(axis=0))
                frame_speed_max = float(speed.max())
                if frame_speed_max > global_speed_max:
                    global_speed_max = frame_speed_max
                if frame_speed_max > peak["speed"]:
                    peak = {
                        "frame": index,
                        "speed": frame_speed_max,
                        "time": times[index],
                    }
                series_speed.append(frame_speed_max)

                ring = convex_hull_ring(pos[:, :2].astype(np.float64))
                hulls_local.append(ring)
                series_area.append(polygon_area(ring))

                axis_vals = pos[:, axis_index]
                extent = float(axis_vals.max())
                series_extent.append(extent)
                if dam_center is not None:
                    series_reach.append(
                        (extent - dam_center) * downstream_sign
                    )
            else:
                series_speed.append(0.0)
                series_area.append(0.0)
                series_extent.append(
                    series_extent[-1] if series_extent else 0.0
                )
                series_reach.append(
                    series_reach[-1] if series_reach else 0.0
                )
                hulls_local.append(
                    hulls_local[-1] if hulls_local
                    else np.zeros((1, 2))
                )

            pos.tofile(out)
            speed.tofile(out)

    frames_bytes = frames_bin.stat().st_size

    # --- terrain / boundary particles -------------------------------------
    terrain_bounds: dict[str, list[float]] | None = None
    bound_path = sim_output / "HADR_DamBreak_Bound.vtk"
    if bound_path.exists():
        try:
            bound = read_vtk_polydata(bound_path)["pos"].astype(np.float32)
            bound.tofile(terrain_bin)
            terrain_bounds = {
                "min": [float(v) for v in bound.min(axis=0)],
                "max": [float(v) for v in bound.max(axis=0)],
            }
        except VtkParseError:
            terrain_bounds = None

    # --- georegistered flood extent ---------------------------------------
    geo_info: dict[str, Any] | None = None
    flood_path: str | None = None
    if geo is not None and hulls_local:
        try:
            features = []
            all_lonlat: list[np.ndarray] = []
            for index, ring in enumerate(hulls_local):
                lonlat = local_to_lonlat(ring, geo)
                all_lonlat.append(lonlat)
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "frame": index,
                            "time": times[index],
                            "area_units": series_area[index],
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [round(float(x), 7), round(float(y), 7)]
                                    for x, y in lonlat
                                ]
                            ],
                        },
                    }
                )
            merged = np.vstack(all_lonlat)
            geo_info = {
                "available": True,
                "crs": "EPSG:4326",
                "dam_lon": geo.get("dam_lon"),
                "dam_lat": geo.get("dam_lat"),
                "bounds": [
                    float(merged[:, 0].min()),
                    float(merged[:, 1].min()),
                    float(merged[:, 0].max()),
                    float(merged[:, 1].max()),
                ],
            }
            flood_path = "flood.geojson"
            (out_dir / flood_path).write_text(
                json.dumps(
                    {"type": "FeatureCollection", "features": features},
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except Exception:
            geo_info = None
            flood_path = None
    elif geo is not None:
        geo_info = {"available": False}

    # --- metrics ------------------------------------------------------------
    volume_estimate = None
    instantaneous = max(counts) if counts else 0
    if particle_spacing and instantaneous:
        # Each fluid particle represents dp^3 of volume in SPH.
        volume_estimate = {
            "value": float(instantaneous * particle_spacing ** 3),
            "dp": float(particle_spacing),
            "unit": "model units^3",
        }

    metrics: dict[str, Any] = {
        "frames": len(frame_paths),
        "duration_s": float(times[-1]) if times else 0.0,
        "time_known": times_known,
        "particles": {
            "max": max(counts) if counts else 0,
            "min": min(counts) if counts else 0,
            "sampled_total": sum(counts),
        },
        "peak_speed": {
            "value": float(peak["speed"]),
            "frame": int(peak["frame"]),
            "time": float(peak["time"]),
            "unit": "model units/s",
        },
        "wet_area": {
            "final": float(series_area[-1]) if series_area else 0.0,
            "max": float(max(series_area)) if series_area else 0.0,
            "unit": "model units^2 (convex hull of fluid particles)",
        },
        "parts_out": run_facts.get("parts_out"),
        "volume_estimate": volume_estimate,
        "units_note": (
            "Values are in numerically scaled model units — the case is a "
            "scaled SPH prototype, not a 1:1 physical model."
        ),
    }

    if series_reach and dam_center is not None:
        metrics["downstream_reach"] = {
            "max": float(max(series_reach)),
            "final": float(series_reach[-1]),
            "reference": "dam centreline",
            "unit": "model units",
            "axis": flow_axis,
        }
    if series_extent:
        metrics["flow_extent"] = {
            "min": float(min(series_extent)),
            "max": float(max(series_extent)),
            "axis": flow_axis,
            "unit": "model units",
        }

    manifest: dict[str, Any] = {
        "format": RESULT_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "files": {
            "frames": "frames.bin",
            "terrain": "terrain.bin" if terrain_bounds else None,
            "flood": flood_path,
            "analytics": "analytics.csv",
        },
        "layout": {
            "frame_bytes_per_particle": 16,
            "frame_order": "float32 xyz (3) then float32 speed (1)",
            "terrain_record": "float32 xyz",
        },
        "frames": {
            "count": len(frame_paths),
            "counts": counts,
            "times": [round(t, 6) for t in times],
            "time_known": times_known,
        },
        "bytes": {"frames": frames_bytes},
        "bounds": {
            "fluid_min": [float(v) for v in fluid_min],
            "fluid_max": [float(v) for v in fluid_max],
            "terrain": terrain_bounds,
        },
        "speed": {
            "global_max": float(global_speed_max),
            "unit": "model units/s",
        },
        "geo": geo_info,
        "dam": dam_info,
        "upstream": upstream,
        "solver": {
            "run": run_facts,
        },
        "metrics": metrics,
        "series": {
            "t": [round(t, 6) for t in times],
            "np": counts,
            "speed_max": [round(v, 6) for v in series_speed],
            "wet_area": [round(v, 6) for v in series_area],
            "extent": [round(v, 6) for v in series_extent],
            "reach": [round(v, 6) for v in series_reach] or None,
        },
    }

    # --- analytics CSV (export) --------------------------------------------
    csv_path = out_dir / "analytics.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["frame", "time_s", "particles", "speed_max", "wet_area", "extent", "reach"]
        )
        for i in range(len(frame_paths)):
            writer.writerow(
                [
                    i,
                    times[i],
                    counts[i],
                    series_speed[i],
                    series_area[i],
                    series_extent[i],
                    series_reach[i] if series_reach else "",
                ]
            )

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest
