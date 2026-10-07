"""Step definitions for the scenario runner.

``build_plan`` assembles an ordered list of :class:`Step` objects around the
EXISTING project components — it never re-implements pipeline logic:

- terrain stages call backend/app/services/terrain/ classes in-process
- the case stage calls generate_terrain_case.generate_case(config_path=...)
- the simulation stage calls backend/app/services/sph/SPHRunner
- post-processing runs the existing example scripts as subprocesses
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import REPO_ROOT, Scenario, ensure_backend_on_path, rel
from .report import Reporter

STAGES = ("terrain", "sph", "simulation", "postprocess")


@dataclass
class RunContext:
    scenario: Scenario
    run_dir: Path
    reporter: Reporter
    dsp_root: Path | None = None
    sim_result: dict[str, Any] | None = None
    commands: list[str] = field(default_factory=list)


@dataclass
class Step:
    id: str
    stage: str
    label: str
    params: dict[str, Any]
    inputs: list[Path]
    outputs: list[Path]
    run: Callable[[RunContext], None]
    skippable: bool = True


# ---------------------------------------------------------------------------
# Case-configuration helpers
# ---------------------------------------------------------------------------

def merged_case_config(scenario: Scenario) -> dict[str, Any]:
    """Base case config + scenario overrides (overrides win)."""
    merged = dict(scenario.case["base_config_data"])
    merged.update(scenario.case["overrides"])
    return merged


def effective_case_config_path(scenario: Scenario, run_dir: Path) -> Path:
    """Where generate_case() should read its configuration from."""
    if scenario.case["overrides"]:
        return run_dir / "case_config.json"
    return scenario.case["config"]


def case_output_paths(scenario: Scenario) -> dict[str, Path]:
    """Paths generate_case() writes (it always writes into its own case dir)."""
    cfg = merged_case_config(scenario)
    case_dir = scenario.case["generator"].parent
    case_name = str(cfg.get("case_name", "HydroTwinCase"))
    stl_name = Path(str(cfg.get("terrain_stl", "terrain.stl"))).name
    paths = {
        "definition": case_dir / str(cfg.get("definition_name", f"{case_name}_Def.xml")),
        "runner": case_dir / str(
            cfg.get("runner_definition_name", f"{case_name}_runner_Def.xml")
        ),
        "summary": case_dir / str(cfg.get("summary_name", "terrain_case_summary.json")),
        "stl_copy": case_dir
        / str(cfg.get("terrain_output_subdir", "terrain"))
        / stl_name,
    }
    # The gate-motion sidecar only exists when the breach gate is enabled.
    if cfg.get("breach_enabled", True):
        paths["motion"] = case_dir / f"{case_name}_gate_motion.txt"
    return paths


def case_simulation_params(scenario: Scenario) -> dict[str, Any]:
    """Simulation parameters as configured by the (merged) case config."""
    cfg = merged_case_config(scenario)
    keys = ("case_name", "particle_spacing", "simulation_time", "time_out",
            "breach_enabled", "breach_width", "breach_time",
            "reservoir_length", "reservoir_min_water_depth")
    return {k: cfg[k] for k in keys if k in cfg}


# ---------------------------------------------------------------------------
# Subprocess helper (post-processing scripts)
# ---------------------------------------------------------------------------

def _child_env() -> dict[str, str]:
    """Subprocess environment: force UTF-8 stdio so status glyphs (→, ✓) do
    not crash child scripts under the Windows cp1252 locale."""
    import os

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_subprocess(ctx: RunContext, argv: list[str], what: str,
                   cwd: Path | None = None) -> None:
    cmd_display = " ".join(f'"{a}"' if " " in a else a for a in argv)
    ctx.commands.append(cmd_display)
    ctx.reporter.note(f"$ {cmd_display}")
    proc = subprocess.run(
        argv,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
    )
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    for line in output.splitlines():
        ctx.reporter.line(f"    {line}")
    if proc.returncode != 0:
        tail = "\n".join(output.splitlines()[-15:])
        raise RuntimeError(
            f"{what} exited with code {proc.returncode}\n{tail}"
        )


# ---------------------------------------------------------------------------
# Step implementations (heavy imports deferred until execution)
# ---------------------------------------------------------------------------

def _do_clip(ctx: RunContext, source: Path, out_dem: Path,
             width_km: float, height_km: float, lon: float, lat: float) -> None:
    ensure_backend_on_path()
    from app.services.terrain.clip import TerrainClipper

    out_dem.parent.mkdir(parents=True, exist_ok=True)
    TerrainClipper(source).clip(
        longitude=lon, latitude=lat,
        width_km=width_km, height_km=height_km,
        output_path=out_dem,
    )


def _do_condition(ctx: RunContext, dem: Path, out_tif: Path, out_png: Path | None,
                  max_fill_depth: float, lon: float, lat: float) -> None:
    ensure_backend_on_path()
    from app.services.terrain.condition import TerrainConditioner

    out_tif.parent.mkdir(parents=True, exist_ok=True)
    TerrainConditioner(dem).condition(
        max_fill_depth=max_fill_depth,
        out_tif=out_tif,
        out_png=out_png,
        dam_lon=lon,
        dam_lat=lat,
    )


def _do_flow(ctx: RunContext, dem: Path, out_tif: Path, out_png: Path | None,
             lon: float, lat: float) -> None:
    ensure_backend_on_path()
    from app.services.terrain.flow import FlowAnalyzer

    out_tif.parent.mkdir(parents=True, exist_ok=True)
    FlowAnalyzer(dem).analyze(
        longitude=lon, latitude=lat,
        out_img=out_png,
        out_acc_tif=out_tif,
    )


def _do_domain(ctx: RunContext, dem: Path, out_tif: Path, out_png: Path | None,
               corridor_width_km: float, lon: float, lat: float) -> None:
    ensure_backend_on_path()
    from app.services.terrain.domain import DomainPreparer

    out_tif.parent.mkdir(parents=True, exist_ok=True)
    DomainPreparer(dem).prepare(
        longitude=lon, latitude=lat,
        corridor_width_km=corridor_width_km,
        out_tif=out_tif,
        out_png=out_png,
    )


def _do_sph_terrain(ctx: RunContext, dem: Path, domain: Path,
                    out_npz: Path, out_csv: Path | None, out_png: Path | None,
                    lon: float, lat: float,
                    target_resolution_m: float, scale: float) -> None:
    ensure_backend_on_path()
    from app.services.terrain.sph_terrain import SPHTerrainPreparer

    out_npz.parent.mkdir(parents=True, exist_ok=True)
    SPHTerrainPreparer(dem, domain).prepare(
        dam_longitude=lon,
        dam_latitude=lat,
        target_resolution_m=target_resolution_m,
        scale=scale,
        out_npz=out_npz,
        out_csv=out_csv,
        out_png=out_png,
    )


def _do_mesh(ctx: RunContext, npz: Path, stl: Path, preview_png: Path | None,
             max_edge_m: float) -> None:
    ensure_backend_on_path()
    from app.services.terrain.mesh import TerrainMeshBuilder

    stl.parent.mkdir(parents=True, exist_ok=True)
    TerrainMeshBuilder(npz).build(
        output_stl=stl,
        preview_png=preview_png,
        max_edge_m=max_edge_m,
    )


def _do_case(ctx: RunContext) -> None:
    scenario, run_dir = ctx.scenario, ctx.run_dir
    cfg_path = effective_case_config_path(scenario, run_dir)
    if scenario.case["overrides"]:
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            json.dumps(merged_case_config(scenario), indent=2) + "\n",
            encoding="utf-8",
        )

    generator = scenario.case["generator"]
    ctx.commands.append(f"generate_case(config_path={rel(cfg_path)})")
    spec = importlib.util.spec_from_file_location("hydro_twin_case_generator", generator)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load case generator: {rel(generator)}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves annotations via sys.modules.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise

    summary = module.generate_case(config_path=cfg_path)
    validation = summary.get("validation", {})
    if validation.get("status") != "pass":
        raise RuntimeError(
            "Generated case failed validation: "
            + json.dumps(validation, default=str)[:600]
        )
    for key in ("definition", "runner", "summary"):
        produced = case_output_paths(scenario).get(key)
        if produced is not None and not produced.exists():
            raise RuntimeError(
                f"Case generator did not produce expected file: {rel(produced)}"
            )


def _do_geometry_preview(ctx: RunContext) -> None:
    scenario, run_dir = ctx.scenario, ctx.run_dir
    script = scenario.case["preview_script"]
    if script is None or not script.exists():
        raise RuntimeError(
            f"Geometry preview script not found: {rel(script) if script else '(unset)'}"
        )
    outputs = case_output_paths(scenario)
    preview = scenario.case["preview_outputs"]
    argv = [
        sys.executable, str(script),
        "--config", str(effective_case_config_path(scenario, run_dir)),
        "--summary", str(outputs["summary"]),
        "--xml", str(outputs["runner"]),
        "--output-png", str(preview["png"]),
        "--output-vtk", str(preview["vtk"]),
        "--output-json", str(preview["json"]),
    ]
    preview["png"].parent.mkdir(parents=True, exist_ok=True)
    run_subprocess(ctx, argv, "Case geometry preview")


def _do_simulation(ctx: RunContext) -> None:
    scenario, run_dir = ctx.scenario, ctx.run_dir
    ensure_backend_on_path()
    from app.services.sph.runner import SPHRunner

    runner_xml = case_output_paths(scenario)["runner"]
    ctx.commands.append(f"SPHRunner({rel(runner_xml)}, runs_dir={rel(run_dir)})")
    try:
        ctx.sim_result = SPHRunner(ctx.dsp_root).run(runner_xml, run_dir)
    except Exception as exc:
        raise RuntimeError(_with_log_tail(run_dir, exc)) from exc


def _with_log_tail(run_dir: Path, exc: BaseException) -> str:
    message = f"{type(exc).__name__}: {exc}"
    logs = sorted(
        (p for p in run_dir.glob("*.log") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if logs:
        try:
            tail = logs[0].read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()[-15:]
            message += f"\n--- last lines of {rel(logs[0])} ---\n" + "\n".join(tail)
        except OSError:
            pass
    return message


def _resolve_sim_dirs(ctx: RunContext) -> tuple[Path, Path]:
    """Simulation output dir for post-processing: this run, else latest.json."""
    if ctx.sim_result:
        out = Path(ctx.sim_result["output_directory"])
        return out, out.parent

    pointer = ctx.scenario.latest_run_pointer
    if not pointer.exists():
        raise RuntimeError(
            "No simulation output available for post-processing. "
            "Run a full run or '--step simulation' first "
            f"(missing {rel(pointer)})."
        )
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
        out = Path(data["sim_output"])
    except Exception as exc:
        raise RuntimeError(f"Cannot read {rel(pointer)}: {exc}") from exc
    if not out.exists():
        raise RuntimeError(
            f"Recorded simulation output no longer exists: {rel(out)}. "
            "Re-run '--step simulation' first."
        )
    return out, out.parent


def _do_postprocess(ctx: RunContext, entry: dict[str, Any]) -> None:
    sim_output, sim_run_dir = _resolve_sim_dirs(ctx)
    replacements = {
        "{run_dir}": str(ctx.run_dir),
        "{sim_run_dir}": str(sim_run_dir),
        "{sim_output}": str(sim_output),
    }
    args: list[str] = []
    for raw in entry["args"]:
        resolved = raw
        for token, value in replacements.items():
            resolved = resolved.replace(token, value)
        args.append(resolved)

    script = entry["script"]
    if not script.exists():
        raise RuntimeError(f"Post-processing script not found: {rel(script)}")
    argv = [sys.executable, str(script), *args]

    cmd_display = " ".join(f'"{a}"' if " " in a else a for a in argv)
    ctx.commands.append(cmd_display)
    cwd = entry["cwd"] or REPO_ROOT
    ctx.reporter.note(f"$ {cmd_display}   (cwd: {rel(cwd)})")
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
    )
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    for line in output.splitlines():
        ctx.reporter.line(f"    {line}")
    if proc.returncode != 0:
        tail = "\n".join(output.splitlines()[-15:])
        raise RuntimeError(f"{entry['name']} exited with code {proc.returncode}\n{tail}")


# ---------------------------------------------------------------------------
# Plan assembly
# ---------------------------------------------------------------------------

def build_plan(scenario: Scenario, run_dir: Path) -> list[Step]:
    """All steps for a scenario, in execution order (every stage)."""
    steps: list[Step] = []
    t = scenario.terrain
    out = scenario.outputs
    lon, lat = scenario.longitude, scenario.latitude
    dem = scenario.inputs["dem"]

    if scenario.clip:
        clip = scenario.clip
        steps.append(Step(
            id="clip",
            stage="terrain",
            label="Clip source DEM",
            params={
                "source_dem": str(clip["source_dem"]),
                "width_km": clip["width_km"],
                "height_km": clip["height_km"],
                "longitude": lon,
                "latitude": lat,
            },
            inputs=[clip["source_dem"]],
            outputs=[dem],
            run=lambda ctx, c=clip: _do_clip(
                ctx, c["source_dem"], dem, c["width_km"], c["height_km"], lon, lat
            ),
        ))

    cond = t["condition"]
    steps.append(Step(
        id="condition",
        stage="terrain",
        label="Condition DEM",
        params={
            "max_fill_depth": cond["max_fill_depth"],
            "dam_longitude": lon,
            "dam_latitude": lat,
        },
        inputs=[dem],
        outputs=[out["conditioned"]]
        + ([out["conditioned_preview"]] if cond["preview"] else []),
        run=lambda ctx: _do_condition(
            ctx, dem, out["conditioned"],
            out["conditioned_preview"] if cond["preview"] else None,
            cond["max_fill_depth"], lon, lat,
        ),
    ))

    flow = t["flow"]
    steps.append(Step(
        id="flow",
        stage="terrain",
        label="Generate flow accumulation",
        params={"dam_longitude": lon, "dam_latitude": lat},
        inputs=[out["conditioned"]],
        outputs=[out["flow_tif"]] + ([out["flow_preview"]] if flow["preview"] else []),
        run=lambda ctx: _do_flow(
            ctx, out["conditioned"], out["flow_tif"],
            out["flow_preview"] if flow["preview"] else None, lon, lat,
        ),
    ))

    dom = t["domain"]
    steps.append(Step(
        id="domain",
        stage="terrain",
        label="Generate flood domain",
        params={
            "dam_longitude": lon,
            "dam_latitude": lat,
            "corridor_width_km": dom["corridor_width_km"],
        },
        inputs=[out["conditioned"]],
        outputs=[out["domain_tif"]] + ([out["domain_preview"]] if dom["preview"] else []),
        run=lambda ctx: _do_domain(
            ctx, out["conditioned"], out["domain_tif"],
            out["domain_preview"] if dom["preview"] else None,
            dom["corridor_width_km"], lon, lat,
        ),
    ))

    sph = t["sph_terrain"]
    steps.append(Step(
        id="sph_terrain",
        stage="sph",
        label="Generate SPH terrain (NPZ)",
        params={
            "dam_longitude": lon,
            "dam_latitude": lat,
            "target_resolution_m": sph["target_resolution_m"],
            "scale": sph["scale"],
            "csv": sph["csv"],
        },
        inputs=[out["conditioned"], out["domain_tif"]],
        outputs=[out["sph_npz"]]
        + ([out["sph_csv"]] if sph["csv"] else [])
        + ([out["sph_preview"]] if sph["preview"] else []),
        run=lambda ctx: _do_sph_terrain(
            ctx, out["conditioned"], out["domain_tif"],
            out["sph_npz"],
            out["sph_csv"] if sph["csv"] else None,
            out["sph_preview"] if sph["preview"] else None,
            lon, lat, sph["target_resolution_m"], sph["scale"],
        ),
    ))

    mesh = t["mesh"]
    steps.append(Step(
        id="mesh",
        stage="sph",
        label="Generate terrain STL",
        params={"max_edge_m": mesh["max_edge_m"]},
        inputs=[out["sph_npz"]],
        outputs=[out["stl"]] + ([out["mesh_preview"]] if mesh["preview"] else []),
        run=lambda ctx: _do_mesh(
            ctx, out["sph_npz"], out["stl"],
            out["mesh_preview"] if mesh["preview"] else None,
            mesh["max_edge_m"],
        ),
    ))

    case_paths = case_output_paths(scenario)
    steps.append(Step(
        id="case",
        stage="sph",
        label="Generate DualSPHysics case",
        params={
            "generator": str(scenario.case["generator"]),
            "config": str(scenario.case["config"]),
            "overrides": scenario.case["overrides"],
        },
        inputs=[out["sph_npz"], out["stl"], scenario.case["config"],
                scenario.case["generator"]],
        outputs=list(case_paths.values()),
        run=lambda ctx: _do_case(ctx),
    ))

    if scenario.case["geometry_preview"]:
        preview = scenario.case["preview_outputs"]
        steps.append(Step(
            id="geometry_preview",
            stage="sph",
            label="Case geometry preview",
            params={"script": str(scenario.case["preview_script"])},
            inputs=[case_paths["summary"], case_paths["runner"]],
            outputs=[preview["png"], preview["vtk"], preview["json"]],
            run=lambda ctx: _do_geometry_preview(ctx),
        ))

    if scenario.simulation["enabled"]:
        steps.append(Step(
            id="simulation",
            stage="simulation",
            label="Run GenCase + DualSPHysics + PartVTK",
            params={"runner_xml": str(case_paths["runner"])},
            inputs=[case_paths["runner"]],
            outputs=[],  # per-run output directory; never skipped
            run=lambda ctx: _do_simulation(ctx),
            skippable=False,
        ))

        for index, entry in enumerate(scenario.postprocess):
            steps.append(Step(
                id=f"postprocess:{index}:{entry['name']}",
                stage="postprocess",
                label=entry["name"],
                params={"script": str(entry["script"]), "args": entry["args"],
                        "cwd": str(entry["cwd"]) if entry["cwd"] else None},
                inputs=[entry["script"]],
                outputs=[],
                run=lambda ctx, e=entry: _do_postprocess(ctx, e),
                skippable=False,
            ))

    return steps


def select_steps(plan: list[Step], stages: list[str]) -> list[Step]:
    return [s for s in plan if s.stage in stages]
