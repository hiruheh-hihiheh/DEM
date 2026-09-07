"""
Reusable DEM / terrain reader for Hydro Twin.

This module is intentionally limited to reading and inspecting DEM rasters.

It does NOT implement:
- clipping
- resampling
- smoothing
- terrain mesh generation
- terrain-to-SPH conversion
- DualSPHysics integration

Future pipeline:

    dam.geojson
        ↓
    dam_service.py
        ↓
    dam coordinates
        ↓
    DEM reader
        ↓
    terrain processing
        ↓
    terrain geometry
        ↓
    DualSPHysics
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError as exc:
    raise ImportError(
        "numpy is required for DEM statistics. "
        "Install the backend project dependencies."
    ) from exc

try:
    import rasterio
    from rasterio import warp
    from rasterio.crs import CRS
    from rasterio.windows import Window
except ImportError as exc:
    raise ImportError(
        "rasterio is required for DEM reading. "
        "Add rasterio to the existing backend dependency file."
    ) from exc


__all__ = [
    "DEMError",
    "DEMFileNotFoundError",
    "DEMInvalidRasterError",
    "DEMBandMissingError",
    "DEMCoordinateError",
    "DEMPointOutsideError",
    "DEMInfo",
    "DEMStatistics",
    "DEMPoint",
    "DEMReader",
    "inspect_dem",
    "get_dem_statistics",
    "query_dem_point",
]


class DEMError(Exception):
    """Base exception for DEM reader errors."""


class DEMFileNotFoundError(DEMError, FileNotFoundError):
    """Raised when the DEM file does not exist."""


class DEMInvalidRasterError(DEMError):
    """Raised when the file exists but is not a readable valid raster."""


class DEMBandMissingError(DEMError):
    """Raised when the raster does not have a readable first band."""


class DEMCoordinateError(DEMError):
    """Raised for invalid coordinate input or coordinate transformation failure."""


class DEMPointOutsideError(DEMCoordinateError):
    """Raised when a requested coordinate is outside the raster."""


@dataclass(frozen=True)
class DEMInfo:
    """
    Metadata describing an opened DEM raster.
    """

    path: str
    crs: str | None
    epsg: int | None
    width: int
    height: int
    count: int
    dtype: str | None
    bounds: tuple[float, float, float, float]
    transform: Any
    resolution: tuple[float, float]
    nodata: float | None
    dimensions: tuple[int, int]


@dataclass(frozen=True)
class DEMStatistics:
    """
    Elevation statistics from the first DEM band.

    nodata pixels are ignored.
    """

    count: int
    minimum: float
    maximum: float
    mean: float
    median: float
    nodata: float | None


@dataclass(frozen=True)
class DEMPoint:
    """
    Result of querying a longitude/latitude point against the DEM.

    longitude/latitude are the original EPSG:4326 input coordinates.
    x/y are the coordinates in the raster CRS.
    """

    longitude: float
    latitude: float
    x: float | None
    y: float | None
    inside: bool
    row: int | None
    col: int | None
    elevation: float | None


class DEMReader:
    """
    Reusable DEM reader.

    Usage:

        with DEMReader("output_hh.tif") as dem:
            info = dem.info
            stats = dem.statistics()
            point = dem.query_point(92.6591667, 11.6244444)
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._src: rasterio.DatasetReader | None = None

    def __enter__(self) -> "DEMReader":
        if not self.path.exists():
            raise DEMFileNotFoundError(
                f"DEM file not found: {self.path}"
            )

        try:
            self._src = rasterio.open(self.path)
        except FileNotFoundError as exc:
            raise DEMFileNotFoundError(
                f"DEM file not found: {self.path}"
            ) from exc
        except Exception as exc:
            raise DEMInvalidRasterError(
                f"Unable to open DEM as a raster: {self.path}"
            ) from exc

        try:
            self._validate()
        except Exception:
            self.close()
            raise

        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close()
        return False

    def close(self) -> None:
        if self._src is not None:
            self._src.close()
            self._src = None

    def _require_src(self) -> rasterio.DatasetReader:
        if self._src is None:
            raise DEMError(
                "DEM dataset is not open. "
                "Use DEMReader as a context manager."
            )
        return self._src

    def _validate(self) -> None:
        src = self._require_src()

        if src.width <= 0 or src.height <= 0:
            raise DEMInvalidRasterError(
                f"DEM has invalid dimensions: "
                f"{src.width} x {src.height}"
            )

        if src.count < 1:
            raise DEMBandMissingError(
                f"DEM has no readable bands: {self.path}"
            )

        if src.crs is None:
            raise DEMInvalidRasterError(
                f"DEM has no CRS defined: {self.path}"
            )

    @property
    def info(self) -> DEMInfo:
        src = self._require_src()

        crs = src.crs
        bounds = src.bounds

        epsg: int | None = None
        crs_text: str | None = None

        if crs is not None:
            crs_text = crs.to_string()
            epsg = crs.to_epsg()

        return DEMInfo(
            path=str(self.path),
            crs=crs_text,
            epsg=epsg,
            width=int(src.width),
            height=int(src.height),
            count=int(src.count),
            dtype=str(src.dtypes[0]) if src.dtypes else None,
            bounds=(
                float(bounds.left),
                float(bounds.bottom),
                float(bounds.right),
                float(bounds.top),
            ),
            transform=src.transform,
            resolution=(
                abs(float(src.res[0])),
                abs(float(src.res[1])),
            ),
            nodata=src.nodata,
            dimensions=(
                int(src.width),
                int(src.height),
            ),
        )

    def statistics(self) -> DEMStatistics:
        """
        Compute first-band elevation statistics while ignoring nodata.
        """

        src = self._require_src()

        try:
            band = src.read(1, masked=True)
        except Exception as exc:
            raise DEMBandMissingError(
                f"Unable to read first band from DEM: {self.path}"
            ) from exc

        # masked=True should already mask nodata, but this also guards
        # against NaN/Inf values.
        band = np.ma.masked_invalid(band)

        valid = band.compressed()

        if valid.size == 0:
            raise DEMInvalidRasterError(
                f"DEM first band contains no valid elevation pixels: {self.path}"
            )

        return DEMStatistics(
            count=int(valid.size),
            minimum=float(valid.min()),
            maximum=float(valid.max()),
            mean=float(valid.mean()),
            median=float(np.median(valid)),
            nodata=src.nodata,
        )

    def _ordered_bounds(self) -> tuple[float, float, float, float]:
        src = self._require_src()
        bounds = src.bounds

        return (
            min(float(bounds.left), float(bounds.right)),
            min(float(bounds.bottom), float(bounds.top)),
            max(float(bounds.left), float(bounds.right)),
            max(float(bounds.bottom), float(bounds.top)),
        )

    def _lonlat_to_raster_xy(
        self,
        longitude: float,
        latitude: float,
    ) -> tuple[float, float]:
        """
        Convert EPSG:4326 longitude/latitude into the raster CRS.

        Input order is explicitly:
            longitude, latitude
        """

        try:
            lon = float(longitude)
            lat = float(latitude)
        except (TypeError, ValueError) as exc:
            raise DEMCoordinateError(
                "longitude and latitude must be numeric"
            ) from exc

        if not math.isfinite(lon) or not math.isfinite(lat):
            raise DEMCoordinateError(
                "longitude and latitude must be finite values"
            )

        src = self._require_src()

        if src.crs is None:
            raise DEMInvalidRasterError(
                f"DEM has no CRS defined: {self.path}"
            )

        try:
            wgs84 = CRS.from_epsg(4326)
        except Exception as exc:
            raise DEMCoordinateError(
                "Unable to create EPSG:4326 source CRS"
            ) from exc

        if src.crs == wgs84 or src.crs.to_epsg() == 4326:
            return lon, lat

        try:
            xs, ys = warp.transform(
                wgs84,
                src.crs,
                [lon],
                [lat],
            )

            return float(xs[0]), float(ys[0])

        except Exception as exc:
            raise DEMCoordinateError(
                "Unable to transform EPSG:4326 coordinate "
                f"({lon}, {lat}) into raster CRS {src.crs}"
            ) from exc

    def _row_col_from_xy(
        self,
        x: float,
        y: float,
    ) -> tuple[int, int]:
        """
        Convert raster-CRS coordinates into raster row/col.

        Raises DEMPointOutsideError if the coordinate is outside the raster.
        """

        src = self._require_src()

        minx, miny, maxx, maxy = self._ordered_bounds()

        if x < minx or x > maxx or y < miny or y > maxy:
            raise DEMPointOutsideError(
                f"Coordinate ({x}, {y}) is outside DEM bounds "
                f"({minx}, {miny}) - ({maxx}, {maxy})"
            )

        try:
            row, col = src.index(x, y)
        except Exception as exc:
            raise DEMPointOutsideError(
                f"Unable to map coordinate ({x}, {y}) to DEM pixel"
            ) from exc

        row = int(row)
        col = int(col)

        # Handle points that lie exactly on the upper/right domain edge.
        if row < 0 or row >= src.height or col < 0 or col >= src.width:
            res_x = abs(float(src.res[0])) if src.res else 0.0
            res_y = abs(float(src.res[1])) if src.res else 0.0

            tol_x = max(res_x * 1e-6, 1e-12)
            tol_y = max(res_y * 1e-6, 1e-12)

            if row >= src.height and abs(y - maxy) <= tol_y:
                row = src.height - 1

            if row < 0 and abs(y - miny) <= tol_y:
                row = 0

            if col >= src.width and abs(x - maxx) <= tol_x:
                col = src.width - 1

            if col < 0 and abs(x - minx) <= tol_x:
                col = 0

        if 0 <= row < src.height and 0 <= col < src.width:
            return row, col

        raise DEMPointOutsideError(
            f"Coordinate ({x}, {y}) mapped outside DEM pixel grid "
            f"(row={row}, col={col})"
        )

    def _read_elevation(
        self,
        row: int,
        col: int,
    ) -> float | None:
        src = self._require_src()

        try:
            window = Window(
                col_off=col,
                row_off=row,
                width=1,
                height=1,
            )

            data = src.read(
                1,
                window=window,
                masked=True,
            )

        except Exception as exc:
            raise DEMBandMissingError(
                f"Unable to read elevation pixel at row={row}, col={col}"
            ) from exc

        data = np.ma.masked_invalid(data)

        if data.count() == 0:
            return None

        return float(data.compressed()[0])

    def contains_lonlat(
        self,
        longitude: float,
        latitude: float,
    ) -> bool:
        """
        Return True when the EPSG:4326 point lies inside the DEM.
        """

        point = self.query_point(
            longitude,
            latitude,
            raise_if_outside=False,
        )

        return point.inside

    def row_col_lonlat(
        self,
        longitude: float,
        latitude: float,
    ) -> tuple[int, int]:
        """
        Return raster row/col for an EPSG:4326 point.

        Raises DEMPointOutsideError when outside.
        """

        point = self.query_point(
            longitude,
            latitude,
            raise_if_outside=True,
        )

        if point.row is None or point.col is None:
            raise DEMPointOutsideError(
                "Point is outside the DEM"
            )

        return point.row, point.col

    def elevation_at_lonlat(
        self,
        longitude: float,
        latitude: float,
    ) -> float | None:
        """
        Return elevation at an EPSG:4326 point.

        Raises DEMPointOutsideError when outside.
        Returns None when the pixel itself is nodata.
        """

        point = self.query_point(
            longitude,
            latitude,
            raise_if_outside=True,
        )

        return point.elevation

    def query_point(
        self,
        longitude: float,
        latitude: float,
        *,
        raise_if_outside: bool = True,
    ) -> DEMPoint:
        """
        Query a point.

        If raise_if_outside=True, raises DEMPointOutsideError when outside.
        If raise_if_outside=False, returns DEMPoint with inside=False.
        """

        lon = float(longitude)
        lat = float(latitude)

        try:
            x, y = self._lonlat_to_raster_xy(lon, lat)
        except DEMCoordinateError:
            if raise_if_outside:
                raise

            return DEMPoint(
                longitude=lon,
                latitude=lat,
                x=None,
                y=None,
                inside=False,
                row=None,
                col=None,
                elevation=None,
            )

        try:
            row, col = self._row_col_from_xy(x, y)
        except DEMPointOutsideError:
            if raise_if_outside:
                raise

            return DEMPoint(
                longitude=lon,
                latitude=lat,
                x=x,
                y=y,
                inside=False,
                row=None,
                col=None,
                elevation=None,
            )

        elevation = self._read_elevation(row, col)

        return DEMPoint(
            longitude=lon,
            latitude=lat,
            x=x,
            y=y,
            inside=True,
            row=row,
            col=col,
            elevation=elevation,
        )


def inspect_dem(path: Path | str) -> DEMInfo:
    """
    Convenience helper to open a DEM, read metadata, and close it.
    """

    with DEMReader(path) as dem:
        return dem.info


def get_dem_statistics(path: Path | str) -> DEMStatistics:
    """
    Convenience helper to compute first-band DEM statistics.
    """

    with DEMReader(path) as dem:
        return dem.statistics()


def query_dem_point(
    path: Path | str,
    longitude: float,
    latitude: float,
    *,
    raise_if_outside: bool = True,
) -> DEMPoint:
    """
    Convenience helper to query a single EPSG:4326 point.
    """

    with DEMReader(path) as dem:
        return dem.query_point(
            longitude,
            latitude,
            raise_if_outside=raise_if_outside,
        )


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return "None"

    return f"{value:.6f}"


def _main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a DEM GeoTIFF and optionally query an EPSG:4326 "
            "longitude/latitude point."
        )
    )

    parser.add_argument(
        "path",
        help="Path to the DEM GeoTIFF, for example output_hh.tif",
    )

    parser.add_argument(
        "--longitude",
        type=float,
        help="EPSG:4326 longitude to query",
    )

    parser.add_argument(
        "--latitude",
        type=float,
        help="EPSG:4326 latitude to query",
    )

    args = parser.parse_args()

    if (args.longitude is None) != (args.latitude is None):
        parser.error(
            "Both --longitude and --latitude must be provided together."
        )

    try:
        with DEMReader(args.path) as dem:
            info = dem.info
            stats = dem.statistics()

            print("DEM path:", info.path)
            print("CRS:", info.crs)
            print("EPSG:", info.epsg)
            print("Width:", info.width)
            print("Height:", info.height)
            print("Bands:", info.count)
            print("Data type:", info.dtype)
            print("Bounds:", info.bounds)
            print("Resolution:", info.resolution)
            print("NoData:", info.nodata)
            print("Dimensions:", info.dimensions)
            print("Transform:", info.transform)

            print()
            print("Valid pixels:", stats.count)
            print("Minimum elevation:", f"{stats.minimum:.6f}")
            print("Maximum elevation:", f"{stats.maximum:.6f}")
            print("Mean elevation:", f"{stats.mean:.6f}")
            print("Median elevation:", f"{stats.median:.6f}")

            if args.longitude is not None and args.latitude is not None:
                print()
                print(
                    "Query coordinate: "
                    f"longitude={args.longitude}, latitude={args.latitude}"
                )

                point = dem.query_point(
                    args.longitude,
                    args.latitude,
                    raise_if_outside=False,
                )

                print("Inside:", point.inside)

                if point.inside:
                    print("Row:", point.row)
                    print("Col:", point.col)
                    print(
                        "Elevation:",
                        _format_optional_float(point.elevation),
                    )
                else:
                    print("Row: None")
                    print("Col: None")
                    print("Elevation: None")
                    return 2

        return 0

    except DEMError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())