"""
Particle outflow diagnostic for HADR_TerrainChouldari.

This script inspects the DualSPHysics solver log and generated VTK particle
files to determine why fluid particles are being excluded from the simulation.

It does NOT modify the terrain, SPH parameters, or generated XML.
It does NOT run GenCase or DualSPHysics.
"""

import argparse
import re
import json
import sys
from pathlib import Path
import numpy as np


CASE_DIR = Path(__file__).resolve().parent
OUT_DIR = CASE_DIR / "HADR_TerrainChouldari_out"
PARTICLES_DIR = OUT_DIR / "particles"


# ---------------------------------------------------------------------------
# VTK parsing
# ---------------------------------------------------------------------------

def parse_vtk_particles(vtk_path: Path) -> np.ndarray | None:
    """
    Legacy VTK point parser (ASCII and BINARY).

    PartVTK writes big-endian BINARY polydata by default; the format line in
    the file header decides which parser runs:

    * BINARY -> slice exactly N*3 big-endian floats/doubles after the POINTS
      header line (numpy frombuffer).
    * ASCII  -> tokenize lines after the POINTS header, stopping at the next
      VTK section keyword or the first non-numeric token.
    """
    try:
        data = vtk_path.read_bytes()
    except Exception as exc:
        print(f"   WARNING: cannot read {vtk_path.name}: {exc}")
        return None

    header_match = re.search(rb"(?im)^POINTS\s+(\d+)\s+(\w+)", data)
    if header_match is None:
        print(f"   WARNING: no POINTS section found in {vtk_path.name}")
        return None

    num_points = int(header_match.group(1))
    type_token = header_match.group(2).lower()
    if num_points <= 0:
        print(f"   WARNING: no POINTS section found in {vtk_path.name}")
        return None

    # Header format line ("BINARY"/"ASCII") appears before the POINTS line.
    is_binary = (
        re.search(rb"(?im)^BINARY[ \t\r]*$", data[: header_match.start()])
        is not None
    )

    if is_binary:
        itemsize = 8 if type_token.startswith(b"double") else 4
        dtype = ">f8" if itemsize == 8 else ">f4"

        # The POINTS header line ends with a single newline (optionally
        # preceded by CR); binary data starts immediately after it.
        start = header_match.end()
        if data[start : start + 2] == b"\r\n":
            start += 2
        elif data[start : start + 1] == b"\n":
            start += 1

        count = num_points * 3
        buf = data[start : start + count * itemsize]
        if len(buf) < count * itemsize:
            print(
                f"   WARNING: truncated BINARY POINTS data in "
                f"{vtk_path.name} ({len(buf)} of {count * itemsize} bytes)"
            )
            return None

        pts = np.frombuffer(buf, dtype=dtype).astype(np.float64)
        return pts.reshape(num_points, 3)

    # ---- ASCII fallback ----
    lines = data.decode("utf-8", errors="ignore").splitlines()
    num_points = 0
    points_start_line = -1

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("POINTS"):
            parts = stripped.split()
            if len(parts) >= 2:
                try:
                    num_points = int(parts[1])
                    points_start_line = idx + 1
                    break
                except ValueError:
                    continue

    if points_start_line < 0 or num_points <= 0:
        print(f"   WARNING: no POINTS section found in {vtk_path.name}")
        return None

    needed = num_points * 3
    values: list[float] = []

    section_keywords = (
        "CELLS", "CELL_TYPES", "CELL_DATA", "POINT_DATA",
        "POLYGONS", "LINES", "TRIANGLE_STRIPS", "VERTICES",
        "SCALARS", "VECTORS", "NORMALS", "TEXTURE_COORDINATES",
        "COLOR_SCALARS", "LOOKUP_TABLE", "METADATA", "XML",
        "FIELD", "DATASET",
    )

    for line in lines[points_start_line:]:
        stripped = line.strip()
        if not stripped:
            continue

        upper = stripped.upper()
        if any(upper.startswith(kw) for kw in section_keywords):
            break

        tokens = stripped.split()
        for token in tokens:
            try:
                values.append(float(token))
            except ValueError:
                # Hit a non-numeric token; if we already have some values
                # this is probably the start of a new section.
                if values:
                    break
                continue

            if len(values) >= needed:
                break

        if len(values) >= needed:
            break

    if len(values) < needed:
        print(
            f"   WARNING: expected {needed} coordinate values in "
            f"{vtk_path.name}, found {len(values)}"
        )
        return None

    return np.array(values[:needed], dtype=np.float64).reshape(num_points, 3)


def vtk_has_type_scalar(vtk_path: Path) -> bool:
    """
    Return True if the VTK file contains a POINT_DATA scalar named 'type'
    or 'Type' (case-insensitive), which would let us classify fluid particles.
    """
    try:
        text = vtk_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False

    return bool(re.search(r"SCALARS\s+type\b", text, re.IGNORECASE))


def find_vtk_files() -> list[Path]:
    """
    Search for PartFluid VTK files in all plausible locations.
    """
    candidates: set[Path] = set()

    search_roots = [OUT_DIR, PARTICLES_DIR]
    patterns = [
        "PartFluid*.vtk",
        "PartFluid_*.vtk",
        "PartFluid*_*.vtk",
    ]

    for root in search_roots:
        if not root.is_dir():
            continue
        for pat in patterns:
            candidates.update(root.glob(pat))

    # Also do a shallow recursive search under OUT_DIR for any PartFluid*.vtk
    if OUT_DIR.is_dir():
        candidates.update(OUT_DIR.rglob("PartFluid*.vtk"))

    return sorted(candidates)


# ---------------------------------------------------------------------------
# Solver-log parsing
# ---------------------------------------------------------------------------

def parse_solver_log(log_path: Path) -> tuple[list[dict], int, list[str]]:
    """
    Parse the DualSPHysics .out / solver.log.

    Supports two formats:

    A) PART table rows:
       00005   0.250367   361   74   58,007   432  ...

    B) Bracketed time rows (older format):
       [Time: 0.01000] ... Np: 3906 ...

    Also extracts cumulative 'Particles out' lines:
       Particles out: 41 (total out: 41)

    Returns:
        steps          – list of {"time": float, "np": int}
        total_out      – last reported cumulative outflow count
        warnings       – any parse issues worth surfacing
    """
    if not log_path.exists():
        return [], 0, [f"log file not found: {log_path}"]

    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return [], 0, [f"cannot read log file: {exc}"]

    lines = text.splitlines()
    steps: list[dict] = []
    total_out = 0
    warnings: list[str] = []

    # ---- Format A: PART table ----
    # 00005   0.250367   361   74   58,007   432
    part_re = re.compile(
        r"^(\d{4,6})\s+([0-9]+(?:\.[0-9]+)?)\s+(\d+)\s+(\d+)\s+([\d,]+)"
    )

    # ---- Format B: bracketed ----
    bracket_re = re.compile(
        r"\[(?:Time:\s*)?([0-9]+(?:\.[0-9]+)?)\].*?Np[:=\s]+(\d+)",
        re.IGNORECASE,
    )

    # ---- Particles out ----
    out_re = re.compile(
        r"Particles\s+out[:=\s]+([\d,]+)(?:\s*\(total\s+out[:=\s]+([\d,]+)\))?",
        re.IGNORECASE,
    )

    for line in lines:
        stripped = line.strip()

        m = part_re.match(stripped)
        if m:
            t = float(m.group(2))
            np_val = int(m.group(5).replace(",", ""))
            steps.append({"time": t, "np": np_val})
            continue

        m = bracket_re.search(stripped)
        if m:
            t = float(m.group(1))
            np_val = int(m.group(2))
            steps.append({"time": t, "np": np_val})
            continue

        m = out_re.search(stripped)
        if m:
            total_out = int(m.group(2).replace(",", "")) if m.group(2) else int(m.group(1).replace(",", ""))

    if not steps:
        warnings.append("no time-step rows found in log (tried PART-table and bracketed formats)")

    return steps, total_out, warnings


# ---------------------------------------------------------------------------
# Domain / geometry helpers
# ---------------------------------------------------------------------------

def load_summary() -> dict | None:
    summary_path = CASE_DIR / "terrain_case_summary.json"
    if not summary_path.exists():
        print(f"ERROR: {summary_path} not found.")
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: cannot parse summary JSON: {exc}")
        return None


def print_geometry_summary(summary: dict) -> None:
    bounds = summary.get("bounds", {})
    terrain = bounds.get("terrain", {})
    pointmin = bounds.get("pointmin")
    pointmax = bounds.get("pointmax")
    dam = summary.get("dam", {})
    reservoir = summary.get("reservoir", {})
    upstream = summary.get("upstream", {})

    print("\n1. Simulation Domain & Geometry Summary")
    print("-" * 60)

    print(f"   Terrain X : {_fmt(terrain.get('x_min'))} to {_fmt(terrain.get('x_max'))}")
    print(f"   Terrain Y : {_fmt(terrain.get('y_min'))} to {_fmt(terrain.get('y_max'))}")
    print(f"   Terrain Z : {_fmt(terrain.get('z_min'))} to {_fmt(terrain.get('z_max'))}")

    if pointmin:
        print(f"   pointmin  : ({_fmt(pointmin[0])}, {_fmt(pointmin[1])}, {_fmt(pointmin[2])})")
    else:
        print("   pointmin  : NOT FOUND")

    if pointmax:
        print(f"   pointmax  : ({_fmt(pointmax[0])}, {_fmt(pointmax[1])}, {_fmt(pointmax[2])})")
    else:
        print("   pointmax  : NOT FOUND")

    print(f"   Dam center: ({_fmt(dam.get('center_x'))}, {_fmt(dam.get('center_y'))})")
    print(f"   Reservoir X: {_fmt(reservoir.get('x_min'))} to {_fmt(reservoir.get('x_max'))}")
    print(f"   Reservoir Y: {_fmt(reservoir.get('y_min'))} to {_fmt(reservoir.get('y_max'))}")
    print(f"   Reservoir Z: {_fmt(reservoir.get('z_min'))} to {_fmt(reservoir.get('z_max'))}")
    print(f"   Flow axis : {upstream.get('flow_axis')}   Upstream sign: {upstream.get('upstream_sign')}")


def _fmt(v) -> str:
    if v is None:
        return "None"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------------------
# VTK snapshot analysis
# ---------------------------------------------------------------------------

def analyze_vtk_snapshots(
    vtk_files: list[Path],
    pointmin: list | None,
    pointmax: list | None,
    flow_axis: str | None,
    upstream_sign: float,
) -> list[dict]:
    """
    For each VTK file, parse points and compute bounding-box statistics.
    Returns a list of dicts, one per successfully parsed file.
    """
    results: list[dict] = []

    if not vtk_files:
        print("\n3. VTK Particle Snapshots")
        print("   No PartFluid*.vtk files found.")
        return results

    print(f"\n3. VTK Particle Snapshots  ({len(vtk_files)} files found)")
    print("-" * 60)

    header = (
        f"   {'File':<32} {'Np':>7}  "
        f"{'X min':>10} {'X max':>10}  "
        f"{'Y min':>10} {'Y max':>10}  "
        f"{'Z min':>10} {'Z max':>10}  "
        f"{'Type?':>5}"
    )
    print(header)

    for vtk in vtk_files:
        pts = parse_vtk_particles(vtk)

        if pts is None or len(pts) == 0:
            print(f"   {vtk.name:<32} {'(parse failed)':>7}")
            continue

        has_type = vtk_has_type_scalar(vtk)

        row = {
            "file": vtk.name,
            "path": vtk,
            "np": len(pts),
            "x_min": float(np.min(pts[:, 0])),
            "x_max": float(np.max(pts[:, 0])),
            "y_min": float(np.min(pts[:, 1])),
            "y_max": float(np.max(pts[:, 1])),
            "z_min": float(np.min(pts[:, 2])),
            "z_max": float(np.max(pts[:, 2])),
            "has_type": has_type,
        }
        results.append(row)

        type_flag = "yes" if has_type else "no"

        print(
            f"   {vtk.name:<32} {len(pts):>7}  "
            f"{row['x_min']:>10.4f} {row['x_max']:>10.4f}  "
            f"{row['y_min']:>10.4f} {row['y_max']:>10.4f}  "
            f"{row['z_min']:>10.4f} {row['z_max']:>10.4f}  "
            f"{type_flag:>5}"
        )

    # Warn if no file carries a type scalar
    if results and not any(r["has_type"] for r in results):
        print(
            "\n   NOTE: VTK contains all particles; fluid-only classification "
            "unavailable from current files."
        )

    return results


# ---------------------------------------------------------------------------
# Boundary-proximity analysis
# ---------------------------------------------------------------------------

def check_boundary_proximity(
    vtk_results: list[dict],
    pointmin: list | None,
    pointmax: list | None,
    flow_axis: str | None,
    upstream_sign: float,
    margin: float = 0.05,
) -> list[str]:
    """
    Compare the last successfully parsed VTK snapshot against the
    simulation-domain bounds.
    """
    findings: list[str] = []

    if not vtk_results:
        findings.append("not enough evidence – no VTK snapshots parsed")
        return findings

    if pointmin is None or pointmax is None:
        findings.append("not enough evidence – pointmin / pointmax missing")
        return findings

    last = vtk_results[-1]

    pm_x_min, pm_y_min, pm_z_min = pointmin
    pm_x_max, pm_y_max, pm_z_max = pointmax

    # --- downstream ---
    if flow_axis == "x":
        if upstream_sign > 0 and last["x_max"] >= pm_x_max - margin:
            findings.append("downstream boundary (+X)")
        elif upstream_sign < 0 and last["x_min"] <= pm_x_min + margin:
            findings.append("downstream boundary (-X)")
    elif flow_axis == "y":
        if upstream_sign > 0 and last["y_max"] >= pm_y_max - margin:
            findings.append("downstream boundary (+Y)")
        elif upstream_sign < 0 and last["y_min"] <= pm_y_min + margin:
            findings.append("downstream boundary (-Y)")

    # --- lateral ---
    if flow_axis == "x":
        if last["y_min"] <= pm_y_min + margin or last["y_max"] >= pm_y_max - margin:
            findings.append("lateral boundary (Y)")
    elif flow_axis == "y":
        if last["x_min"] <= pm_x_min + margin or last["x_max"] >= pm_x_max - margin:
            findings.append("lateral boundary (X)")

    # --- vertical ---
    if last["z_max"] >= pm_z_max - margin:
        findings.append("vertical boundary (+Z)")
    if last["z_min"] <= pm_z_min + margin:
        findings.append("vertical boundary (-Z)")

    if not findings:
        findings.append("not enough evidence – particles do not appear to be hitting domain bounds")

    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    global OUT_DIR, PARTICLES_DIR

    parser = argparse.ArgumentParser(
        description=(
            "Particle outflow diagnostic for HADR_TerrainChouldari "
            "(read-only: parses the solver log and generated VTK particles)."
        )
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help=(
            "Simulation output directory to inspect: the directory containing "
            "solver.log (default: HADR_TerrainChouldari_out next to this "
            "script). Point it at a scenario-runner run directory to analyze "
            "that run."
        ),
    )
    args = parser.parse_args()

    if args.out_dir:
        OUT_DIR = Path(args.out_dir).resolve()
        PARTICLES_DIR = OUT_DIR / "particles"

    print("=" * 78)
    print("HADR_TerrainChouldari  Particle Outflow Diagnostic")
    print("=" * 78)

    # ---- geometry / domain ----
    summary = load_summary()
    if summary is None:
        return 1

    print_geometry_summary(summary)

    bounds = summary.get("bounds", {})
    pointmin = bounds.get("pointmin")
    pointmax = bounds.get("pointmax")
    upstream = summary.get("upstream", {})
    flow_axis = upstream.get("flow_axis")
    upstream_sign = float(upstream.get("upstream_sign", 1.0))

    # ---- solver log ----
    out_path = OUT_DIR / "HADR_TerrainChouldari.out"
    log_path = OUT_DIR / "solver.log"
    target_log = out_path if out_path.exists() else log_path

    print(f"\n2. Solver Log  ({target_log.name if target_log.exists() else 'NOT FOUND'})")
    print("-" * 60)

    steps, total_out, log_warnings = parse_solver_log(target_log)

    if steps:
        print(f"   {'PART':<8} {'Time':>12} {'Np':>10} {'Delta Np':>10}")
        prev_np = steps[0]["np"]
        for i, s in enumerate(steps):
            delta = s["np"] - prev_np
            print(f"   {i:<8} {s['time']:>12.6f} {s['np']:>10} {delta:>+10}")
            prev_np = s["np"]

        if total_out > 0:
            print(f"\n   Cumulative 'Particles out' from log: {total_out}")
    else:
        print("   No time-step rows parsed.")

    if log_warnings:
        for w in log_warnings:
            print(f"   WARNING: {w}")

    # ---- VTK snapshots ----
    vtk_files = find_vtk_files()
    vtk_results = analyze_vtk_snapshots(
        vtk_files, pointmin, pointmax, flow_axis, upstream_sign
    )

    # ---- immediate-exclusion heuristic ----
    immediate_drop = False
    if len(steps) >= 2:
        initial_np = steps[0]["np"]
        step1_np = steps[1]["np"]
        if step1_np < initial_np * 0.5:
            immediate_drop = True

    # ---- XML "default" domain check ----
    xml_path = CASE_DIR / "HADR_TerrainChouldari_Def.xml"
    xml_uses_default = False
    if xml_path.exists():
        try:
            xml_text = xml_path.read_text(encoding="utf-8")
            if 'x="default"' in xml_text or 'y="default"' in xml_text or 'z="default"' in xml_text:
                xml_uses_default = True
        except Exception:
            pass

    # ---- boundary proximity ----
    boundary_findings = check_boundary_proximity(
        vtk_results, pointmin, pointmax, flow_axis, upstream_sign
    )

    # ---- final diagnosis ----
    print("\n4. Diagnosis")
    print("-" * 60)

    if xml_uses_default:
        print(
            "   CRITICAL: <simulationdomain> uses 'default'.\n"
            "   DualSPHysics collapses the domain to the exact initial-particle\n"
            "   bounding box, so ANY movement causes immediate exclusion.\n"
            "   FIX: write explicit numeric posmin/posmax in the XML."
        )

    if immediate_drop:
        print(
            "   Particles dropped massively between step 0 and step 1.\n"
            "   Most likely cause: domain bounds too tight OR fluid overlapping\n"
            "   terrain STL at spawn time."
        )

    if boundary_findings:
        print("   Boundary proximity findings:")
        for f in boundary_findings:
            print(f"     - {f}")
    else:
        print("   No boundary proximity findings.")

    # ---- overall verdict ----
    print("\n5. Overall Verdict")
    print("-" * 60)

    if xml_uses_default:
        print("   ➜ simulation-domain bounds (fix 'default' in XML first)")
    elif immediate_drop and not vtk_results:
        print("   ➜ another exclusion mechanism (immediate drop, no VTK to inspect)")
    elif boundary_findings:
        for f in boundary_findings:
            print(f"   ➜ {f}")
    else:
        print("   ➜ another exclusion mechanism")

    print("\n" + "=" * 78)
    print("Diagnostic complete.  Do NOT run GenCase or DualSPHysics yet.")
    print("=" * 78)

    return 0


if __name__ == "__main__":
    sys.exit(main())