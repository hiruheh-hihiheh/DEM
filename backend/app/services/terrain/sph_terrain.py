"""
Intermediate terrain-to-SPH preparation for Hydro Twin.

This module prepares a real DEM-based flood domain for eventual conversion
into a DualSPHysics terrain boundary. It does NOT generate SPH particles,
STL/OBJ meshes, or DualSPHysics XML.

LIMITATIONS & NOTES:
- Copernicus GLO-30 is a Digital Surface Model (DSM), not bare-earth DTM.
- Terrain resolution cannot exceed the information content of the source DEM.
- The simulation scale is a numerical prototype parameter, not a physical claim.
- This intermediate terrain is not yet a validated hydrodynamic model.
- Final SPH/Delft3D coupling will require hydrological/river observations
  and proper calibration.
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
from rasterio.transform import xy as rasterio_xy

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SPHTerrainError(Exception):
    """Base exception for SPH terrain preparation errors."""


class SPHTerrainFileNotFoundError(SPHTerrainError, FileNotFoundError):
    """Raised when a required input file does not exist."""


class SPHTerrainInvalidError(SPHTerrainError):
    """Raised when input data is invalid or inconsistent."""


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class SPHTerrainResult:
    """Container for the prepared terrain data and metadata."""

    x_m: np.ndarray
    y_m: np.ndarray
    elevation_m: np.ndarray
    x_sim: np.ndarray
    y_sim: np.ndarray
    z_sim: np.ndarray
    scale: float
    source_crs: str
    projected_crs: str
    target_resolution: float
    native_resolution: float
    dam_lon: float
    dam_lat: float
    dam_x_m: float
    dam_y_m: float
    origin_x: float
    origin_y: float
    num_samples: int
    elev_min: float
    elev_max: float
    sim_x_min: float
    sim_x_max: float
    sim_y_min: float
    sim_y_max: float
    sim_z_min: float
    sim_z_max: float


# ---------------------------------------------------------------------------
# Main preparation class
# ---------------------------------------------------------------------------

class SPHTerrainPreparer:
    """
    Prepares an intermediate terrain representation from a conditioned DEM
    and a flood-domain mask for eventual SPH boundary generation.
    """

    def __init__(
        self,
        dem_path: Path | str,
        domain_path: Path | str,
    ):
        self.dem_path = Path(dem_path)
        self.domain_path = Path(domain_path)

        if not self.dem_path.exists():
            raise SPHTerrainFileNotFoundError(
                f"Conditioned DEM not found: {self.dem_path}"
            )
        if not self.domain_path.exists():
            raise SPHTerrainFileNotFoundError(
                f"Flood domain not found: {self.domain_path}"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _utm_epsg_from_lonlat(lon: float, lat: float) -> int:
        """Determine the local UTM EPSG code from lon/lat."""
        zone = int(math.floor((lon + 180.0) / 6.0)) + 1
        zone = max(1, min(60, zone))
        if lat >= 0:
            return 32600 + zone
        return 32700 + zone

    # ------------------------------------------------------------------
    # Core preparation
    # ------------------------------------------------------------------

    def prepare(
        self,
        dam_longitude: float,
        dam_latitude: float,
        target_resolution_m: float = 30.0,
        scale: float = 0.02,
        out_npz: Path | str | None = None,
        out_csv: Path | str | None = None,
        out_png: Path | str | None = None,
    ) -> SPHTerrainResult:
        """
        Reads the domain GeoTIFF, extracts domain cells, transforms to UTM,
        applies resolution reduction and simulation scaling, and writes outputs.
        """

        # ------------------------------------------------------------------
        # 1. Read domain GeoTIFF (Band 1 = mask, Band 2 = elevation)
        # ------------------------------------------------------------------
        with rasterio.open(self.domain_path) as dom_src:
            if dom_src.count < 2:
                raise SPHTerrainInvalidError(
                    "Domain GeoTIFF must have at least 2 bands "
                    "(mask + elevation)."
                )

            domain_mask = dom_src.read(1)
            domain_elev = dom_src.read(2)
            dom_transform = dom_src.transform
            dom_crs = dom_src.crs
            dom_nodata = dom_src.nodata

            if dom_crs is None:
                raise SPHTerrainInvalidError("Domain GeoTIFF has no CRS.")

        # ------------------------------------------------------------------
        # 2. Read conditioned DEM for native resolution / CRS reference
        # ------------------------------------------------------------------
        with rasterio.open(self.dem_path) as dem_src:
            dem_crs = dem_src.crs
            native_res_x = abs(dem_src.res[0])
            native_res_y = abs(dem_src.res[1])

        native_res_m = (native_res_x + native_res_y) / 2.0
        # Approximate native resolution in metres (for EPSG:4326)
        if dom_crs.to_epsg() == 4326:
            mean_lat = math.radians(
                (dom_src.bounds.bottom + dom_src.bounds.top) / 2.0
            )
            cos_lat = math.cos(mean_lat)
            native_res_m = (
                native_res_x * 111320.0 * cos_lat
                + native_res_y * 110540.0
            ) / 2.0

        # ------------------------------------------------------------------
        # 3. Extract domain cells
        # ------------------------------------------------------------------
        valid = (domain_mask == 1) & (~np.isnan(domain_elev))
        if dom_nodata is not None:
            valid &= (domain_elev != dom_nodata)

        rows, cols = np.where(valid)
        if len(rows) == 0:
            raise SPHTerrainInvalidError(
                "No valid domain cells found in the flood domain."
            )

        elevations = domain_elev[rows, cols].astype(np.float64)

        # Geographic coordinates (lon/lat) via affine transform
        lons, lats = rasterio_xy(dom_transform, rows, cols)
        lons = np.array(lons, dtype=np.float64)
        lats = np.array(lats, dtype=np.float64)

        # ------------------------------------------------------------------
        # 4. Determine local UTM CRS and transform
        # ------------------------------------------------------------------
        utm_epsg = self._utm_epsg_from_lonlat(
            float(dam_longitude), float(dam_latitude)
        )
        utm_crs = CRS.from_epsg(utm_epsg)

        xs_utm, ys_utm = warp.transform(
            dom_crs, utm_crs, lons.tolist(), lats.tolist()
        )
        xs_utm = np.array(xs_utm, dtype=np.float64)
        ys_utm = np.array(ys_utm, dtype=np.float64)

        # Dam position in UTM
        dam_x_utm, dam_y_utm = warp.transform(
            dom_crs, utm_crs, [dam_longitude], [dam_latitude]
        )
        dam_x_utm = float(dam_x_utm[0])
        dam_y_utm = float(dam_y_utm[0])

        # ------------------------------------------------------------------
        # 5. Resolution reduction / resampling
        # ------------------------------------------------------------------
        effective_res = max(target_resolution_m, native_res_m)

        if effective_res > native_res_m * 1.5:
            # Need to aggregate: create a coarser grid
            xs_utm, ys_utm, elevations = self._resample_to_grid(
                xs_utm, ys_utm, elevations, effective_res
            )

        # ------------------------------------------------------------------
        # 6. Local origin (shift so simulation coords are small)
        # ------------------------------------------------------------------
        origin_x = float(np.min(xs_utm))
        origin_y = float(np.min(ys_utm))

        x_local = xs_utm - origin_x
        y_local = ys_utm - origin_y

        # Dam in local coordinates
        dam_x_local = dam_x_utm - origin_x
        dam_y_local = dam_y_utm - origin_y

        # ------------------------------------------------------------------
        # 7. Apply simulation scale
        # ------------------------------------------------------------------
        x_sim = x_local * scale
        y_sim = y_local * scale
        z_sim = elevations * scale

        # ------------------------------------------------------------------
        # 8. Build result
        # ------------------------------------------------------------------
        result = SPHTerrainResult(
            x_m=x_local,
            y_m=y_local,
            elevation_m=elevations,
            x_sim=x_sim,
            y_sim=y_sim,
            z_sim=z_sim,
            scale=scale,
            source_crs=str(dom_crs),
            projected_crs=str(utm_crs),
            target_resolution=effective_res,
            native_resolution=native_res_m,
            dam_lon=float(dam_longitude),
            dam_lat=float(dam_latitude),
            dam_x_m=float(dam_x_local),
            dam_y_m=float(dam_y_local),
            origin_x=origin_x,
            origin_y=origin_y,
            num_samples=len(x_local),
            elev_min=float(np.min(elevations)),
            elev_max=float(np.max(elevations)),
            sim_x_min=float(np.min(x_sim)),
            sim_x_max=float(np.max(x_sim)),
            sim_y_min=float(np.min(y_sim)),
            sim_y_max=float(np.max(y_sim)),
            sim_z_min=float(np.min(z_sim)),
            sim_z_max=float(np.max(z_sim)),
        )

        # ------------------------------------------------------------------
        # 9. Save outputs
        # ------------------------------------------------------------------
        if out_npz is not None:
            self._save_npz(result, Path(out_npz))

        if out_csv is not None:
            self._save_csv(result, Path(out_csv))

        if out_png is not None:
            self._save_preview(result, Path(out_png), dom_crs)

        return result

    # ------------------------------------------------------------------
    # Resampling
    # ------------------------------------------------------------------

    @staticmethod
    def _resample_to_grid(
        xs: np.ndarray,
        ys: np.ndarray,
        elev: np.ndarray,
        resolution: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Aggregates scattered points onto a regular grid at the given
        resolution using nearest-neighbour binning.
        """
        x_min, x_max = np.min(xs), np.max(xs)
        y_min, y_max = np.min(ys), np.max(ys)

        # Grid indices
        xi = ((xs - x_min) / resolution).astype(np.int64)
        yi = ((ys - y_min) / resolution).astype(np.int64)

        nx = int((x_max - x_min) / resolution) + 1
        ny = int((y_max - y_min) / resolution) + 1

        # Accumulate
        grid_sum = np.zeros((ny, nx), dtype=np.float64)
        grid_cnt = np.zeros((ny, nx), dtype=np.int64)

        np.add.at(grid_sum, (yi, xi), elev)
        np.add.at(grid_cnt, (yi, xi), 1)

        # Mean elevation per grid cell
        valid_cells = grid_cnt > 0
        grid_mean = np.full((ny, nx), np.nan)
        grid_mean[valid_cells] = grid_sum[valid_cells] / grid_cnt[valid_cells]

        # Output coordinates (cell centres)
        gy, gx = np.where(valid_cells)
        out_x = x_min + (gx + 0.5) * resolution
        out_y = y_min + (gy + 0.5) * resolution
        out_elev = grid_mean[valid_cells]

        return out_x, out_y, out_elev

    # ------------------------------------------------------------------
    # Output writers
    # ------------------------------------------------------------------

    @staticmethod
    def _save_npz(result: SPHTerrainResult, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            x_m=result.x_m,
            y_m=result.y_m,
            elevation_m=result.elevation_m,
            x_sim=result.x_sim,
            y_sim=result.y_sim,
            z_sim=result.z_sim,
            scale=np.array([result.scale]),
            target_resolution=np.array([result.target_resolution]),
            native_resolution=np.array([result.native_resolution]),
            origin_x=np.array([result.origin_x]),
            origin_y=np.array([result.origin_y]),
            dam_lon=np.array([result.dam_lon]),
            dam_lat=np.array([result.dam_lat]),
            dam_x_m=np.array([result.dam_x_m]),
            dam_y_m=np.array([result.dam_y_m]),
            source_crs=np.array([result.source_crs]),
            projected_crs=np.array([result.projected_crs]),
        )

    @staticmethod
    def _save_csv(result: SPHTerrainResult, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = "x_m,y_m,elevation_m,x_sim,y_sim,z_sim"
        data = np.column_stack([
            result.x_m,
            result.y_m,
            result.elevation_m,
            result.x_sim,
            result.y_sim,
            result.z_sim,
        ])
        np.savetxt(path, data, delimiter=",", header=header, comments="")

    def _save_preview(
        self,
        result: SPHTerrainResult,
        path: Path,
        src_crs: CRS,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        fig, ax = plt.subplots(figsize=(12, 9))

        # Scatter terrain points coloured by elevation
        sc = ax.scatter(
            result.x_m,
            result.y_m,
            c=result.elevation_m,
            cmap="terrain",
            s=4,
            edgecolors="none",
            alpha=0.85,
        )

        # Dam marker
        ax.plot(
            result.dam_x_m,
            result.dam_y_m,
            marker="*",
            color="red",
            markersize=18,
            markeredgecolor="black",
            markeredgewidth=1.5,
            label="Chouldari Dam",
            zorder=10,
        )

        # Local origin marker
        ax.plot(
            0, 0,
            marker="+",
            color="blue",
            markersize=12,
            markeredgewidth=2,
            label="Local Origin (0, 0)",
            zorder=10,
        )

        cbar = fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label("Elevation (m)")

        ax.set_title(
            f"SPH Terrain Preparation\n"
            f"Scale: {result.scale} | "
            f"Resolution: {result.target_resolution:.1f} m | "
            f"Samples: {result.num_samples}"
        )
        ax.set_xlabel("X (metres, local origin)")
        ax.set_ylabel("Y (metres, local origin)")
        ax.set_aspect("equal")
        ax.legend(loc="upper right")
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(path, dpi=300, bbox_inches="tight")
        plt.close(fig)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare an intermediate terrain representation for eventual "
            "SPH boundary generation. Does NOT create SPH particles or XML."
        )
    )

    parser.add_argument(
        "dem",
        help="Path to conditioned DEM GeoTIFF",
    )
    parser.add_argument(
        "domain",
        help="Path to flood-domain GeoTIFF (2-band: mask + elevation)",
    )
    parser.add_argument(
        "--longitude",
        type=float,
        default=92.6591667,
        help="Dam longitude (EPSG:4326)",
    )
    parser.add_argument(
        "--latitude",
        type=float,
        default=11.6244444,
        help="Dam latitude (EPSG:4326)",
    )
    parser.add_argument(
        "--target-resolution-m",
        type=float,
        default=30.0,
        help="Target terrain sampling resolution in metres (default: 30)",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=0.02,
        help="Numerical prototype scale factor (default: 0.02)",
    )
    parser.add_argument(
        "--output-npz",
        type=str,
        default=None,
        help="Output .npz path",
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default=None,
        help="Output .csv path",
    )
    parser.add_argument(
        "--output-png",
        type=str,
        default=None,
        help="Output preview .png path",
    )

    args = parser.parse_args()

    # Default output paths
    base_dir = Path(args.domain).parent
    stem = "chouldari_sph_terrain"

    out_npz = args.output_npz or str(base_dir / f"{stem}.npz")
    out_csv = args.output_csv or str(base_dir / f"{stem}.csv")
    out_png = args.output_png or str(base_dir / f"{stem}_preview.png")

    try:
        preparer = SPHTerrainPreparer(args.dem, args.domain)
        result = preparer.prepare(
            dam_longitude=args.longitude,
            dam_latitude=args.latitude,
            target_resolution_m=args.target_resolution_m,
            scale=args.scale,
            out_npz=out_npz,
            out_csv=out_csv,
            out_png=out_png,
        )

        # ------------------------------------------------------------------
        # Print summary
        # ------------------------------------------------------------------
        print(f"Source DEM: {args.dem}")
        print(f"Domain: {args.domain}")
        print(f"Source CRS: {result.source_crs}")
        print(f"Projected CRS: {result.projected_crs}")
        print(f"Terrain samples: {result.num_samples}")
        print(f"Target terrain resolution: {result.target_resolution:.2f} m")
        print(f"Native DEM resolution: {result.native_resolution:.2f} m")
        print(f"Original elevation range: "
              f"{result.elev_min:.2f} m to {result.elev_max:.2f} m")
        print(f"Simulation scale: {result.scale}")
        print(f"Simulation X range: "
              f"{result.sim_x_min:.4f} to {result.sim_x_max:.4f}")
        print(f"Simulation Y range: "
              f"{result.sim_y_min:.4f} to {result.sim_y_max:.4f}")
        print(f"Simulation Z range: "
              f"{result.sim_z_min:.4f} to {result.sim_z_max:.4f}")
        print(f"Local origin (UTM): "
              f"({result.origin_x:.2f}, {result.origin_y:.2f})")
        print(f"Dam position (local): "
              f"({result.dam_x_m:.2f}, {result.dam_y_m:.2f})")
        print(f"\nOutputs:")
        print(f"  NPZ: {out_npz}")
        print(f"  CSV: {out_csv}")
        print(f"  PNG: {out_png}")

        print("\n--- LIMITATIONS ---")
        print("- Copernicus GLO-30 is a DSM, not a bare-earth DTM.")
        print("- Terrain resolution cannot exceed DEM information content.")
        print("- Simulation scale is a numerical prototype parameter.")
        print("- This is NOT a validated hydrodynamic model.")
        print("- Final SPH/Delft3D coupling requires hydrological calibration.")

        return 0

    except SPHTerrainError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())