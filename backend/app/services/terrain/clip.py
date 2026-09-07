"""
Reusable terrain clipping utility for Hydro Twin.

This module extracts a local terrain subset (window) around a specific
dam coordinate from a larger DEM GeoTIFF.

The resulting clipped DEM will later become the terrain input for the
SPH flood-domain preparation stage, where it will be converted into
DualSPHysics-compatible boundary geometry.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio import warp
from rasterio.windows import Window, from_bounds


class TerrainClipError(Exception):
    """Base exception for terrain clipping errors."""


class TerrainDEMNotFoundError(TerrainClipError, FileNotFoundError):
    """Raised when the source DEM file does not exist."""


class TerrainInvalidDimensionsError(TerrainClipError):
    """Raised when the requested clip dimensions are invalid."""


class TerrainCoordinateOutsideError(TerrainClipError):
    """Raised when the requested center coordinate is outside the DEM."""


class TerrainClippingFailureError(TerrainClipError):
    """Raised when the clipping operation fails."""


@dataclass(frozen=True)
class ClippedTerrainInfo:
    """
    Metadata describing a clipped terrain raster.
    """

    path: str | None
    crs: str | None
    epsg: int | None
    width: int
    height: int
    bounds: tuple[float, float, float, float]
    resolution: tuple[float, float]
    minimum: float
    maximum: float
    mean: float
    dam_row: int
    dam_col: int


def _get_local_utm_epsg(lon: float, lat: float) -> int:
    """
    Estimate the local UTM EPSG code for a given lon/lat.
    This allows us to accurately buffer distances in meters before
    transforming back to the source CRS.
    """
    zone = int(math.floor((lon + 180) / 6) + 1)
    zone = max(1, min(60, zone))
    if lat >= 0:
        return 32600 + zone
    return 32700 + zone


class TerrainClipper:
    """
    Extracts a local terrain window from a DEM.
    """

    def __init__(self, dem_path: Path | str):
        self.dem_path = Path(dem_path)
        if not self.dem_path.exists():
            raise TerrainDEMNotFoundError(
                f"DEM file not found: {self.dem_path}"
            )

    def clip(
        self,
        longitude: float,
        latitude: float,
        width_km: float = 5.0,
        height_km: float = 5.0,
        output_path: Path | str | None = None,
    ) -> ClippedTerrainInfo:
        """
        Clip the DEM to a window centered on the given coordinate.
        """
        if width_km <= 0 or height_km <= 0:
            raise TerrainInvalidDimensionsError(
                "Width and height must be strictly positive."
            )

        with rasterio.open(self.dem_path) as src:
            if src.crs is None:
                raise TerrainClippingFailureError(
                    f"DEM has no CRS defined: {self.dem_path}"
                )

            wgs84 = CRS.from_epsg(4326)
            
            # 1. Transform center coordinate to source CRS
            try:
                if src.crs == wgs84 or src.crs.to_epsg() == 4326:
                    cx, cy = float(longitude), float(latitude)
                else:
                    xs, ys = warp.transform(
                        wgs84, src.crs, [longitude], [latitude]
                    )
                    cx, cy = float(xs[0]), float(ys[0])
            except Exception as exc:
                raise TerrainCoordinateOutsideError(
                    f"Failed to transform coordinate ({longitude}, {latitude}): {exc}"
                ) from exc

            # 2. Check if center is inside source bounds
            minx, miny, maxx, maxy = src.bounds
            if not (minx <= cx <= maxx and miny <= cy <= maxy):
                raise TerrainCoordinateOutsideError(
                    f"Center coordinate ({cx}, {cy}) in CRS {src.crs} "
                    f"is outside DEM bounds ({minx}, {miny}, {maxx}, {maxy})."
                )

            # 3. Calculate window bounds in meters using local UTM
            # This handles the curvature of the earth correctly when 
            # the source DEM is in degrees (EPSG:4326).
            utm_epsg = _get_local_utm_epsg(longitude, latitude)
            utm_crs = CRS.from_epsg(utm_epsg)

            cx_utm, cy_utm = warp.transform(
                wgs84, utm_crs, [longitude], [latitude]
            )
            cx_utm, cy_utm = float(cx_utm[0]), float(cy_utm[0])

            half_w = (width_km * 1000.0) / 2.0
            half_h = (height_km * 1000.0) / 2.0

            corners_x_utm = [
                cx_utm - half_w,
                cx_utm + half_w,
                cx_utm + half_w,
                cx_utm - half_w,
            ]
            corners_y_utm = [
                cy_utm - half_h,
                cy_utm - half_h,
                cy_utm + half_h,
                cy_utm + half_h,
            ]

            # Transform corners back to source CRS
            corners_x_src, corners_y_src = warp.transform(
                utm_crs, src.crs, corners_x_utm, corners_y_utm
            )

            src_minx = min(corners_x_src)
            src_maxx = max(corners_x_src)
            src_miny = min(corners_y_src)
            src_maxy = max(corners_y_src)

            # 4. Intersect with DEM bounds to avoid reading outside the raster
            src_minx = max(src_minx, minx)
            src_maxx = min(src_maxx, maxx)
            src_miny = max(src_miny, miny)
            src_maxy = min(src_maxy, maxy)

            if src_minx >= src_maxx or src_miny >= src_maxy:
                raise TerrainClippingFailureError(
                    "Clipping window is completely outside DEM bounds."
                )

            # 5. Create and round window to exact pixel boundaries
            window = from_bounds(
                src_minx, src_miny, src_maxx, src_maxy, src.transform
            )
            window = window.round_lengths()
            window = window.round_offsets()
            window = window.intersection(Window(0, 0, src.width, src.height))

            if window.width == 0 or window.height == 0:
                raise TerrainClippingFailureError(
                    "Resulting clipped window has zero area."
                )

            # 6. Read data
            data = src.read(window=window)
            win_transform = src.window_transform(window)

            # 7. Calculate dam pixel in the CLIPPED raster
            col, row = ~win_transform * (cx, cy)
            dam_col = max(0, min(int(round(col)), int(window.width) - 1))
            dam_row = max(0, min(int(round(row)), int(window.height) - 1))

            # 8. Calculate statistics ignoring nodata
            nodata = src.nodata
            if nodata is not None:
                masked_data = np.ma.masked_equal(data, nodata)
            else:
                masked_data = np.ma.masked_invalid(data)

            valid_data = masked_data.compressed()
            if valid_data.size == 0:
                min_elev = max_elev = mean_elev = float("nan")
            else:
                min_elev = float(valid_data.min())
                max_elev = float(valid_data.max())
                mean_elev = float(valid_data.mean())

            # 9. Save if requested
            out_path_str = None
            if output_path is not None:
                out_path = Path(output_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path_str = str(out_path)

                profile = src.profile
                profile.update(
                    {
                        "height": int(window.height),
                        "width": int(window.width),
                        "transform": win_transform,
                    }
                )

                with rasterio.open(out_path, "w", **profile) as dst:
                    dst.write(data)

            # 10. Get exact window bounds
            win_bounds = rasterio.windows.bounds(window, src.transform)

            return ClippedTerrainInfo(
                path=out_path_str,
                crs=src.crs.to_string() if src.crs else None,
                epsg=src.crs.to_epsg() if src.crs else None,
                width=int(window.width),
                height=int(window.height),
                bounds=(
                    float(win_bounds[0]),
                    float(win_bounds[1]),
                    float(win_bounds[2]),
                    float(win_bounds[3]),
                ),
                resolution=(
                    abs(float(win_transform.a)),
                    abs(float(win_transform.e)),
                ),
                minimum=min_elev,
                maximum=max_elev,
                mean=mean_elev,
                dam_row=dam_row,
                dam_col=dam_col,
            )


def _format_float(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    return f"{value:.4f}"


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Clip a DEM GeoTIFF to a local window around a coordinate."
    )

    parser.add_argument("path", help="Path to the source DEM GeoTIFF")
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--width-km", type=float, default=5.0)
    parser.add_argument("--height-km", type=float, default=5.0)
    parser.add_argument("--output", type=str, default=None)

    args = parser.parse_args()

    try:
        clipper = TerrainClipper(args.path)
        info = clipper.clip(
            longitude=args.longitude,
            latitude=args.latitude,
            width_km=args.width_km,
            height_km=args.height_km,
            output_path=args.output,
        )

        print("Source DEM:", args.path)
        print("Clipped DEM:", info.path or "(in memory)")
        print("CRS:", info.crs)
        print("EPSG:", info.epsg)
        print("Width:", info.width)
        print("Height:", info.height)
        print("Bounds:", info.bounds)
        print("Resolution:", f"{info.resolution[0]:.8f}, {info.resolution[1]:.8f}")
        print("Minimum elevation:", _format_float(info.minimum))
        print("Maximum elevation:", _format_float(info.maximum))
        print("Mean elevation:", _format_float(info.mean))
        print("Dam pixel:", f"row={info.dam_row}, col={info.dam_col}")

        return 0

    except TerrainClipError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())