from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.transform import rowcol


def local_xy(lon, lat, lat0):
    """Convert lon/lat to local metric X/Y coordinates."""
    r = 6371000.0
    return (
        r * math.cos(math.radians(lat0)) * math.radians(lon),
        r * math.radians(lat),
    )


def lonlat_from_xy(x, y, lat0):
    """Convert local metric X/Y coordinates back to lon/lat."""
    r = 6371000.0
    return (
        math.degrees(x / (r * math.cos(math.radians(lat0)))),
        math.degrees(y / r),
    )


def sample_axis(ds, arr, lon0, lat0, bearing_deg, distances_m):
    """
    Sample the DEM along a line defined by a bearing and distances from center.
    Tracks unique raster cells to avoid oversampling a coarse DEM.
    """
    x0, y0 = local_xy(lon0, lat0, lat0)
    b = math.radians(bearing_deg)
    out = []
    unique_cells = set()

    for d in distances_m:
        x = x0 + d * math.sin(b)
        y = y0 + d * math.cos(b)
        lon, lat = lonlat_from_xy(x, y, lat0)
        r, c = rowcol(ds.transform, lon, lat)
        r, c = int(r), int(c)
        
        z = None
        if 0 <= r < ds.height and 0 <= c < ds.width:
            val = float(arr[r, c])
            if math.isfinite(val):
                z = val
                unique_cells.add((r, c))
                
        out.append({
            "distance_m": float(d),
            "longitude": float(lon),
            "latitude": float(lat),
            "elevation_m": z,
        })
    return out, len(unique_cells)


def score_orientation(profile, unique_cells):
    """
    Evaluate whether the terrain profile represents a plausible valley crossing.
    
    Scoring Logic:
    1. Divide the profile into left bank (first 15%), center valley (middle 30%), 
       and right bank (last 15%).
    2. Calculate relief as bank elevation minus center elevation.
    3. If either bank is lower than or equal to the center, it's not a valley 
       crossing. Heavily penalize the score.
    4. If it is a valid crossing, reward high bilateral relief (depth of the 
       valley relative to the lower bank) and penalize asymmetry (difference 
       between left and right relief).
    """
    valid = [p for p in profile if p["elevation_m"] is not None]
    
    if len(valid) < 3 or unique_cells < 2:
        return None
        
    z_vals = np.array([p["elevation_m"] for p in valid])
    distances = np.array([p["distance_m"] for p in valid])
    
    max_d = np.max(distances)
    min_d = np.min(distances)
    span = max_d - min_d
    
    if span <= 0:
        return None
        
    # Define zones based on distance along the profile
    left_thresh = min_d + 0.15 * span
    right_thresh = max_d - 0.15 * span
    center_left = min_d + 0.35 * span
    center_right = max_d - 0.35 * span
    
    left_bank_z = z_vals[(distances <= left_thresh) & (distances >= min_d)]
    right_bank_z = z_vals[(distances >= right_thresh) & (distances <= max_d)]
    center_z = z_vals[(distances >= center_left) & (distances <= center_right)]
    
    # Fallback to simple array slicing if distance thresholds fail
    if left_bank_z.size == 0 or right_bank_z.size == 0 or center_z.size == 0:
        n = len(z_vals)
        left_bank_z = z_vals[:max(1, n//6)]
        right_bank_z = z_vals[-max(1, n//6):]
        center_z = z_vals[n//3 : 2*n//3 + 1]

    left_end = float(np.mean(left_bank_z))
    right_end = float(np.mean(right_bank_z))
    center = float(np.mean(center_z))
    
    min_z = float(np.min(z_vals))
    max_z = float(np.max(z_vals))
    
    left_relief = left_end - center
    right_relief = right_end - center
    bilateral_relief = min(left_relief, right_relief)
    
    both_ends_above_center = (left_end > center) and (right_end > center)
    
    # Scoring logic
    if not both_ends_above_center:
        # Heavily penalize if it's not a true valley crossing
        score = -100.0 - abs(left_relief) - abs(right_relief)
        if bilateral_relief > 0:
            quality = "WEAK_CROSSING"
        else:
            quality = "INVALID/INSUFFICIENT_DATA"
    else:
        # Reward bilateral relief, penalize asymmetry
        symmetry_penalty = abs(left_relief - right_relief)
        score = (
            10.0 * bilateral_relief 
            + 2.0 * (left_relief + right_relief) 
            - 2.0 * symmetry_penalty
        )
        if bilateral_relief > 2.0:
            quality = "GOOD_CROSS_VALLEY"
        else:
            quality = "WEAK_CROSSING"
            
    if unique_cells < 3:
        quality = "INVALID/INSUFFICIENT_DATA"

    return {
        "score": float(score),
        "left_end_elevation_m": left_end,
        "center_elevation_m": center,
        "right_end_elevation_m": right_end,
        "min_elevation_m": min_z,
        "max_elevation_m": max_z,
        "left_relief_m": left_relief,
        "right_relief_m": right_relief,
        "bilateral_relief_m": bilateral_relief,
        "both_ends_above_center": both_ends_above_center,
        "unique_cells_sampled": unique_cells,
        "quality": quality,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Scan possible Chouldari dam orientations using the real DEM."
    )
    ap.add_argument("dem")
    ap.add_argument("--longitude", type=float, required=True)
    ap.add_argument("--latitude", type=float, required=True)
    ap.add_argument("--dam-length-m", type=float, default=98.0)
    ap.add_argument("--orientation-step-deg", type=float, default=1.0)
    ap.add_argument("--profile-step-m", type=float, default=10.0)
    ap.add_argument("--output-json", default=None)
    ap.add_argument("--output-png", default=None)
    ap.add_argument("--top-n", type=int, default=10)
    args = ap.parse_args()

    dem = Path(args.dem)
    if not dem.exists():
        raise SystemExit(f"DEM not found: {dem}")

    with rasterio.open(dem) as ds:
        arr = ds.read(1).astype(float)

        # Sample exact center point
        r, c = rowcol(ds.transform, args.longitude, args.latitude)
        r, c = int(r), int(c)
        if r < 0 or r >= ds.height or c < 0 or c >= ds.width:
            raise SystemExit("Dam point is outside the DEM.")
        dam_z = float(arr[r, c])
        if not math.isfinite(dam_z):
            raise SystemExit("Dam point elevation is invalid/NaN.")

        half = args.dam_length_m / 2.0
        step = max(1.0, args.profile_step_m)
        distances = np.arange(-half, half + step/2.0, step)

        results = []

        for bearing in np.arange(0.0, 180.0, max(args.orientation_step_deg, 0.1)):
            profile, unique_cells = sample_axis(
                ds,
                arr,
                args.longitude,
                args.latitude,
                float(bearing),
                distances,
            )
            metrics = score_orientation(profile, unique_cells)
            if metrics is None:
                continue

            results.append(
                {
                    "bearing_deg": float(bearing),
                    **metrics,
                    "profile": profile,
                }
            )

        if not results:
            raise SystemExit("No valid orientations could be evaluated.")

        results_sorted = sorted(results, key=lambda x: x["score"], reverse=True)

        out_json = Path(args.output_json) if args.output_json else (
            dem.with_name(dem.stem + "_orientation_scan.json")
        )
        out_png = Path(args.output_png) if args.output_png else (
            dem.with_name(dem.stem + "_orientation_scan.png")
        )

        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_png.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "dem": str(dem),
            "dam": {
                "longitude": args.longitude,
                "latitude": args.latitude,
                "elevation_m": dam_z,
                "registered_length_reference_m": args.dam_length_m,
            },
            "scan": {
                "orientation_step_deg": args.orientation_step_deg,
                "profile_step_m": args.profile_step_m,
                "orientations_tested": len(results_sorted),
                "definition": "bearing of dam axis, clockwise from north",
            },
            "top_candidates": results_sorted[: max(1, args.top_n)],
            "note": (
                "Terrain-based screening only. This does not establish the "
                "surveyed dam footprint, structural geometry, or validated "
                "hydrological river alignment."
            ),
        }

        out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        bearings = [r["bearing_deg"] for r in results_sorted]
        scores = [r["score"] for r in results_sorted]

        fig = plt.figure(figsize=(11, 8))
        ax1 = fig.add_axes([0.10, 0.58, 0.84, 0.32])
        ax1.plot(bearings, scores, marker='.', linestyle='-')
        ax1.set_xlabel("Dam-axis bearing (degrees clockwise from north)")
        ax1.set_ylabel("Terrain-fit score")
        ax1.set_title(f"Chouldari {args.dam_length_m:.0f}m dam-orientation scan")
        ax1.grid(True, alpha=0.25)

        top = results_sorted[: min(3, len(results_sorted))]
        ax2 = fig.add_axes([0.10, 0.10, 0.84, 0.38])

        for candidate in top:
            p = candidate["profile"]
            xp = [q["distance_m"] for q in p]
            zp = [q["elevation_m"] if q["elevation_m"] is not None else np.nan for q in p]
            ax2.plot(
                xp,
                zp,
                marker='o',
                label=f"{candidate['bearing_deg']:.0f}° "
                      f"(score {candidate['score']:.1f}, {candidate['quality']})",
            )

        ax2.axvline(0.0, color='black', linestyle="--", label="Dam Center")
        ax2.axhline(dam_z, color='red', linestyle=":", label=f"Center Z ({dam_z:.1f}m)")
        ax2.axvline(-args.dam_length_m/2.0, color='gray', linestyle=":", alpha=0.5)
        ax2.axvline(args.dam_length_m/2.0, color='gray', linestyle=":", alpha=0.5)
        
        ax2.set_xlabel("Distance along candidate dam axis (m)")
        ax2.set_ylabel("Elevation (m)")
        ax2.set_title("Top candidate terrain profiles")
        ax2.grid(True, alpha=0.25)
        ax2.legend(fontsize=8)

        fig.suptitle(
            f"Chouldari Dam Orientation Scan — {args.dam_length_m:.0f} m reference length",
            fontsize=13,
        )
        fig.savefig(out_png, dpi=150, bbox_inches="tight")
        plt.close(fig)

        print("=" * 95)
        print("CHOULDARI REAL-SITE DAM ORIENTATION SCAN")
        print("=" * 95)
        print(f"Dam point: {args.latitude:.8f}, {args.longitude:.8f}")
        print(f"Dam elevation: {dam_z:.3f} m")
        print(f"Registered length reference: {args.dam_length_m:.1f} m")
        print(f"Orientations tested: {len(results_sorted)}")
        print()
        print("Top candidates:")
        print(
            f"{'Bearing':>7} | {'Score':>8} | {'LeftEnd':>8} | {'Center':>8} | "
            f"{'RightEnd':>8} | {'LeftRel':>8} | {'RightRel':>8} | {'BilatRel':>8} | Quality"
        )
        print("-" * 95)

        for r in results_sorted[: args.top_n]:
            print(
                f"{r['bearing_deg']:7.1f} | "
                f"{r['score']:8.2f} | "
                f"{r['left_end_elevation_m']:8.2f} | "
                f"{r['center_elevation_m']:8.2f} | "
                f"{r['right_end_elevation_m']:8.2f} | "
                f"{r['left_relief_m']:8.2f} | "
                f"{r['right_relief_m']:8.2f} | "
                f"{r['bilateral_relief_m']:8.2f} | "
                f"{r['quality']}"
            )

        best = results_sorted[0]
        print()
        print(
            f"Best terrain-screened bearing: "
            f"{best['bearing_deg']:.1f}°"
        )
        print()
        print("IMPORTANT: This is terrain screening, NOT a surveyed dam alignment.")
        print()
        print(f"JSON: {out_json}")
        print(f"PNG:  {out_png}")


if __name__ == "__main__":
    main()