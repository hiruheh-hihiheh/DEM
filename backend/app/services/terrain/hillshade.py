"""Grayscale + hillshade preview image of a DEM for the web map.

Reads the scenario's real DEM GeoTIFF (the same file the terrain pipeline
uses), downsamples it for the browser and renders a grayscale elevation
image blended with a classic hillshade lighting model. The response carries
the DEM bounds reprojected to WGS84 so MapLibre can place it as an
``image`` source with no extra georeferencing on the client.

Nothing here is decorative: every value (bounds, min/max elevation, source
path) comes from the raster itself.
"""

from __future__ import annotations

import base64
import io
import threading
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds

# Cap the preview raster so the JSON payload stays small and MapLibre
# stays responsive; aspect ratio is preserved.
MAX_SIZE = 900

# Blend of grayscale elevation (base) and hillshade lighting (contrast).
_HILLSHADE_STRENGTH = 0.55

_cache: dict[tuple[str, float, int], dict[str, Any]] = {}
_cache_lock = threading.Lock()


def render_dem_preview(dem_path: Path | str) -> dict[str, Any]:
    """Render (and cache) the DEM preview for the map.

    Always returns a JSON-safe dict: either ``available: true`` with a PNG
    data URI + WGS84 corner coordinates, or ``available: false`` with an
    ``error`` explaining why.
    """
    path = Path(dem_path)
    if not path.exists():
        return {"available": False, "error": f"DEM file not found: {path}"}

    try:
        mtime = path.stat().st_mtime
        size = path.stat().st_size
    except OSError as exc:
        return {"available": False, "error": str(exc)}

    key = (str(path), mtime, size)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    try:
        payload = _render(path)
    except Exception as exc:
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    with _cache_lock:
        _cache[key] = payload
        # Keep the cache bounded (one entry per DEM is plenty).
        if len(_cache) > 8:
            for old_key in list(_cache)[: len(_cache) - 8]:
                _cache.pop(old_key, None)
    return payload


def _render(path: Path) -> dict[str, Any]:
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError(f"DEM has no CRS defined: {path}")

        # Downsample for the browser while preserving aspect ratio.
        width, height = src.width, src.height
        scale = max(width, height) / MAX_SIZE
        if scale > 1:
            width = max(1, int(round(width / scale)))
            height = max(1, int(round(height / scale)))
            data = src.read(
                1, out_shape=(height, width), masked=True, resampling=Resampling.bilinear
            )
        else:
            data = src.read(1, masked=True)

        data = np.ma.masked_invalid(data)
        if data.count() == 0:
            raise ValueError("DEM contains no valid elevation data.")

        min_elev = float(data.min())
        max_elev = float(data.max())
        span = max_elev - min_elev

        # --- grayscale elevation base (0..255) -----------------------------
        filled = data.filled(np.nan)
        if span > 0:
            gray = (filled - min_elev) / span
        else:
            gray = np.zeros_like(filled)
        gray = np.nan_to_num(gray, nan=0.0)
        base = (gray * 255.0).astype(np.float32)

        # --- hillshade lighting (standard 315° azimuth / 45° altitude) -----
        # np.gradient returns (row, col) slopes in pixel units.
        pad = np.nan_to_num(filled, nan=min_elev)
        dy, dx = np.gradient(pad)
        # Scale slopes to elevation units per pixel (gradient is per cell,
        # cell size cancels out for the lighting direction we need here).
        slope = np.pi / 4.0 - np.arctan(np.hypot(dx, dy))
        aspect = np.arctan2(-dx, dy)
        az = np.deg2rad(315.0)
        alt = np.deg2rad(45.0)
        shade = (
            np.sin(alt) * np.sin(slope)
            + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
        )
        shade = np.clip(shade, 0.0, 1.0)

        blended = base * (1.0 - _HILLSHADE_STRENGTH) + (shade * 255.0) * _HILLSHADE_STRENGTH
        image = np.clip(blended, 0, 255).astype(np.uint8)
        # Nodata cells -> black (transparent-looking against the dark style).
        image[np.isnan(filled)] = 0

        # --- WGS84 corner coordinates for the MapLibre image source --------
        bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        w, s, e, n = (float(v) for v in bounds)

    png = _encode_png(image)
    return {
        "available": True,
        "image": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        "coordinates": [[w, n], [e, n], [e, s], [w, s]],
        "elevation": {
            "min": round(min_elev, 2),
            "max": round(max_elev, 2),
            "unit": "m",
        },
        "source": str(path),
        "size": {"width": int(image.shape[1]), "height": int(image.shape[0])},
    }


def _encode_png(image: np.ndarray) -> bytes:
    """Encode a uint8 grayscale array as PNG via GDAL (rasterio)."""
    from rasterio.io import MemoryFile

    height, width = image.shape
    with MemoryFile() as memfile:
        with memfile.open(
            driver="PNG",
            height=height,
            width=width,
            count=1,
            dtype="uint8",
        ) as dataset:
            dataset.write(image, 1)
        return memfile.read()
