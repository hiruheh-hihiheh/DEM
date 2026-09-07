"""
Preliminary terrain-flow analysis utility for Hydro Twin.

This module performs a basic D8 flow-direction and flow-accumulation analysis
on a clipped DEM to estimate natural downhill drainage and identify a preliminary
downstream flood corridor.

LIMITATIONS:
- Copernicus GLO-30 is a Digital Surface Model (DSM), meaning it includes 
  vegetation, buildings, and the dam structure itself, rather than bare earth.
- This is a preliminary terrain-based D8 drainage analysis.
- It is not a validated hydrological model and does not account for subsurface 
  flow, channel routing, or observed river data.
- Final flood routing will combine terrain/hydrological information with the 
  SPH/Delft3D stages.
"""

import argparse
import math
from pathlib import Path
from collections import deque

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio import warp
from rasterio.transform import xy

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


class FlowAnalysisError(Exception):
    """Base exception for flow analysis errors."""


class FlowDEMNotFoundError(FlowAnalysisError, FileNotFoundError):
    """Raised when the DEM file does not exist."""


class FlowInvalidRasterError(FlowAnalysisError):
    """Raised when the DEM is invalid or missing CRS/data."""


class FlowCoordinateOutsideError(FlowAnalysisError):
    """Raised when the requested coordinate is outside the DEM bounds."""


class FlowAnalyzer:
    """
    Analyzes D8 flow direction and accumulation on a clipped DEM.
    """

    # D8 neighbor offsets and distance factors
    DR = np.array([-1, -1, -1, 0, 0, 1, 1, 1])
    DC = np.array([-1, 0, 1, -1, 1, -1, 0, 1])
    DIST_FACTOR = np.array([
        np.sqrt(2), 1, np.sqrt(2), 
        1, 1, 
        np.sqrt(2), 1, np.sqrt(2)
    ])

    def __init__(self, dem_path: Path | str):
        self.dem_path = Path(dem_path)
        if not self.dem_path.exists():
            raise FlowDEMNotFoundError(
                f"DEM file not found: {self.dem_path}"
            )

    def analyze(
        self,
        longitude: float,
        latitude: float,
        out_img: Path | str | None = None,
        out_acc_tif: Path | str | None = None,
    ) -> dict:
        """
        Computes D8 flow direction, accumulation, and traces the downstream path.
        """
        with rasterio.open(self.dem_path) as src:
            if src.crs is None:
                raise FlowInvalidRasterError(
                    f"DEM has no CRS defined: {self.dem_path}"
                )

            try:
                dem = src.read(1, masked=True)
            except Exception as exc:
                raise FlowInvalidRasterError(
                    f"Unable to read first band from DEM: {self.dem_path}"
                ) from exc

            dem = np.ma.masked_invalid(dem)
            valid_mask = ~dem.mask
            H, W = dem.shape

            if dem.count() == 0:
                raise FlowInvalidRasterError(
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
                    raise FlowCoordinateOutsideError(
                        f"Failed to transform coordinate ({longitude}, {latitude}): {exc}"
                    ) from exc

            # Check bounds
            minx, miny, maxx, maxy = src.bounds
            if not (minx <= dam_x <= maxx and miny <= dam_y <= maxy):
                raise FlowCoordinateOutsideError(
                    f"Dam coordinate ({dam_x}, {dam_y}) in CRS {src.crs} "
                    f"is outside DEM bounds ({minx}, {miny}, {maxx}, {maxy})."
                )

            dam_r, dam_c = src.index(dam_x, dam_y)
            dam_r = max(0, min(int(dam_r), H - 1))
            dam_c = max(0, min(int(dam_c), W - 1))

            if not valid_mask[dam_r, dam_c]:
                raise FlowInvalidRasterError(
                    "Dam coordinate falls on a nodata/invalid pixel."
                )

            # 1. Compute D8 Flow Direction
            flow_dir = np.full((H, W), -1, dtype=np.int8)
            max_slope = np.full((H, W), -1.0)

            padded = np.pad(dem.filled(np.nan), 1, mode='constant', constant_values=np.nan)

            for i in range(8):
                r_off = self.DR[i] + 1
                c_off = self.DC[i] + 1
                neighbor = padded[r_off:r_off + H, c_off:c_off + W]

                slope = (dem.filled(np.nan) - neighbor) / self.DIST_FACTOR[i]
                
                better = (slope > max_slope) & valid_mask & ~np.isnan(neighbor)
                max_slope[better] = slope[better]
                flow_dir[better] = i

            # 2. Compute Flow Accumulation (Topological Sort / Kahn's Algorithm)
            in_degree = np.zeros((H, W), dtype=np.int32)
            
            r_idx, c_idx = np.where(flow_dir >= 0)
            dirs = flow_dir[r_idx, c_idx]
            target_r = r_idx + self.DR[dirs]
            target_c = c_idx + self.DC[dirs]

            valid_targets = (target_r >= 0) & (target_r < H) & (target_c >= 0) & (target_c < W)
            np.add.at(in_degree, (target_r[valid_targets], target_c[valid_targets]), 1)

            accum = np.zeros((H, W), dtype=np.int32)
            accum[valid_mask] = 1

            queue = deque()
            start_r, start_c = np.where((in_degree == 0) & valid_mask)
            for r, c in zip(start_r, start_c):
                queue.append((r, c))

            while queue:
                r, c = queue.popleft()
                d = flow_dir[r, c]
                if d >= 0:
                    nr = r + self.DR[d]
                    nc = c + self.DC[d]
                    if 0 <= nr < H and 0 <= nc < W:
                        accum[nr, nc] += accum[r, c]
                        in_degree[nr, nc] -= 1
                        if in_degree[nr, nc] == 0:
                            queue.append((nr, nc))

            # 3. Trace Downstream Path
            path_r = [dam_r]
            path_c = [dam_c]
            visited = {(dam_r, dam_c)}

            curr_r, curr_c = dam_r, dam_c
            while True:
                d = flow_dir[curr_r, curr_c]
                if d < 0:
                    break
                nr = curr_r + self.DR[d]
                nc = curr_c + self.DC[d]
                if not (0 <= nr < H and 0 <= nc < W):
                    break
                if (nr, nc) in visited:
                    break
                visited.add((nr, nc))
                path_r.append(nr)
                path_c.append(nc)
                curr_r, curr_c = nr, nc

            first_r, first_c = (path_r[1], path_c[1]) if len(path_r) > 1 else (None, None)
            end_elev = float(dem[path_r[-1], path_c[-1]])
            max_acc = int(np.max(accum[path_r, path_c]))

            # 4. Calculate Distance
            mean_lat = (src.bounds.bottom + src.bounds.top) / 2.0
            mean_lat_rad = math.radians(mean_lat)
            cos_lat = math.cos(mean_lat_rad)

            dy_km = abs(src.res[1]) * 111.32
            dx_km = abs(src.res[0]) * 111.32 * cos_lat

            total_dist_km = 0.0
            for i in range(1, len(path_r)):
                d_r = path_r[i] - path_r[i - 1]
                d_c = path_c[i] - path_c[i - 1]
                dist = math.sqrt((d_r * dy_km) ** 2 + (d_c * dx_km) ** 2)
                total_dist_km += dist

            # 5. Save Flow Accumulation GeoTIFF
            if out_acc_tif is not None:
                out_tif_path = Path(out_acc_tif)
                out_tif_path.parent.mkdir(parents=True, exist_ok=True)
                
                profile = src.profile
                profile.update(dtype=rasterio.int32, count=1, nodata=-1)
                accum_out = accum.copy()
                accum_out[~valid_mask] = -1

                with rasterio.open(out_tif_path, 'w', **profile) as dst:
                    dst.write(accum_out, 1)

            # 6. Generate Visualization
            if out_img is not None:
                out_img_path = Path(out_img)
                out_img_path.parent.mkdir(parents=True, exist_ok=True)

                fig, ax = plt.subplots(figsize=(10, 8))
                extent = [src.bounds.left, src.bounds.right, src.bounds.bottom, src.bounds.top]

                cmap_dem = plt.get_cmap("terrain").copy()
                cmap_dem.set_bad(color="white")
                ax.imshow(dem.filled(np.nan), extent=extent, origin="upper", cmap=cmap_dem, alpha=0.8)

                # Flow accumulation overlay (log scale for streams)
                acc_plot = np.ma.masked_where(accum <= 10, accum)
                cmap_acc = plt.get_cmap("Blues")
                ax.imshow(np.log1p(acc_plot.filled(0)), extent=extent, origin="upper", cmap=cmap_acc, alpha=0.7)

                path_x, path_y = xy(src.transform, path_r, path_c)
                ax.plot(path_x, path_y, color='red', linewidth=2, label='Downstream Path')
                ax.plot(dam_x, dam_y, marker='*', color='red', markersize=15, markeredgecolor='black', label='Dam')

                if is_geographic:
                    ax.set_aspect(1.0 / cos_lat)
                else:
                    ax.set_aspect('equal')

                ax.set_title("Preliminary DEM Flow Analysis")
                ax.legend(loc="upper right")
                plt.tight_layout()
                plt.savefig(out_img_path, dpi=300, bbox_inches="tight")
                plt.close(fig)

            return {
                "crs": src.crs.to_string(),
                "dam_r": dam_r,
                "dam_c": dam_c,
                "dam_elev": float(dem[dam_r, dam_c]),
                "first_r": first_r,
                "first_c": first_c,
                "path_len_px": len(path_r),
                "dist_km": total_dist_km,
                "end_elev": end_elev,
                "max_acc": max_acc,
            }


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze preliminary D8 terrain flow and downstream path."
    )

    parser.add_argument("path", help="Path to the clipped DEM GeoTIFF")
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--output", type=str, default=None, help="Output PNG path")
    parser.add_argument("--output-tif", type=str, default=None, help="Output Flow Accumulation GeoTIFF path")

    args = parser.parse_args()

    # Default output paths if not provided
    base_dir = Path(args.path).parent
    stem = Path(args.path).stem
    out_img = args.output or str(base_dir / f"{stem}_flow_preview.png")
    out_acc_tif = args.output_tif or str(base_dir / f"{stem}_flow_accumulation.tif")

    try:
        analyzer = FlowAnalyzer(args.path)
        res = analyzer.analyze(
            longitude=args.longitude,
            latitude=args.latitude,
            out_img=out_img,
            out_acc_tif=out_acc_tif,
        )

        print(f"DEM: {args.path}")
        print(f"CRS: {res['crs']}")
        print(f"Dam pixel: row={res['dam_r']}, col={res['dam_c']}")
        print(f"Dam elevation: {res['dam_elev']:.2f} m")
        if res['first_r'] is not None:
            print(f"First downstream cell: row={res['first_r']}, col={res['first_c']}")
        else:
            print("First downstream cell: None (sink or flat)")
        print(f"Downstream path length: {res['path_len_px']} pixels")
        print(f"Approximate downstream distance: {res['dist_km']:.3f} km")
        print(f"End elevation: {res['end_elev']:.2f} m")
        print(f"Maximum flow accumulation along path: {res['max_acc']}")
        
        print("\nNote: This is preliminary terrain-based D8 drainage analysis.")
        print("It does not replace validated hydrological models or observed river data.")

        return 0

    except FlowAnalysisError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())