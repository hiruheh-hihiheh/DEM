"""
Flood computational domain preparation for Hydro Twin.

This module extracts a preliminary flood corridor around the downstream
drainage path from a conditioned DEM. It produces a geospatial domain mask
and elevation raster for subsequent SPH/Delft3D stages.

LIMITATIONS:
- This is a preliminary computational domain derived from DEM terrain flow.
- It is NOT the actual inundation area.
- Final flood routing requires hydrological data and SPH/Delft3D simulation.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio import warp
from rasterio.transform import xy

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D


class DomainError(Exception):
    """Base exception for domain preparation errors."""


class DomainDEMNotFoundError(DomainError, FileNotFoundError):
    """Raised when the DEM file does not exist."""


class DomainInvalidRasterError(DomainError):
    """Raised when the DEM is invalid or missing data."""


class DomainCoordinateOutsideError(DomainError):
    """Raised when the dam coordinate is outside the DEM bounds."""


class DomainPreparer:
    """
    Prepares a preliminary flood computational domain from a conditioned DEM.
    """

    DR = [-1, -1, -1, 0, 0, 1, 1, 1]
    DC = [-1, 0, 1, -1, 1, -1, 0, 1]
    DIST_FACTOR = np.array([np.sqrt(2), 1, np.sqrt(2), 1, 1, np.sqrt(2), 1, np.sqrt(2)])

    def __init__(self, dem_path: Path | str):
        self.dem_path = Path(dem_path)
        if not self.dem_path.exists():
            raise DomainDEMNotFoundError(
                f"DEM file not found: {self.dem_path}"
            )

    def _transform_coord(
        self, lon: float, lat: float, src_crs: CRS
    ) -> tuple[float, float]:
        wgs84 = CRS.from_epsg(4326)
        if src_crs == wgs84 or src_crs.to_epsg() == 4326:
            return float(lon), float(lat)
        
        xs, ys = warp.transform(wgs84, src_crs, [lon], [lat])
        return float(xs[0]), float(ys[0])

    def _trace_downstream_path(
        self, dem_data: np.ndarray, mask: np.ndarray, dam_r: int, dam_c: int
    ) -> tuple[list[int], list[int]]:
        """
        Lightweight D8 flow direction and path tracing (mirrors flow.py logic).
        """
        H, W = dem_data.shape
        flow_dir = np.full((H, W), -1, dtype=np.int8)
        max_slope = np.full((H, W), -1.0)

        padded = np.pad(dem_data, 1, mode='constant', constant_values=np.nan)
        valid = ~mask

        for i in range(8):
            r_off = self.DR[i] + 1
            c_off = self.DC[i] + 1
            neighbor = padded[r_off:r_off + H, c_off:c_off + W]

            slope = (dem_data - neighbor) / self.DIST_FACTOR[i]
            better = (slope > max_slope) & valid & (~np.isnan(neighbor))
            max_slope[better] = slope[better]
            flow_dir[better] = i

        path_r = [dam_r]
        path_c = [dam_c]
        visited = {(dam_r, dam_c)}
        curr_r, curr_c = dam_r, dam_c

        while True:
            d = flow_dir[curr_r, curr_c]
            if d < 0:
                break
            nr, nc = curr_r + self.DR[d], curr_c + self.DC[d]
            if not (0 <= nr < H and 0 <= nc < W):
                break
            if (nr, nc) in visited:
                break
            visited.add((nr, nc))
            path_r.append(nr)
            path_c.append(nc)
            curr_r, curr_c = nr, nc

        return path_r, path_c

    def _build_corridor_mask(
        self, path_r: list[int], path_c: list[int], H: int, W: int, radius_px: int
    ) -> np.ndarray:
        """
        Creates a binary corridor mask by buffering the downstream path.
        """
        mask = np.zeros((H, W), dtype=np.uint8)
        
        if radius_px <= 0:
            for r, c in zip(path_r, path_c):
                if 0 <= r < H and 0 <= c < W:
                    mask[r, c] = 1
            return mask

        y, x = np.ogrid[-radius_px:radius_px + 1, -radius_px:radius_px + 1]
        circle = (x * x + y * y) <= radius_px * radius_px

        for r, c in zip(path_r, path_c):
            r_min = max(0, r - radius_px)
            r_max = min(H, r + radius_px + 1)
            c_min = max(0, c - radius_px)
            c_max = min(W, c + radius_px + 1)

            kr_min = r_min - (r - radius_px)
            kr_max = circle.shape[0] - ((r + radius_px + 1) - r_max)
            kc_min = c_min - (c - radius_px)
            kc_max = circle.shape[1] - ((c + radius_px + 1) - c_max)

            mask[r_min:r_max, c_min:c_max] |= circle[kr_min:kr_max, kc_min:kc_max]

        return mask

    def prepare(
        self,
        longitude: float,
        latitude: float,
        corridor_width_km: float = 1.0,
        out_tif: Path | str | None = None,
        out_png: Path | str | None = None,
    ) -> dict:
        """
        Runs domain preparation, saves outputs, and returns statistics.
        """
        with rasterio.open(self.dem_path) as src:
            if src.crs is None:
                raise DomainInvalidRasterError("DEM has no CRS defined.")

            dem = src.read(1, masked=True)
            mask = np.ma.getmaskarray(dem)
            dem_data = dem.data.astype(np.float32)

            if np.all(mask):
                raise DomainInvalidRasterError("DEM contains no valid data.")

            dam_x, dam_y = self._transform_coord(longitude, latitude, src.crs)
            minx, miny, maxx, maxy = src.bounds

            if not (minx <= dam_x <= maxx and miny <= dam_y <= maxy):
                raise DomainCoordinateOutsideError(
                    f"Dam coordinate ({dam_x}, {dam_y}) is outside DEM bounds."
                )

            dam_r, dam_c = src.index(dam_x, dam_y)
            dam_r = max(0, min(int(dam_r), src.height - 1))
            dam_c = max(0, min(int(dam_c), src.width - 1))

            if mask[dam_r, dam_c]:
                raise DomainInvalidRasterError("Dam coordinate falls on a nodata pixel.")

            # Trace D8 downstream path
            path_r, path_c = self._trace_downstream_path(dem_data, mask, dam_r, dam_c)

            # Distance calculation
            mean_lat = math.radians((miny + maxy) / 2.0)
            cos_lat = math.cos(mean_lat)
            dy_km = abs(src.res[1]) * 111.32
            dx_km = abs(src.res[0]) * 111.32 * cos_lat

            total_dist_km = 0.0
            for i in range(1, len(path_r)):
                dr = path_r[i] - path_r[i - 1]
                dc = path_c[i] - path_c[i - 1]
                total_dist_km += math.sqrt((dr * dy_km) ** 2 + (dc * dx_km) ** 2)

            # Corridor buffer calculation
            res_x_m = abs(src.res[0]) * 111320 * cos_lat
            res_y_m = abs(src.res[1]) * 110540
            avg_res_m = (res_x_m + res_y_m) / 2.0
            radius_m = (corridor_width_km * 1000.0) / 2.0
            radius_px = max(1, int(round(radius_m / avg_res_m)))

            corridor_mask = self._build_corridor_mask(
                path_r, path_c, src.height, src.width, radius_px
            )

            domain_cells = int(np.sum(corridor_mask))
            valid_cells = int(np.sum(~mask))
            domain_pct = (domain_cells / valid_cells) * 100.0 if valid_cells > 0 else 0.0

            # Save GeoTIFF (Band 1: Mask, Band 2: Elevation)
            if out_tif is not None:
                out_path = Path(out_tif)
                out_path.parent.mkdir(parents=True, exist_ok=True)

                profile = src.profile
                profile.update(count=2, dtype=rasterio.float32, nodata=-9999.0)

                elev_band = np.where(corridor_mask == 1, dem_data, -9999.0).astype(np.float32)
                mask_band = corridor_mask.astype(np.float32)

                with rasterio.open(out_path, "w", **profile) as dst:
                    dst.write(mask_band, 1)
                    dst.write(elev_band, 2)

            # Save Preview PNG
            if out_png is not None:
                out_img = Path(out_png)
                out_img.parent.mkdir(parents=True, exist_ok=True)

                is_geographic = (src.crs == CRS.from_epsg(4326) or src.crs.to_epsg() == 4326)
                aspect = 1.0 / cos_lat if is_geographic and cos_lat > 0 else "equal"
                extent = [minx, maxx, miny, maxy]

                fig, ax = plt.subplots(figsize=(10, 8))

                cmap_dem = plt.get_cmap("terrain").copy()
                cmap_dem.set_bad(color="white")
                ax.imshow(
                    np.where(mask, np.nan, dem_data),
                    extent=extent, origin="upper", cmap=cmap_dem, alpha=0.6
                )

                corridor_plot = np.where(corridor_mask == 1, 1, np.nan)
                cmap_corr = plt.get_cmap("Blues")
                ax.imshow(
                    corridor_plot,
                    extent=extent, origin="upper", cmap=cmap_corr, alpha=0.5
                )

                path_x, path_y = xy(src.transform, path_r, path_c)
                ax.plot(path_x, path_y, color='red', linewidth=2)
                ax.plot(dam_x, dam_y, marker='*', color='red', markersize=15, markeredgecolor='black')

                legend_elements = [
                    Patch(facecolor='tab:blue', alpha=0.5, label=f'Flood Corridor ({corridor_width_km} km)'),
                    Line2D([0], [0], color='red', linewidth=2, label='D8 Downstream Path'),
                    Line2D([0], [0], marker='*', color='w', markerfacecolor='red', markeredgecolor='black', markersize=12, label='Chouldari Dam')
                ]
                ax.legend(handles=legend_elements, loc='upper right')

                ax.set_title("Preliminary Flood Computational Domain")
                if is_geographic:
                    ax.set_xlabel("Longitude")
                    ax.set_ylabel("Latitude")
                else:
                    ax.set_xlabel("Easting")
                    ax.set_ylabel("Northing")
                    
                ax.set_aspect(aspect)
                plt.tight_layout()
                plt.savefig(out_img, dpi=300, bbox_inches="tight")
                plt.close(fig)

            return {
                "crs": src.crs.to_string(),
                "dam_r": dam_r,
                "dam_c": dam_c,
                "path_len": len(path_r),
                "dist_km": total_dist_km,
                "corridor_km": corridor_width_km,
                "domain_cells": domain_cells,
                "domain_pct": domain_pct,
            }


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a preliminary flood computational domain from a conditioned DEM."
    )

    parser.add_argument("path", help="Path to conditioned DEM GeoTIFF")
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--corridor-width-km", type=float, default=1.0)
    parser.add_argument("--output", type=str, default=None, help="Output domain GeoTIFF")
    parser.add_argument("--preview", type=str, default=None, help="Output preview PNG")

    args = parser.parse_args()

    base_dir = Path(args.path).parent
    stem = Path(args.path).stem.replace("_conditioned", "")
    
    out_tif = args.output or str(base_dir / f"{stem}_flood_domain.tif")
    out_png = args.preview or str(base_dir / f"{stem}_flood_domain_preview.png")

    try:
        preparer = DomainPreparer(args.path)
        res = preparer.prepare(
            longitude=args.longitude,
            latitude=args.latitude,
            corridor_width_km=args.corridor_width_km,
            out_tif=out_tif,
            out_png=out_png,
        )

        print(f"DEM: {args.path}")
        print(f"CRS: {res['crs']}")
        print(f"Dam pixel: row={res['dam_r']}, col={res['dam_c']}")
        print(f"Downstream path length: {res['path_len']} pixels")
        print(f"Downstream distance: {res['dist_km']:.3f} km")
        print(f"Corridor width: {res['corridor_km']:.2f} km")
        print(f"Domain cells: {res['domain_cells']}")
        print(f"Domain percentage: {res['domain_pct']:.2f}%")

        print("\nNote: This is a preliminary computational domain derived from DEM terrain flow.")
        print("It is NOT the actual inundation area. Final flood routing requires SPH/Delft3D.")

        return 0

    except DomainError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())