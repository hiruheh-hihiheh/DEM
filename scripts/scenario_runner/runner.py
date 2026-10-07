"""Scenario run orchestration: validate → (clean) → execute → summarize."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import env as env_mod
from .config import (
    REPO_ROOT,
    Scenario,
    ScenarioError,
    load_scenario,
    rel,
)
from .env import EnvError, resolve_dualsphysics_root
from .manifest import Manifest
from .report import Reporter
from .steps import (
    STAGES,
    RunContext,
    Step,
    build_plan,
    case_simulation_params,
    select_steps,
)

_MODULE_LABELS = {
    "rasterio": "Rasterio",
    "numpy": "NumPy",
    "matplotlib": "Matplotlib",
}

_STAGE_TITLES = {
    "terrain": "Terrain",
    "sph": "SPH terrain & case",
    "simulation": "Simulation",
    "postprocess": "Post-processing",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resolve_stages(scenario: Scenario, step: str | None) -> list[str]:
    if step:
        if step in ("simulation", "postprocess") and not scenario.simulation["enabled"]:
            raise ScenarioError(
                f"Scenario '{scenario.name}' has simulation disabled "
                f"('simulation.enabled': false); --step {step} is unavailable."
            )
        return [step]
    if scenario.simulation["enabled"]:
        return list(STAGES)
    return ["terrain", "sph"]


def _make_run_dir(scenario: Scenario) -> tuple[str, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"run_{stamp}"
    run_dir = scenario.runs_root / run_id
    counter = 2
    while run_dir.exists():
        run_id = f"run_{stamp}_{counter}"
        run_dir = scenario.runs_root / run_id
        counter += 1
    return run_id, run_dir


def _nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(
    reporter: Reporter,
    scenario: Scenario,
    full_plan: list[Step],
    planned: list[Step],
    stages: list[str],
    run_dir: Path,
    dsp: Any,
    dsp_error: EnvError | None,
) -> list[str]:
    errors: list[str] = []
    reporter.banner("HYDRO TWIN SCENARIO CHECK")

    reporter.field("Scenario:", scenario.display_name, f"({scenario.name})")

    # --- inputs -----------------------------------------------------------
    dem = scenario.inputs["dem"]
    produced = {p for s in planned for p in s.outputs}
    dem_needed = any(dem in s.inputs for s in planned)
    dem_produced_by_plan = dem in produced

    if dem.exists():
        reporter.field("DEM:", "FOUND", rel(dem))
        if dem_needed and ("terrain" in stages or "sph" in stages):
            try:
                import rasterio  # noqa: F401  (presence already reported below)

                with rasterio.open(dem) as src:
                    if src.crs is None:
                        reporter.field("DEM:", "INVALID", "no CRS defined")
                        errors.append(f"DEM has no CRS defined: {rel(dem)}")
                    else:
                        reporter.note(f"CRS {src.crs} · {src.width}x{src.height} px")
            except ImportError:
                pass
            except Exception as exc:
                reporter.field("DEM:", "UNREADABLE", str(exc))
                errors.append(f"DEM cannot be read: {rel(dem)} ({exc})")
    elif dem_produced_by_plan:
        reporter.field("DEM:", "PENDING", f"will be created by this run: {rel(dem)}")
    elif dem_needed:
        reporter.field("DEM:", "MISSING", rel(dem))
        errors.append(
            f"Input DEM not found: {rel(dem)} — place the site DEM there "
            "(or configure 'clip' in the scenario to generate it)."
        )
    else:
        reporter.field("DEM:", "n/a", "(not needed for the selected steps)")

    if scenario.clip:
        source = scenario.clip["source_dem"]
        status = "FOUND" if source.exists() else "MISSING"
        reporter.field("Source DEM:", status, rel(source))
        if not source.exists():
            errors.append(f"Clip source DEM not found: {rel(source)}")

    secondary = scenario.inputs.get("secondary_dem")
    if secondary is not None:
        reporter.field("Secondary DEM:", "FOUND" if secondary.exists() else "MISSING",
                       rel(secondary) + " (optional)")

    dam_label = scenario.dam_name or "custom coordinates"
    dam_extra = f" ({scenario.dam_id})" if scenario.dam_id else ""
    reporter.field(
        "Dam:",
        "FOUND",
        f"{dam_label}{dam_extra} at {scenario.longitude:.6f}, {scenario.latitude:.6f}",
    )

    # --- interpreter / modules ---------------------------------------------
    needs_terrain_modules = bool({"terrain", "sph"} & set(stages))
    needed_modules: list[str] = []
    if needs_terrain_modules:
        needed_modules = ["rasterio", "numpy", "matplotlib"]
    elif "postprocess" in stages:
        needed_modules = ["numpy"]

    reporter.field("Python:", "OK", env_mod.python_version())
    if needed_modules:
        for module, ok in env_mod.check_modules(tuple(needed_modules)):
            label = _MODULE_LABELS.get(module, module)
            reporter.field(f"{label}:", "OK" if ok else "MISSING",
                           "" if ok else f"pip install {module}")
            if not ok:
                errors.append(
                    f"Required Python module '{module}' is not available. "
                    f"Install the GIS stack: pip install -r backend/requirements.txt"
                )

    # --- DualSPHysics case files --------------------------------------------
    if "sph" in stages:
        generator = scenario.case["generator"]
        config = scenario.case["config"]
        reporter.field("Case generator:", "FOUND" if generator.exists() else "MISSING",
                       rel(generator))
        if not generator.exists():
            errors.append(f"Case generator not found: {rel(generator)}")
        reporter.field("Case config:", "FOUND" if config.exists() else "MISSING",
                       rel(config))
        if not config.exists():
            errors.append(f"Case config not found: {rel(config)}")
        if scenario.case["geometry_preview"]:
            preview_script = scenario.case["preview_script"]
            if preview_script is None or not preview_script.exists():
                reporter.field("Preview script:", "MISSING",
                               rel(preview_script) if preview_script else "(unset)")
                errors.append(
                    "case.geometry_preview is enabled but the preview script is "
                    f"missing: {rel(preview_script) if preview_script else '(unset)'}"
                )

    # --- DualSPHysics installation --------------------------------------------
    if "simulation" in stages:
        if dsp_error is not None:
            reporter.field("DualSPHysics:", "NOT SET", "DUALSPHYSICS_ROOT not configured")
            errors.append(str(dsp_error))
        else:
            reporter.field("DualSPHysics:", "FOUND", str(dsp.root))
            reporter.note(f"resolved from: {dsp.source}")
            for label, path in env_mod.binary_paths(dsp.root):
                ok = path.exists()
                reporter.field(f"{label}:", "FOUND" if ok else "MISSING", str(path))
                if not ok:
                    errors.append(f"{label} executable not found: {path}")

    # --- generic upstream inputs for the planned steps -------------------------
    owner: dict[Path, str] = {}
    for step in full_plan:
        for output in step.outputs:
            owner.setdefault(output, step.stage)

    for step in planned:
        for inp in step.inputs:
            if inp in produced or inp == dem or inp.exists():
                continue
            source_stage = owner.get(inp)
            hint = (
                f"generate it first: --step {source_stage} (or a full run)"
                if source_stage
                else "check the scenario configuration / input files"
            )
            reporter.field(f"Input ({step.label}):", "MISSING", rel(inp))
            errors.append(f"Missing input for '{step.label}': {rel(inp)} — {hint}")

    if "postprocess" in stages and "simulation" not in stages:
        pointer = scenario.latest_run_pointer
        if not pointer.exists():
            reporter.field("Previous run:", "MISSING", rel(pointer))
            errors.append(
                "Post-processing needs a simulation output, but no previous run "
                "is recorded. Run '--step simulation' (or a full run) first."
            )
        else:
            reporter.field("Previous run:", "FOUND", rel(pointer))

    # --- output directories ------------------------------------------------------
    check_dirs = {run_dir.parent, scenario.manifest_path.parent}
    for step in planned:
        for output in step.outputs:
            check_dirs.add(output.parent)
    unwritable = []
    for directory in sorted(check_dirs, key=str):
        ancestor = _nearest_existing(directory)
        if not ancestor.is_dir() or not os.access(ancestor, os.W_OK):
            unwritable.append(directory)
    if unwritable:
        reporter.field("Output dirs:", "NOT WRITABLE", str(unwritable[0]))
        for directory in unwritable:
            errors.append(
                f"Cannot create output directory {rel(directory)} "
                f"(nearest existing path not writable: {_nearest_existing(directory)})"
            )
    else:
        reporter.field("Output dirs:", "CREATABLE", f"{len(check_dirs)} location(s)")

    reporter.line()
    if errors:
        reporter.field("Environment validation:", "FAILED")
        reporter.line()
        for message in errors:
            for index, line in enumerate(str(message).splitlines()):
                reporter.line(("  ✗ " if index == 0 else "    ") + line)
        reporter.line()
    else:
        reporter.field("Environment validation:", "PASSED")
    return errors


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def _print_dry_run(
    reporter: Reporter,
    scenario: Scenario,
    planned: list[Step],
    stages: list[str],
    run_dir: Path,
    manifest: Manifest,
    dsp: Any,
    force: bool,
    clean: bool,
) -> None:
    reporter.banner("HYDRO TWIN — DRY RUN")
    reporter.kv("Scenario:", f"{scenario.display_name} ({scenario.name})")
    if scenario.description:
        reporter.kv("Description:", scenario.description)
    reporter.kv("Dam:", f"{scenario.dam_name or 'custom'} "
                        f"({scenario.longitude:.6f}, {scenario.latitude:.6f})")
    reporter.kv("Stages:", ", ".join(stages))
    reporter.line()

    reporter.line("INPUT")
    reporter.kv("  DEM:", rel(scenario.inputs["dem"]))
    secondary = scenario.inputs.get("secondary_dem")
    if secondary is not None:
        reporter.kv("  Secondary DEM:",
                    f"{rel(secondary)} ({'found' if secondary.exists() else 'not found'})")
    if scenario.clip:
        reporter.kv("  Clip source:", rel(scenario.clip["source_dem"]))
    reporter.line()

    by_stage: dict[str, list[Step]] = {}
    for step in planned:
        by_stage.setdefault(step.stage, []).append(step)

    step: Step
    for stage in STAGES:
        if stage not in by_stage:
            continue
        title = "PROCESSING" if stage == "terrain" else _STAGE_TITLES[stage].upper()
        if stage == "simulation":
            title = "SIMULATION"
        reporter.line(title + ("" if stage == "terrain" else ""))
        for step in by_stage[stage]:
            fresh = (
                step.skippable
                and not force
                and manifest.is_fresh(step.id, step.params, step.inputs, step.outputs)
            )
            suffix = "  (fresh — will skip)" if fresh else ""
            reporter.mark("skip" if fresh else "plan", f"{step.label}{suffix}")
            if step.outputs:
                reporter.note(f"→ {rel(step.outputs[0])}")
                for extra in step.outputs[1:]:
                    reporter.note(f"  {rel(extra)}")
            elif step.stage == "simulation":
                reporter.note(f"→ {rel(run_dir)}")
        reporter.line()

    if "simulation" in stages:
        reporter.line("SIMULATION PARAMETERS")
        for key, value in case_simulation_params(scenario).items():
            shown = "true" if value is True else "false" if value is False else value
            reporter.kv(f"  {key}:", str(shown))
        reporter.line()

    if "simulation" in stages:
        reporter.line("DUALSPHYSICS")
        reporter.kv("  ROOT:", str(dsp.root) if dsp else "not configured")
        if dsp:
            reporter.kv("  source:", dsp.source)
            for label, path in env_mod.binary_paths(dsp.root):
                reporter.kv(f"  {label}:", "FOUND" if path.exists() else "MISSING")
        reporter.line()

    reporter.line("OUTPUT")
    reporter.kv("  Run directory:", rel(run_dir))
    reporter.kv("  Manifest:", rel(scenario.manifest_path))
    if "postprocess" in stages and scenario.postprocess:
        for entry in scenario.postprocess:
            reporter.kv("  Post-process:", entry["name"])
    if clean:
        reporter.line()
        reporter.line("CLEAN")
        reporter.mark("info", "--clean requested: generated outputs would be "
                              "deleted before the run (see --clean docs); "
                              "raw inputs are never touched.")
    reporter.line()
    reporter.mark("ok", "DRY RUN COMPLETE — no processing was executed")


# ---------------------------------------------------------------------------
# --clean
# ---------------------------------------------------------------------------

def _clean(reporter: Reporter, scenario: Scenario, full_plan: list[Step],
           run_dir: Path) -> None:
    protected: set[Path] = {scenario.path.resolve(), scenario.case["config"].resolve(),
                            scenario.case["generator"].resolve()}
    if scenario.case["preview_script"] is not None:
        protected.add(scenario.case["preview_script"].resolve())
    if not scenario.clip:
        # Without a clip step the input DEM is raw source data.
        protected.add(scenario.inputs["dem"].resolve())
    secondary = scenario.inputs.get("secondary_dem")
    if secondary is not None:
        protected.add(secondary.resolve())
    if scenario.clip:
        protected.add(scenario.clip["source_dem"].resolve())

    candidates: set[Path] = set()
    for step in full_plan:
        candidates.update(p.resolve() for p in step.outputs)
    candidates.add(scenario.manifest_path.resolve())
    if scenario.latest_run_pointer.exists():
        candidates.add(scenario.latest_run_pointer.resolve())
    run_children: list[Path] = []
    if scenario.runs_root.is_dir():
        for child in scenario.runs_root.iterdir():
            resolved = child.resolve()
            if resolved == run_dir.resolve():
                continue  # never delete the run directory we are about to use
            run_children.append(resolved)
            candidates.add(resolved)

    outside = [p for p in candidates if REPO_ROOT not in p.parents and p != REPO_ROOT]
    if outside:
        raise ScenarioError(
            "Refusing to clean paths outside the repository: "
            + ", ".join(str(p) for p in outside)
        )

    deletable = sorted(
        (p for p in candidates if p.exists() and p not in protected),
        key=str,
    )
    if not deletable:
        reporter.mark("info", "--clean: no generated outputs to delete")
        return

    reporter.mark("info", f"--clean: deleting {len(deletable)} generated item(s):")
    for path in deletable:
        reporter.note(f"delete {rel(path)}")
    for path in deletable:
        if path.is_dir():
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except OSError as exc:
                raise ScenarioError(f"Cannot delete {rel(path)}: {exc}") from exc
    manifest = Manifest.load(scenario.manifest_path)
    manifest.clear()
    reporter.mark("ok", "--clean: generated outputs removed (raw inputs untouched)")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _write_metadata(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n",
                        encoding="utf-8")
    except OSError:
        pass


def _print_summary(
    reporter: Reporter,
    scenario: Scenario,
    results: list[dict[str, Any]],
    run_dir: Path,
    ctx: RunContext,
    duration: float,
    run_id: str,
    status: str,
) -> None:
    reporter.line()
    reporter.banner("HYDRO TWIN — RUN COMPLETE" if status == "success"
                    else "HYDRO TWIN — RUN FAILED")

    reporter.kv("Scenario:", f"{scenario.display_name} ({scenario.name})")
    reporter.kv("Status:", status)
    reporter.line()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(result["stage"], []).append(result)

    for stage, title in _STAGE_TITLES.items():
        if stage not in grouped:
            continue
        reporter.line(f"{title}:")
        for result in grouped[stage]:
            if result["status"] == "executed":
                reporter.mark("ok", f"{result['label']} ({result['duration']:.1f}s)")
            elif result["status"] == "skipped":
                reporter.mark("skip", f"{result['label']} — already up to date, reused")
            else:
                reporter.mark("fail", f"{result['label']} — FAILED")
        reporter.line()

    reporter.line("Outputs:")
    out = scenario.outputs
    for label, path in (
        ("Conditioned DEM", out["conditioned"]),
        ("Flood domain", out["domain_tif"]),
        ("SPH terrain NPZ", out["sph_npz"]),
        ("Terrain STL", out["stl"]),
    ):
        if path.exists():
            reporter.note(f"{label}: {rel(path)}")
    if ctx.sim_result:
        reporter.note(f"Simulation output: {rel(ctx.sim_result['output_directory'])}")
        reporter.note(f"Solver log: {rel(ctx.sim_result['log_file'])}")
    reporter.note(f"Run directory: {rel(run_dir)}")
    reporter.note(f"Run log: {rel(run_dir / 'run.log')}")
    reporter.line()

    minutes, seconds = divmod(int(duration), 60)
    reporter.kv("Run ID:", run_id)
    reporter.kv("Duration:", f"{minutes}m {seconds}s")
    reporter.kv("Finished:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def run(args: Any) -> int:
    reporter = Reporter(debug=bool(getattr(args, "debug", False)))
    scenario: Scenario = load_scenario(args.scenario)
    stages = _resolve_stages(scenario, args.step)
    run_id, run_dir = _make_run_dir(scenario)

    full_plan = build_plan(scenario, run_dir)
    planned = select_steps(full_plan, stages)
    manifest = Manifest.load(scenario.manifest_path)

    dsp = None
    dsp_error: EnvError | None = None
    if "simulation" in stages:
        try:
            dsp = resolve_dualsphysics_root(
                getattr(args, "dualsphysics_root", None),
                scenario.dualsphysics_root,
            )
        except EnvError as exc:
            dsp_error = exc

    errors = _validate(reporter, scenario, full_plan, planned, stages,
                       run_dir, dsp, dsp_error)
    if errors:
        return 1

    if args.dry_run:
        reporter.line()
        _print_dry_run(reporter, scenario, planned, stages, run_dir, manifest,
                       dsp, bool(args.force), bool(args.clean))
        return 0

    # ---- real run -------------------------------------------------------
    run_dir.mkdir(parents=True, exist_ok=True)
    reporter.open_log(run_dir / "run.log")

    header = [
        f"scenario:  {scenario.name} ({scenario.display_name})",
        f"stages:    {', '.join(stages)}",
        f"flags:     step={args.step or 'full'} force={bool(args.force)} "
        f"clean={bool(args.clean)}",
        f"dualsphysics: {str(dsp.root) + ' (from ' + dsp.source + ')' if dsp else 'n/a'}",
        f"python:    {env_mod.python_version()} on {sys.platform}",
        f"repo:      {REPO_ROOT}",
        f"run dir:   {rel(run_dir)}",
    ]
    reporter.run_header(header)

    started_at = _now()
    t_start = time.monotonic()

    try:
        if args.clean:
            _clean(reporter, scenario, full_plan, run_dir)
    except ScenarioError as exc:
        reporter.exception(exc)
        reporter.close_log()
        return 1

    ctx = RunContext(scenario=scenario, run_dir=run_dir, reporter=reporter,
                     dsp_root=dsp.root if dsp else None)

    results: list[dict[str, Any]] = []
    failed_step: str | None = None

    for step in planned:
        fresh = (
            step.skippable
            and not args.force
            and manifest.is_fresh(step.id, step.params, step.inputs, step.outputs)
        )
        if fresh:
            reporter.mark("skip", f"{step.label} — already exists, skipping")
            results.append({"id": step.id, "stage": step.stage, "label": step.label,
                            "status": "skipped", "duration": 0.0})
            continue

        reporter.mark("info", f"{step.label} ...")
        step_start = time.monotonic()
        try:
            step.run(ctx)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            duration = time.monotonic() - step_start
            reporter.exception(exc, context=step.label)
            results.append({"id": step.id, "stage": step.stage, "label": step.label,
                            "status": "failed", "duration": duration})
            failed_step = step.label
            break

        duration = time.monotonic() - step_start
        if step.skippable:
            manifest.record(step.id, step.params, step.inputs, step.outputs)
            manifest.save()
        reporter.mark("ok", f"{step.label} ({duration:.1f}s)")
        results.append({"id": step.id, "stage": step.stage, "label": step.label,
                        "status": "executed", "duration": duration})

    total_duration = time.monotonic() - t_start
    status = "success" if failed_step is None else "failed"

    sim_output = None
    sim_run_dir = None
    if ctx.sim_result:
        sim_output = ctx.sim_result["output_directory"]
        sim_run_dir = str(Path(sim_output).parent)

    metadata = {
        "scenario": scenario.name,
        "display_name": scenario.display_name,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": status,
        "failed_step": failed_step,
        "started_at": started_at,
        "finished_at": _now(),
        "duration_seconds": round(total_duration, 3),
        "stages": stages,
        "steps": results,
        "dualsphysics_root": str(dsp.root) if dsp else None,
        "dualsphysics_source": dsp.source if dsp else None,
        "inputs": {k: str(v) for k, v in scenario.inputs.items()},
        "outputs": {k: str(v) for k, v in scenario.outputs.items()},
        "simulation_params": case_simulation_params(scenario)
        if scenario.simulation["enabled"] else None,
        "simulation_output": sim_output,
        "commands": ctx.commands,
        "python": env_mod.python_version(),
        "platform": sys.platform,
        "log": "run.log",
    }
    _write_metadata(run_dir / "metadata.json", metadata)

    if status == "success":
        latest = {
            "scenario": scenario.name,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "sim_output": sim_output,
            "sim_run_dir": sim_run_dir,
            "finished_at": metadata["finished_at"],
            "status": status,
        }
        _write_metadata(scenario.latest_run_pointer, latest)

    _print_summary(reporter, scenario, results, run_dir, ctx, total_duration,
                   run_id, status)
    reporter.footer()
    reporter.close_log()

    return 0 if status == "success" else 1
