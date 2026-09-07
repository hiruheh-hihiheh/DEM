"""
Reusable terrain preview and analysis utility for Hydro Twin.

This module visually inspects a clipped DEM and prints basic terrain statistics
before converting the terrain into DualSPHysics-compatible boundary geometry.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio import warp

# Use a non-interactive backend for headless/server environments
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


class TerrainPreviewError(Exception):
    """Base exception for terrain preview errors."""


class TerrainDEMNotFoundError(TerrainPreviewError, FileNotFoundError):
    """Raised when the DEM file does not exist."""


class TerrainInvalidRasterError(TerrainPreviewError):
    """Raised when the DEM is invalid or missing CRS/data."""


class TerrainCoordinateOutsideError(TerrainPreviewError):
    """Raised when the requested coordinate is outside the DEM bounds."""


@dataclass(frozen=True)
class TerrainStats:
    """
    Basic statistics and metadata for a clipped DEM.
    """

    width: int
    height: int
    crs: str
    bounds: tuple[float, float, float, float]
    resolution: tuple[float, float]
    min_elev: float
    max_elev: float
    mean_elev: float
    median_elev: float
    dam_row: int
    dam_col: int
    dam_x: float
    dam_y: float


class TerrainPreviewer:
    """
    Analyzes and plots a clipped DEM raster.
    """

    def __init__(self, dem_path: Path | str):
        self.dem_path = Path(dem_path)
        if not self.dem_path.exists():
            raise TerrainDEMNotFoundError(
                f"DEM file not found: {self.dem_path}"
            )

    def analyze_and_plot(
        self,
        longitude: float,
        latitude: float,
        output_path: Path | str | None = None,
        dam_name: str = "Dam Location",
        title: str = "Terrain Preview",
    ) -> TerrainStats:
        """
        Reads the DEM, computes statistics, and optionally saves a preview PNG.
        """
        with rasterio.open(self.dem_path) as src:
            if src.crs is None:
                raise TerrainInvalidRasterError(
                    f"DEM has no CRS defined: {self.dem_path}"
                )

            # Read and mask data
            try:
                data = src.read(1, masked=True)
            except Exception as exc:
                raise TerrainInvalidRasterError(
                    f"Unable to read first band from DEM: {self.dem_path}"
                ) from exc

            data = np.ma.masked_invalid(data)

            if data.count() == 0:
                raise TerrainInvalidRasterError(
                    "DEM contains no valid elevation data."
                )

            # Transform dam coordinate to DEM CRS
            wgs84 = CRS.from_epsg(4326)
            is_geographic = (src.crs == wgs84 or src.crs.to_epsg() == 4326)

            if is_geographic:
                dam_x, dam_y = float(longitude), float(latitude)
            else:
                try:
                    xs, ys = warp.transform(
                        wgs84, src.crs, [longitude], [latitude]
                    )
                    dam_x, dam_y = float(xs[0]), float(ys[0])
                except Exception as exc:
                    raise TerrainCoordinateOutsideError(
                        f"Failed to transform coordinate ({longitude}, {latitude}): {exc}"
                    ) from exc

            # Check bounds
            minx, miny, maxx, maxy = src.bounds
            if not (minx <= dam_x <= maxx and miny <= dam_y <= maxy):
                raise TerrainCoordinateOutsideError(
                    f"Dam coordinate ({dam_x}, {dam_y}) in CRS {src.crs} "
                    f"is outside DEM bounds ({minx}, {miny}, {maxx}, {maxy})."
                )

            # Get dam pixel
            row, col = src.index(dam_x, dam_y)
            dam_row = max(0, min(int(row), src.height - 1))
            dam_col = max(0, min(int(col), src.width - 1))

            # Compute statistics
            valid_data = data.compressed()
            stats = TerrainStats(
                width=src.width,
                height=src.height,
                crs=src.crs.to_string(),
                bounds=(
                    float(minx),
                    float(miny),
                    float(maxx),
                    float(maxy),
                ),
                resolution=(abs(float(src.res[0])), abs(float(src.res[1]))),
                min_elev=float(valid_data.min()),
                max_elev=float(valid_data.max()),
                mean_elev=float(valid_data.mean()),
                median_elev=float(np.median(valid_data)),
                dam_row=dam_row,
                dam_col=dam_col,
                dam_x=dam_x,
                dam_y=dam_y,
            )

            # Plotting
            if output_path is not None:
                out_p = Path(output_path)
                out_p.parent.mkdir(parents=True, exist_ok=True)

                fig, ax = plt.subplots(figsize=(10, 8))

                # Extent for imshow: [left, right, bottom, top]
                extent = [minx, maxx, miny, maxy]

                # Colormap setup
                cmap = plt.get_cmap("terrain").copy()
                cmap.set_bad(color="white")

                im = ax.imshow(
                    data.filled(np.nan),
                    extent=extent,
                    origin="upper",
                    cmap=cmap,
                    interpolation="nearest",
                )

                # Preserve correct geographic aspect ratio
                if is_geographic:
                    mean_lat = math.radians((miny + maxy) / 2.0)
                    cos_lat = math.cos(mean_lat)
                    if cos_lat > 0:
                        # Adjust aspect ratio to prevent lat/lon stretching
                        ax.set_aspect(1.0 / cos_lat)
                else:
                    ax.set_aspect("equal")

                # Mark dam location
                ax.plot(
                    dam_x,
                    dam_y,
                    marker="*",
                    color="red",
                    markersize=15,
                    markeredgecolor="black",
                    markeredgewidth=1.5,
                    label=dam_name,
                    zorder=5,
                )

                # Labels and Title
                if is_geographic:
                    ax.set_xlabel("Longitude")
                    ax.set_ylabel("Latitude")
                else:
                    ax.set_xlabel("Easting (X)")
                    ax.set_ylabel("Northing (Y)")

                ax.set_title(title)
                ax.legend(loc="upper right")

                # Colorbar
                cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                cbar.set_label("Elevation (m)")

                plt.tight_layout()
                plt.savefig(out_p, dpi=300, bbox_inches="tight")
                plt.close(fig)

            return stats


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Visually inspect and analyze a clipped DEM GeoTIFF."
    )

    parser.add_argument("path", help="Path to the clipped DEM GeoTIFF")
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument(
        "--dam-name",
        type=str,
        default="Dam Location",
        help="Label for the dam marker on the plot",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="Terrain Preview",
        help="Title for the preview plot",
    )

    args = parser.parse_args()

    try:
        previewer = TerrainPreviewer(args.path)
        stats = previewer.analyze_and_plot(
            longitude=args.longitude,
            latitude=args.latitude,
            output_path=args.output,
            dam_name=args.dam_name,
            title=args.title,
        )

        print(f"Width: {stats.width}")
        print(f"Height: {stats.height}")
        print(f"CRS: {stats.crs}")
        print(f"Bounds: {stats.bounds}")
        print(
            f"Resolution: {stats.resolution[0]:.8f}, {stats.resolution[1]:.8f}"
        )
        print(f"Minimum elevation: {stats.min_elev:.4f}")
        print(f"Maximum elevation: {stats.max_elev:.4f}")
        print(f"Mean elevation: {stats.mean_elev:.4f}")
        print(f"Median elevation: {stats.median_elev:.4f}")
        print(f"Dam pixel: row={stats.dam_row}, col={stats.dam_col}")
        print(
            f"Dam coordinate (in CRS): X={stats.dam_x:.6f}, Y={stats.dam_y:.6f}"
        )

        if args.output:
            print(f"Preview saved to: {args.output}")

        return 0

    except TerrainPreviewError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())