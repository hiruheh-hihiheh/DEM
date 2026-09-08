"""
Terrain surface mesh preparation for Hydro Twin.

This module converts the intermediate DEM-derived terrain representation
(`chouldari_sph_terrain.npz`) into a triangulated 3D terrain surface mesh.

The current output is an ASCII STL surface mesh:

    data/terrain/chouldari/chouldari_sph_terrain.stl

and a 3D preview image:

    data/terrain/chouldari/chouldari_sph_terrain_mesh_preview.png

IMPORTANT LIMITATIONS:

- The source elevation data is Copernicus GLO-30 DSM data.
- The mesh resolution cannot exceed the information content of the DEM.
- This is only a terrain SURFACE mesh. It is not a closed solid, and it does
  not add artificial side walls, bottoms, boxes, or containment boundaries.
- This is a mesh representation of the preliminary DEM-derived flood domain.
- It is not yet a validated hydrodynamic terrain representation.
- SPH particle generation is a later stage.
- DualSPHysics XML generation is a later stage.
- Final flood routing requires hydrological/river observations and calibration.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


class TerrainMeshError(Exception):
    """Base exception for terrain mesh errors."""


class TerrainMeshFileNotFoundError(TerrainMeshError, FileNotFoundError):
    """Raised when the intermediate terrain NPZ file does not exist."""


class TerrainMeshInvalidError(TerrainMeshError):
    """Raised when terrain data is invalid or meshing fails."""


@dataclass(frozen=True)
class TerrainMeshResult:
    """
    Result container for terrain mesh generation.
    """

    input_npz: str
    samples_loaded: int
    unique_samples: int
    scale: float

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    triangulation_backend: str
    triangles_generated: int
    triangles_removed: int
    triangles_kept: int

    max_edge_threshold: float
    min_edge: float
    max_edge_actual: float
    avg_edge: float
    connected_components: int

    dam_x_sim: float | None
    dam_y_sim: float | None
    dam_z_sim: float | None

    stl_output: str
    preview_output: str | None


class TerrainMeshBuilder:
    """
    Builds a terrain surface mesh from the intermediate SPH terrain NPZ file.
    """

    def __init__(self, npz_path: Path | str):
        self.npz_path = Path(npz_path)

        if not self.npz_path.exists():
            raise TerrainMeshFileNotFoundError(
                f"Intermediate terrain NPZ not found: {self.npz_path}"
            )

    # ------------------------------------------------------------------
    # Loading helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _optional_scalar(data: np.lib.npyio.NpzFile, key: str) -> float | None:
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

    def _load_npz(self) -> dict:
        try:
            with np.load(self.npz_path, allow_pickle=False) as data:
                required = ("x_sim", "y_sim", "z_sim", "scale")
                missing = [key for key in required if key not in data]

                if missing:
                    raise TerrainMeshInvalidError(
                        "NPZ is missing required arrays: "
                        + ", ".join(missing)
                    )

                x_sim = np.asarray(data["x_sim"], dtype=float).ravel()
                y_sim = np.asarray(data["y_sim"], dtype=float).ravel()
                z_sim = np.asarray(data["z_sim"], dtype=float).ravel()

                scale = float(np.asarray(data["scale"]).ravel()[0])

                dam_x_m = self._optional_scalar(data, "dam_x_m")
                dam_y_m = self._optional_scalar(data, "dam_y_m")

        except TerrainMeshInvalidError:
            raise
        except Exception as exc:
            raise TerrainMeshInvalidError(
                f"Unable to load intermediate terrain NPZ: {self.npz_path}"
            ) from exc

        if not (x_sim.size == y_sim.size == z_sim.size):
            raise TerrainMeshInvalidError(
                "x_sim, y_sim, and z_sim must have the same number of samples."
            )

        if not math.isfinite(scale) or scale <= 0.0:
            raise TerrainMeshInvalidError(
                f"Invalid simulation scale stored in NPZ: {scale}"
            )

        return {
            "x_sim": x_sim,
            "y_sim": y_sim,
            "z_sim": z_sim,
            "scale": scale,
            "dam_x_m": dam_x_m,
            "dam_y_m": dam_y_m,
        }

    # ------------------------------------------------------------------
    # Triangulation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _triangulate(xy: np.ndarray) -> tuple[np.ndarray, str]:
        """
        Triangulate scattered 2D terrain samples.

        Uses scipy.spatial.Delaunay when available. Otherwise falls back to
        matplotlib.tri.Triangulation, which is already available in this
        project because matplotlib is used for terrain previews.
        """

        try:
            from scipy.spatial import Delaunay
        except ImportError:
            Delaunay = None

        if Delaunay is not None:
            try:
                delaunay = Delaunay(xy)
                simplices = np.asarray(delaunay.simplices, dtype=np.int64)
                return simplices, "scipy.spatial.Delaunay"
            except Exception:
                # Fall through to the matplotlib triangulation backend.
                pass

        try:
            from matplotlib.tri import Triangulation

            triangulation = Triangulation(xy[:, 0], xy[:, 1])
            triangles = np.asarray(triangulation.triangles, dtype=np.int64)
            return triangles, "matplotlib.tri.Triangulation"
        except Exception as exc:
            raise TerrainMeshInvalidError(
                "Unable to triangulate terrain samples. The data may be too "
                "sparse, collinear, or duplicated."
            ) from exc

    @staticmethod
    def _orient_triangles(
        x: np.ndarray,
        y: np.ndarray,
        triangles: np.ndarray,
    ) -> np.ndarray:
        """
        Orient triangles consistently using the XY projection.
        """

        triangles = triangles.copy()

        i0 = triangles[:, 0]
        i1 = triangles[:, 1]
        i2 = triangles[:, 2]

        cross_z = (
            (x[i1] - x[i0]) * (y[i2] - y[i0])
            - (y[i1] - y[i0]) * (x[i2] - x[i0])
        )

        flip = cross_z < 0.0
        triangles[flip] = triangles[flip][:, [0, 2, 1]]

        return triangles

    @staticmethod
    def _filter_triangles(
        vertices: np.ndarray,
        triangles: np.ndarray,
        max_edge: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Remove triangles with unreasonable long edges or degenerate geometry.
        """

        v0 = vertices[triangles[:, 0]]
        v1 = vertices[triangles[:, 1]]
        v2 = vertices[triangles[:, 2]]

        e01 = np.linalg.norm(v1 - v0, axis=1)
        e12 = np.linalg.norm(v2 - v1, axis=1)
        e20 = np.linalg.norm(v0 - v2, axis=1)

        edge_lengths = np.stack([e01, e12, e20], axis=1)

        long_edge_keep = np.all(
            edge_lengths <= max_edge + 1e-9,
            axis=1,
        )

        cross = np.cross(v1 - v0, v2 - v0)
        cross_norm = np.linalg.norm(cross, axis=1)
        non_degenerate_keep = cross_norm > 1e-12

        keep = long_edge_keep & non_degenerate_keep

        kept_triangles = triangles[keep]
        kept_edge_lengths = edge_lengths[keep]

        return kept_triangles, kept_edge_lengths

    @staticmethod
    def _connected_components(triangles: np.ndarray) -> int:
        """
        Count connected triangular mesh components based on shared edges.
        """

        n = len(triangles)
        if n == 0:
            return 0

        edge_map: dict[tuple[int, int], list[int]] = {}

        for tri_index, tri in enumerate(triangles):
            a = int(tri[0])
            b = int(tri[1])
            c = int(tri[2])

            for p, q in ((a, b), (b, c), (c, a)):
                key = (p, q) if p < q else (q, p)
                edge_map.setdefault(key, []).append(tri_index)

        adjacency: list[list[int]] = [[] for _ in range(n)]

        for tri_indices in edge_map.values():
            if len(tri_indices) < 2:
                continue

            for i in range(len(tri_indices)):
                for j in range(i + 1, len(tri_indices)):
                    first = tri_indices[i]
                    second = tri_indices[j]
                    adjacency[first].append(second)
                    adjacency[second].append(first)

        visited = [False] * n
        components = 0

        for start in range(n):
            if visited[start]:
                continue

            components += 1
            stack = [start]
            visited[start] = True

            while stack:
                current = stack.pop()

                for neighbor in adjacency[current]:
                    if not visited[neighbor]:
                        visited[neighbor] = True
                        stack.append(neighbor)

        return components

    # ------------------------------------------------------------------
    # Output writers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_ascii_stl(
        path: Path,
        vertices: np.ndarray,
        triangles: np.ndarray,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        v0 = vertices[triangles[:, 0]]
        v1 = vertices[triangles[:, 1]]
        v2 = vertices[triangles[:, 2]]

        normals = np.cross(v1 - v0, v2 - v0)
        norms = np.linalg.norm(normals, axis=1)

        if np.any(norms <= 0.0):
            raise TerrainMeshInvalidError(
                "Unable to compute valid STL normals for all triangles."
            )

        unit_normals = normals / norms[:, None]

        solid_name = path.stem.replace(" ", "_") or "terrain"

        with path.open("w", encoding="ascii", newline="\n") as file:
            file.write(f"solid {solid_name}\n")

            for i in range(len(triangles)):
                nx, ny, nz = unit_normals[i]

                file.write(
                    f"  facet normal {nx:.8f} {ny:.8f} {nz:.8f}\n"
                )
                file.write("    outer loop\n")

                for vertex in (v0[i], v1[i], v2[i]):
                    file.write(
                        f"      vertex {vertex[0]:.8f} "
                        f"{vertex[1]:.8f} "
                        f"{vertex[2]:.8f}\n"
                    )

                file.write("    endloop\n")
                file.write("  endfacet\n")

            file.write(f"endsolid {solid_name}\n")

    @staticmethod
    def _write_preview(
        path: Path,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        triangles: np.ndarray,
        dam_x_sim: float | None,
        dam_y_sim: float | None,
        dam_z_sim: float | None,
        scale: float,
        unique_samples: int,
        backend: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        x_span = max(float(np.ptp(x)), 1e-6)
        y_span = max(float(np.ptp(y)), 1e-6)
        z_span = max(float(np.ptp(z)), 1e-9)

        # Preview-only vertical exaggeration so the terrain is visually clear.
        visual_z_span = max(z_span, 0.15 * max(x_span, y_span))
        vertical_exaggeration = visual_z_span / z_span

        fig = plt.figure(figsize=(12, 9))
        ax = fig.add_subplot(111, projection="3d")

        surf = ax.plot_trisurf(
            x,
            y,
            z,
            triangles=triangles,
            cmap="terrain",
            edgecolor="gray",
            linewidth=0.1,
            alpha=0.95,
            antialiased=True,
        )

        z_min = float(np.min(z))

        if (
            dam_x_sim is not None
            and dam_y_sim is not None
            and dam_z_sim is not None
        ):
            ax.scatter(
                [dam_x_sim],
                [dam_y_sim],
                [dam_z_sim],
                color="red",
                marker="*",
                s=220,
                edgecolors="black",
                linewidths=1.0,
                depthshade=False,
                label="Chouldari Dam",
                zorder=10,
            )

            ax.plot(
                [dam_x_sim, dam_x_sim],
                [dam_y_sim, dam_y_sim],
                [z_min, dam_z_sim],
                color="red",
                linestyle="--",
                linewidth=1.5,
            )

        ax.scatter(
            [0.0],
            [0.0],
            [z_min],
            color="blue",
            marker="o",
            s=70,
            edgecolors="black",
            linewidths=1.0,
            depthshade=False,
            label="Local coordinate origin (X=0, Y=0)",
            zorder=10,
        )

        cbar = fig.colorbar(surf, ax=ax, shrink=0.6, pad=0.08)
        cbar.set_label("Simulation elevation z_sim")

        ax.set_title(
            "Preliminary DEM Terrain Surface Mesh\n"
            f"Samples: {unique_samples} | Scale: {scale} | "
            f"Triangles: {len(triangles)}\n"
            f"Backend: {backend} | Preview vertical exaggeration: "
            f"{vertical_exaggeration:.2f}x"
        )

        ax.set_xlabel("x_sim (scaled simulation metres)")
        ax.set_ylabel("y_sim (scaled simulation metres)")
        ax.set_zlabel("z_sim (scaled elevation)")

        ax.set_box_aspect((x_span, y_span, visual_z_span))
        ax.view_init(elev=35, azim=-60)
        ax.legend(loc="upper right")

        plt.tight_layout()
        plt.savefig(path, dpi=300, bbox_inches="tight")
        plt.close(fig)

    # ------------------------------------------------------------------
    # Main build routine
    # ------------------------------------------------------------------

    def build(
        self,
        output_stl: Path | str,
        preview_png: Path | str | None = None,
        max_edge_m: float = 1.5,
    ) -> TerrainMeshResult:
        if not math.isfinite(max_edge_m) or max_edge_m <= 0.0:
            raise TerrainMeshInvalidError(
                "max-edge-m must be a positive finite simulation distance."
            )

        payload = self._load_npz()

        x_raw = payload["x_sim"]
        y_raw = payload["y_sim"]
        z_raw = payload["z_sim"]
        scale = payload["scale"]

        samples_loaded = int(x_raw.size)

        finite_mask = (
            np.isfinite(x_raw)
            & np.isfinite(y_raw)
            & np.isfinite(z_raw)
        )

        x = x_raw[finite_mask]
        y = y_raw[finite_mask]
        z = z_raw[finite_mask]

        if x.size < 3:
            raise TerrainMeshInvalidError(
                "At least three valid terrain samples are required to build a mesh."
            )

        # Remove duplicated XY samples that could destabilize triangulation.
        xy = np.column_stack([x, y])
        xy_key = np.round(xy, 9)

        _, unique_idx = np.unique(xy_key, axis=0, return_index=True)
        unique_idx = np.sort(unique_idx)

        x = x[unique_idx]
        y = y[unique_idx]
        z = z[unique_idx]

        unique_samples = int(x.size)

        if unique_samples < 3:
            raise TerrainMeshInvalidError(
                "At least three unique XY terrain samples are required."
            )

        triangles, backend = self._triangulate(np.column_stack([x, y]))

        if triangles.size == 0:
            raise TerrainMeshInvalidError(
                "Triangulation produced no triangles."
            )

        triangles = self._orient_triangles(x, y, triangles)

        vertices = np.column_stack([x, y, z])

        kept_triangles, kept_edge_lengths = self._filter_triangles(
            vertices,
            triangles,
            max_edge_m,
        )

        if kept_triangles.size == 0:
            raise TerrainMeshInvalidError(
                "All generated triangles were removed. "
                "Try increasing --max-edge-m."
            )

        min_edge = float(np.min(kept_edge_lengths))
        max_edge_actual = float(np.max(kept_edge_lengths))
        avg_edge = float(np.mean(kept_edge_lengths))

        components = self._connected_components(kept_triangles)

        output_stl = Path(output_stl)
        self._write_ascii_stl(
            output_stl,
            vertices,
            kept_triangles,
        )

        dam_x_sim: float | None = None
        dam_y_sim: float | None = None
        dam_z_sim: float | None = None

        dam_x_m = payload["dam_x_m"]
        dam_y_m = payload["dam_y_m"]

        if dam_x_m is not None and dam_y_m is not None:
            dam_x_sim = dam_x_m * scale
            dam_y_sim = dam_y_m * scale

            distances_sq = (x - dam_x_sim) ** 2 + (y - dam_y_sim) ** 2
            nearest_index = int(np.argmin(distances_sq))
            dam_z_sim = float(z[nearest_index])

        preview_output: str | None = None

        if preview_png is not None:
            preview_path = Path(preview_png)

            self._write_preview(
                path=preview_path,
                x=x,
                y=y,
                z=z,
                triangles=kept_triangles,
                dam_x_sim=dam_x_sim,
                dam_y_sim=dam_y_sim,
                dam_z_sim=dam_z_sim,
                scale=scale,
                unique_samples=unique_samples,
                backend=backend,
            )

            preview_output = str(preview_path)

        return TerrainMeshResult(
            input_npz=str(self.npz_path),
            samples_loaded=samples_loaded,
            unique_samples=unique_samples,
            scale=scale,
            x_min=float(np.min(x)),
            x_max=float(np.max(x)),
            y_min=float(np.min(y)),
            y_max=float(np.max(y)),
            z_min=float(np.min(z)),
            z_max=float(np.max(z)),
            triangulation_backend=backend,
            triangles_generated=int(len(triangles)),
            triangles_removed=int(len(triangles) - len(kept_triangles)),
            triangles_kept=int(len(kept_triangles)),
            max_edge_threshold=float(max_edge_m),
            min_edge=min_edge,
            max_edge_actual=max_edge_actual,
            avg_edge=avg_edge,
            connected_components=int(components),
            dam_x_sim=dam_x_sim,
            dam_y_sim=dam_y_sim,
            dam_z_sim=dam_z_sim,
            stl_output=str(output_stl),
            preview_output=preview_output,
        )


def _main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a preliminary terrain surface mesh from the intermediate "
            "DEM-derived SPH terrain representation."
        )
    )

    parser.add_argument(
        "npz",
        help="Path to intermediate terrain NPZ file",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output ASCII STL path",
    )

    parser.add_argument(
        "--preview",
        type=str,
        default=None,
        help="Output 3D mesh preview PNG path",
    )

    parser.add_argument(
        "--max-edge-m",
        type=float,
        default=1.5,
        help=(
            "Maximum allowed triangle edge length in simulation coordinates. "
            "Default: 1.5"
        ),
    )

    args = parser.parse_args()

    npz_path = Path(args.npz)

    output_stl = (
        Path(args.output)
        if args.output
        else npz_path.with_suffix(".stl")
    )

    preview_png = (
        Path(args.preview)
        if args.preview
        else npz_path.with_name(f"{npz_path.stem}_mesh_preview.png")
    )

    try:
        builder = TerrainMeshBuilder(npz_path)

        result = builder.build(
            output_stl=output_stl,
            preview_png=preview_png,
            max_edge_m=args.max_edge_m,
        )

        print(f"Input NPZ: {result.input_npz}")
        print(
            f"Samples: {result.samples_loaded} loaded / "
            f"{result.unique_samples} unique XY"
        )
        print(f"Simulation scale: {result.scale}")
        print(f"X range: {result.x_min:.4f} to {result.x_max:.4f}")
        print(f"Y range: {result.y_min:.4f} to {result.y_max:.4f}")
        print(f"Z range: {result.z_min:.4f} to {result.z_max:.4f}")
        print(f"Triangles generated: {result.triangles_generated}")
        print(f"Triangles removed: {result.triangles_removed}")
        print(f"Triangles kept: {result.triangles_kept}")
        print(
            f"Maximum triangle edge: "
            f"{result.max_edge_threshold:.4f} simulation units (configured)"
        )
        print(f"STL output: {result.stl_output}")

        if result.preview_output is not None:
            print(f"Preview output: {result.preview_output}")

        print()
        print("Mesh diagnostics:")
        print(f"  Triangulation backend: {result.triangulation_backend}")
        print(f"  Minimum triangle edge: {result.min_edge:.6f}")
        print(f"  Maximum triangle edge: {result.max_edge_actual:.6f}")
        print(f"  Average triangle edge: {result.avg_edge:.6f}")
        print(f"  Connected components: {result.connected_components}")

        if (
            result.dam_x_sim is not None
            and result.dam_y_sim is not None
            and result.dam_z_sim is not None
        ):
            print()
            print("Dam position in simulation coordinates:")
            print(f"  x_sim: {result.dam_x_sim:.6f}")
            print(f"  y_sim: {result.dam_y_sim:.6f}")
            print(f"  z_sim: {result.dam_z_sim:.6f}")

        print()
        print("--- IMPORTANT LIMITATIONS ---")
        print("- This is a terrain SURFACE mesh only.")
        print("- No artificial box, side walls, or bottom closure were added.")
        print("- The source DEM is Copernicus GLO-30 DSM data.")
        print("- Terrain resolution cannot exceed DEM information content.")
        print("- The simulation scale is a numerical prototype parameter.")
        print("- This is not yet a validated hydrodynamic terrain model.")
        print("- SPH particle generation remains a later stage.")
        print("- DualSPHysics XML generation remains a later stage.")

        return 0

    except TerrainMeshError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())