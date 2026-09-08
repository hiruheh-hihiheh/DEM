"""
Terrain conditioning utility for Hydro Twin.

This module prepares a clipped DEM for preliminary hydrological analysis by
filling small artificial depressions/sinks that can trap D8 flow algorithms.

LIMITATIONS & NOTES:
- Copernicus GLO-30 is a Digital Surface Model (DSM), not a bare-earth DTM.
- Depression filling is preprocessing for preliminary drainage analysis only.
- It does not create a validated river network or replace observed hydrological data.
- Final flood routing will combine terrain conditioning with SPH/Delft3D stages.
- Uses a dependency-light Priority-Flood algorithm (Barnes et al., 2014) to avoid
  requiring external hydrology packages like pysheds or richdem.
"""

from __future__ import annotations

import argparse
import heapq
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio import warp

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


class ConditionError(Exception):
    """Base exception for terrain conditioning errors."""


class ConditionDEMNotFoundError(ConditionError, FileNotFoundError):
    """Raised when the DEM file does not exist."""


class ConditionInvalidRasterError(ConditionError):
    """Raised when the DEM is invalid or missing data."""


class TerrainConditioner:
    """
    Applies conservative depression filling to a DEM.
    """

    # 8-neighbor offsets
    DR = [-1, -1, -1, 0, 0, 1, 1, 1]
    DC = [-1, 0, 1, -1, 1, -1, 0, 1]

    def __init__(self, dem_path: Path | str):
        self.dem_path = Path(dem_path)
        if not self.dem_path.exists():
            raise ConditionDEMNotFoundError(
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

    def _priority_flood(
        self,
        dem_data: np.ndarray,
        mask: np.ndarray,
        max_fill_depth: float,
    ) -> np.ndarray:
        """
        Iterative Priority-Flood depression filling with a maximum fill depth constraint.
        """
        filled = dem_data.copy()
        H, W = dem_data.shape
        visited = np.zeros((H, W), dtype=bool)
        pq = []

        # 1. Seed the priority queue with all valid boundary cells
        for r in range(H):
            for c in [0, W - 1]:
                if 0 <= c < W and not mask[r, c] and not visited[r, c]:
                    heapq.heappush(pq, (filled[r, c], r, c))
                    visited[r, c] = True

        for c in range(W):
            for r in [0, H - 1]:
                if 0 <= r < H and not mask[r, c] and not visited[r, c]:
                    heapq.heappush(pq, (filled[r, c], r, c))
                    visited[r, c] = True

        # 2. Process cells in increasing order of elevation
        while pq:
            elev, r, c = heapq.heappop(pq)

            for i in range(8):
                nr, nc = r + self.DR[i], c + self.DC[i]
                
                if 0 <= nr < H and 0 <= nc < W and not visited[nr, nc]:
                    visited[nr, nc] = True
                    
                    if mask[nr, nc]:
                        continue

                    orig_elev = filled[nr, nc]
                    
                    if orig_elev < elev:
                        fill_depth = elev - orig_elev
                        if fill_depth <= max_fill_depth:
                            # Conservative fill: raise to neighbor level
                            filled[nr, nc] = elev
                            heapq.heappush(pq, (elev, nr, nc))
                        else:
                            # Deep sink: do not fill, do not propagate flood through it
                            filled[nr, nc] = orig_elev
                    else:
                        # Natural downhill or flat: keep original, propagate
                        heapq.heappush(pq, (orig_elev, nr, nc))

        return filled

    def condition(
        self,
        max_fill_depth: float = 5.0,
        out_tif: Path | str | None = None,
        out_png: Path | str | None = None,
        dam_lon: float | None = None,
        dam_lat: float | None = None,
    ) -> dict:
        """
        Runs conditioning, saves outputs, and returns statistics.
        """
        with rasterio.open(self.dem_path) as src:
            if src.crs is None:
                raise ConditionInvalidRasterError("DEM has no CRS defined.")

            dem = src.read(1, masked=True)
            mask = np.ma.getmaskarray(dem)
            dem_data = dem.data.astype(np.float32)

            if np.all(mask):
                raise ConditionInvalidRasterError("DEM contains no valid data.")

            # Run Priority-Flood
            filled_data = self._priority_flood(dem_data, mask, max_fill_depth)

            # Compute statistics
            valid = ~mask
            orig_vals = dem_data[valid]
            filled_vals = filled_data[valid]

            orig_min, orig_max, orig_mean = (
                float(np.min(orig_vals)),
                float(np.max(orig_vals)),
                float(np.mean(orig_vals)),
            )
            cond_min, cond_max, cond_mean = (
                float(np.min(filled_vals)),
                float(np.max(filled_vals)),
                float(np.mean(filled_vals)),
            )

            diff = filled_vals - orig_vals
            modified_mask = diff > 1e-6
            num_modified = int(np.sum(modified_mask))
            max_change = float(np.max(diff[modified_mask])) if num_modified > 0 else 0.0
            pct_modified = (num_modified / np.sum(valid)) * 100.0

            stats = {
                "crs": src.crs.to_string(),
                "dimensions": (src.width, src.height),
                "orig_min": orig_min,
                "orig_max": orig_max,
                "orig_mean": orig_mean,
                "cond_min": cond_min,
                "cond_max": cond_max,
                "cond_mean": cond_mean,
                "max_change": max_change,
                "num_modified": num_modified,
                "pct_modified": pct_modified,
            }

            # Save GeoTIFF
            if out_tif is not None:
                out_path = Path(out_tif)
                out_path.parent.mkdir(parents=True, exist_ok=True)

                profile = src.profile
                profile.update(dtype=rasterio.float32, nodata=src.nodata)

                with rasterio.open(out_path, "w", **profile) as dst:
                    dst.write(filled_data, 1)

            # Generate Comparison PNG
            if out_png is not None:
                out_img_path = Path(out_png)
                out_img_path.parent.mkdir(parents=True, exist_ok=True)

                is_geographic = (
                    src.crs == CRS.from_epsg(4326) or src.crs.to_epsg() == 4326
                )
                if is_geographic:
                    mean_lat = math.radians((src.bounds.bottom + src.bounds.top) / 2.0)
                    cos_lat = math.cos(mean_lat)
                    aspect = 1.0 / cos_lat if cos_lat > 0 else "equal"
                else:
                    aspect = "equal"

                extent = [
                    src.bounds.left, src.bounds.right,
                    src.bounds.bottom, src.bounds.top
                ]

                fig, axes = plt.subplots(1, 3, figsize=(18, 6))
                cmap_dem = plt.get_cmap("terrain").copy()
                cmap_dem.set_bad(color="white")

                # Original
                im0 = axes[0].imshow(
                    np.where(mask, np.nan, dem_data),
                    extent=extent, origin="upper", cmap=cmap_dem
                )
                axes[0].set_title("Original DEM")
                fig.colorbar(im0, ax=axes[0], fraction=0.046, pad=0.04)

                # Conditioned
                im1 = axes[1].imshow(
                    np.where(mask, np.nan, filled_data),
                    extent=extent, origin="upper", cmap=cmap_dem
                )
                axes[1].set_title(f"Conditioned DEM (Max Fill: {max_fill_depth}m)")
                fig.colorbar(im1, ax=axes[1], fraction=0.046, pad=0.04)

                # Difference
                diff_map = np.where(mask, np.nan, filled_data - dem_data)
                cmap_diff = plt.get_cmap("Reds")
                cmap_diff.set_bad(color="lightgray")
                im2 = axes[2].imshow(
                    diff_map,
                    extent=extent, origin="upper", cmap=cmap_diff,
                    vmin=0, vmax=max(max_change, 0.1)
                )
                axes[2].set_title(f"Fill Depth (Max Change: {max_change:.2f}m)")
                fig.colorbar(im2, ax=axes[2], fraction=0.046, pad=0.04)

                # Dam Marker & Aspect
                if dam_lon is not None and dam_lat is not None:
                    dam_x, dam_y = self._transform_coord(dam_lon, dam_lat, src.crs)
                    for ax in axes:
                        ax.plot(
                            dam_x, dam_y, marker="*", color="red",
                            markersize=15, markeredgecolor="black", zorder=5
                        )
                
                for ax in axes:
                    ax.set_aspect(aspect)
                    ax.axis("off")

                plt.suptitle("DEM Terrain Conditioning", fontsize=14)
                plt.tight_layout()
                plt.savefig(out_img_path, dpi=300, bbox_inches="tight")
                plt.close(fig)

            return stats


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply conservative depression filling to a DEM for hydrological analysis."
    )

    parser.add_argument("path", help="Path to input DEM GeoTIFF")
    parser.add_argument("--output", type=str, default=None, help="Output conditioned GeoTIFF")
    parser.add_argument("--preview", type=str, default=None, help="Output comparison PNG")
    parser.add_argument("--max-fill-depth", type=float, default=5.0, help="Maximum depression depth to fill (meters)")
    parser.add_argument("--longitude", type=float, default=None, help="Dam longitude for marker")
    parser.add_argument("--latitude", type=float, default=None, help="Dam latitude for marker")

    args = parser.parse_args()

    base_dir = Path(args.path).parent
    stem = Path(args.path).stem
    out_tif = args.output or str(base_dir / f"{stem}_conditioned.tif")
    out_png = args.preview or str(base_dir / f"{stem}_conditioned_comparison.png")

    try:
        conditioner = TerrainConditioner(args.path)
        stats = conditioner.condition(
            max_fill_depth=args.max_fill_depth,
            out_tif=out_tif,
            out_png=out_png,
            dam_lon=args.longitude,
            dam_lat=args.latitude,
        )

        print(f"Input DEM: {args.path}")
        print(f"Output DEM: {out_tif}")
        print(f"CRS: {stats['crs']}")
        print(f"Dimensions: {stats['dimensions'][0]} x {stats['dimensions'][1]}")
        print(f"Original min: {stats['orig_min']:.2f} m")
        print(f"Original max: {stats['orig_max']:.2f} m")
        print(f"Original mean: {stats['orig_mean']:.2f} m")
        print(f"Conditioned min: {stats['cond_min']:.2f} m")
        print(f"Conditioned max: {stats['cond_max']:.2f} m")
        print(f"Conditioned mean: {stats['cond_mean']:.2f} m")
        print(f"Maximum elevation change: {stats['max_change']:.2f} m")
        print(f"Modified cells: {stats['num_modified']}")
        print(f"Modified percentage: {stats['pct_modified']:.2f}%")
        
        print("\nNote: Conditioning is preprocessing for preliminary drainage analysis only.")
        print("It does not replace validated hydrological models or observed river data.")

        return 0

    except ConditionError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())