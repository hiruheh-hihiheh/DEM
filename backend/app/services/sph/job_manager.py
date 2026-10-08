"""Web job manager: runs the EXISTING Scenario Runner as a subprocess.

Architecture (nothing is duplicated or rewritten):

    Browser -> FastAPI -> JobManager -> python scripts/run_scenario.py <scenario>
                                          -> terrain / sph / GenCase /
                                             DualSPHysics / PartVTK /
                                             post-processing (unchanged)

The job manager only *observes* the runner: it parses the runner's own
console markers (``→ step ...`` / ``✓ step (1.2s)``) plus the files the
runner produces (run dir, solver frame files) to derive a live stage /
progress model for the UI, and it packages completed output with
``results.process_run``.

State is persisted to ``simulations/jobs/<id>.json`` (status, progress,
metrics) and ``simulations/jobs/<id>.log`` (captured runner stdout), so
the history page survives backend restarts.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .results import load_geo_transform, process_run

REPO_ROOT = Path(__file__).resolve().parents[4]
SCENARIOS_DIR = REPO_ROOT / "scenarios"
JOBS_DIR = REPO_ROOT / "simulations" / "jobs"
RUN_SCENARIO = REPO_ROOT / "scripts" / "run_scenario.py"

# Stage plan for a full pipeline run. Weights drive the overall progress bar.
SIMULATION_STAGES: list[tuple[str, str, float]] = [
    ("validate", "Validating configuration", 0.04),
    ("terrain", "Terrain preparation", 0.10),
    ("sph", "SPH preparation", 0.08),
    ("case", "Case generation", 0.05),
    ("geometry", "Geometry validation", 0.05),
    ("gencase", "GenCase", 0.03),
    ("solver", "DualSPHysics", 0.45),
    ("partvtk", "PartVTK", 0.05),
    ("postprocess", "Post-processing", 0.15),
]

# Stage plan for importing an existing simulation output package.
IMPORT_STAGES: list[tuple[str, str, float]] = [
    ("validate", "Validating package", 0.30),
    ("postprocess", "Processing simulation output", 0.70),
]

# Runner step label -> our stage id.
_STEP_STAGE = {
    "Clip source DEM": "terrain",
    "Condition DEM": "terrain",
    "Generate flow accumulation": "terrain",
    "Generate flood domain": "terrain",
    "Generate SPH terrain (NPZ)": "sph",
    "Generate terrain STL": "sph",
    "Generate DualSPHysics case": "case",
    "Case geometry preview": "geometry",
    "Run GenCase + DualSPHysics + PartVTK": "simulation",
}

_STEP_RE = re.compile(r"^(✓|↷|✗|→)\s(.+)$")
_RUN_DIR_RE = re.compile(r"run dir:\s+(\S+)")
_DERIVED_SCENARIO_RE = re.compile(r"^.+_web_[0-9a-f]{8}$")

_TOTAL_SIM_WEIGHT = sum(w for _, _, w in SIMULATION_STAGES)


class JobError(RuntimeError):
    """Raised for job-creation conflicts / invalid requests."""


class JobConflict(JobError):
    """Raised when a job cannot start because another is active."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _job_id(prefix: str = "job") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# scenario derivation (parameterised runs reuse the scenario-file mechanism)
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scenario_catalog() -> list[dict[str, Any]]:
    """Scenarios available to the UI, with their effective parameters."""
    from scenario_runner.config import (  # noqa: sys.path prepared below
        ScenarioError,
        list_scenarios,
        load_scenario,
    )

    catalog: list[dict[str, Any]] = []
    for item in list_scenarios():
        entry: dict[str, Any] = {
            "name": item["name"],
            "display_name": item["display_name"],
            "description": item["description"],
            "ready": False,
            "error": None,
            "dam_id": None,
            "parameters": None,
        }
        try:
            scenario = load_scenario(item["name"])
            overrides = dict(scenario.case["overrides"])
            effective = dict(scenario.case["base_config_data"])
            effective.update(overrides)
            entry.update(
                ready=True,
                dam_id=scenario.dam_id,
                description=scenario.description or entry["description"],
                display_name=scenario.display_name,
                simulation_enabled=bool(scenario.simulation["enabled"]),
                overrides=overrides,
                parameters={
                    key: effective.get(key)
                    for key in (
                        "reservoir_water_depth",
                        "particle_spacing",
                        "simulation_time",
                        "time_out",
                        "breach_width",
                        "breach_time",
                        "reservoir_length",
                        "fluid_bed_clearance",
                    )
                    if key in effective
                },
            )
        except ScenarioError as exc:
            entry["error"] = str(exc)
        except Exception as exc:  # dam inventory missing, etc.
            entry["error"] = f"{type(exc).__name__}: {exc}"
        catalog.append(entry)
    return catalog


def _ensure_scripts_on_path() -> None:
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


_ensure_scripts_on_path()


# ---------------------------------------------------------------------------
# JobManager
# ---------------------------------------------------------------------------

class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._running: str | None = None
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        self._recover()

    # -- persistence --------------------------------------------------------

    def _path(self, job_id: str) -> Path:
        return JOBS_DIR / f"{job_id}.json"

    def _log_path(self, job_id: str) -> Path:
        return JOBS_DIR / f"{job_id}.log"

    def _save(self, job: dict[str, Any]) -> None:
        tmp = self._path(job["id"]).with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(job, indent=2, default=str), encoding="utf-8"
        )
        tmp.replace(self._path(job["id"]))

    def _recover(self) -> None:
        """Load persisted jobs; interrupt anything that was running."""
        for path in sorted(JOBS_DIR.glob("*.json")):
            try:
                job = _load_json(path)
            except Exception:
                continue
            if job.get("status") in ("queued", "running"):
                job["status"] = "failed"
                job["error"] = (
                    "Interrupted: the backend restarted while this job "
                    "was running."
                )
                job["finished_at"] = job.get("finished_at") or _now()
                self._mark_stages_failed(job)
                self._save(job)
            self._jobs[job["id"]] = job

        # Remove derived scenario files left behind by interrupted jobs.
        pattern = re.compile(r".+_web_[0-9a-f]{8}\.json$")
        for path in SCENARIOS_DIR.glob("*_web_*.json"):
            if pattern.match(path.stem):
                try:
                    path.unlink()
                except OSError:
                    pass

    # -- reads --------------------------------------------------------------

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [copy.deepcopy(j) for j in self._jobs.values()]
        jobs.extend(self._discover_runner_runs())
        jobs.sort(
            key=lambda j: str(j.get("created_at") or ""), reverse=True
        )
        return jobs

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return copy.deepcopy(job)
        return self._runner_run_record(job_id)

    def _discover_runner_runs(self) -> list[dict[str, Any]]:
        """Runs launched directly from the CLI (scenarios/<x>/runs/...)."""
        records: list[dict[str, Any]] = []
        known_run_dirs = {
            str(j.get("run_dir"))
            for j in self._jobs.values()
            if j.get("run_dir")
        }
        if not SCENARIOS_DIR.is_dir():
            return records
        for scenario_path in sorted(SCENARIOS_DIR.glob("*.json")):
            stem = scenario_path.stem
            if _DERIVED_SCENARIO_RE.match(stem) or stem == "local":
                continue
            try:
                doc = _load_json(scenario_path)
            except Exception:
                continue
            runs_root = REPO_ROOT / str(doc.get("runs_root", ""))
            if not runs_root.is_dir():
                continue
            for run_dir in sorted(runs_root.glob("run_*"), reverse=True):
                if str(run_dir) in known_run_dirs:
                    continue
                meta_path = run_dir / "metadata.json"
                if not meta_path.exists():
                    continue
                record = self._record_from_metadata(
                    stem, doc, run_dir, meta_path
                )
                if record is not None:
                    records.append(record)
        return records

    def _runner_run_record(self, job_id: str) -> dict[str, Any] | None:
        if "-" not in job_id:
            return None
        scenario, run_id = job_id.split("-", 1)
        run_dir = None
        scenario_path = SCENARIOS_DIR / f"{scenario}.json"
        if scenario_path.exists():
            try:
                doc = _load_json(scenario_path)
                run_dir = REPO_ROOT / str(doc.get("runs_root", "")) / run_id
            except Exception:
                return None
        if run_dir is None or not (run_dir / "metadata.json").exists():
            return None
        try:
            doc = _load_json(scenario_path)
        except Exception:
            return None
        return self._record_from_metadata(
            scenario, doc, run_dir, run_dir / "metadata.json"
        )

    def _record_from_metadata(
        self,
        scenario: str,
        doc: dict[str, Any],
        run_dir: Path,
        meta_path: Path,
    ) -> dict[str, Any] | None:
        try:
            meta = _load_json(meta_path)
        except Exception:
            return None
        job_id = f"{scenario}-{run_dir.name}"
        result_dir = JOBS_DIR / job_id / "result"
        success = meta.get("status") == "success"
        record = {
            "id": job_id,
            "type": "run",
            "source": "runner",
            "status": "completed" if success else "failed",
            "stage": None,
            "stages": [],
            "progress": 1.0 if success else 0.0,
            "scenario": scenario,
            "scenario_display": doc.get("display_name") or scenario,
            "dam_id": doc.get("dam_id"),
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "simulation_output": meta.get("simulation_output"),
            "created_at": meta.get("started_at"),
            "started_at": meta.get("started_at"),
            "finished_at": meta.get("finished_at"),
            "duration_seconds": meta.get("duration_seconds"),
            "parameters": meta.get("simulation_params"),
            "steps": meta.get("steps"),
            "error": (
                f"Step failed: {meta.get('failed_step')}"
                if meta.get("failed_step")
                else None
            ),
            "result": {
                "available": (result_dir / "manifest.json").exists(),
                "path": str(result_dir) if success else None,
            },
        }
        return record

    # -- logs ---------------------------------------------------------------

    def read_log(self, job_id: str, offset: int = 0) -> tuple[list[str], int]:
        path = self._log_path(job_id)
        if not path.exists():
            # CLI runs: fall back to the runner's own run.log.
            job = self.get(job_id)
            if job and job.get("run_dir"):
                run_log = Path(job["run_dir"]) / "run.log"
                if run_log.exists():
                    path = run_log
        if not path.exists():
            return [], offset
        try:
            lines = path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            return [], offset
        offset = max(0, min(offset, len(lines)))
        return lines[offset:], len(lines)

    def append_log(self, job_id: str, line: str) -> None:
        with self._log_path(job_id).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # -- creation -----------------------------------------------------------

    def create_simulation_job(
        self,
        scenario_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            if self._running and self._job(self._running):
                active = self._jobs[self._running]
                if active.get("status") in ("queued", "running"):
                    raise JobConflict(
                        f"Another simulation is already running "
                        f"({active['id']}). Wait for it to finish."
                    )

        _ensure_scripts_on_path()
        from scenario_runner.config import (
            CASE_OVERRIDE_RANGES,
            ScenarioError,
            load_scenario,
        )

        try:
            scenario = load_scenario(scenario_name)
        except ScenarioError as exc:
            raise JobError(str(exc)) from exc

        base_cfg = dict(scenario.case["base_config_data"])
        overrides = dict(scenario.case["overrides"])
        effective = {**base_cfg, **overrides}

        def _apply(key: str, value: float | None) -> None:
            if value is None:
                return
            current = effective.get(key)
            if current is None or abs(float(current) - float(value)) > 1e-9:
                low, high = CASE_OVERRIDE_RANGES.get(key, (-1e18, 1e18))
                if not (low <= float(value) <= high):
                    raise JobError(
                        f"'{key}' = {value} outside valid range [{low}, {high}]"
                    )
                overrides[key] = float(value)
                effective[key] = float(value)

        # Reservoir level is a percentage of the validated fill depth.
        level = float(params.get("reservoir_level", 100))
        reference_depth = float(effective.get("reservoir_water_depth", 0.0))
        if abs(level - 100.0) > 1e-9:
            _apply("reservoir_water_depth", round(reference_depth * level / 100.0, 4))

        _apply("breach_width", params.get("breach_width"))
        _apply("breach_time", params.get("breach_time"))
        _apply("simulation_time", params.get("simulation_time"))
        _apply("particle_spacing", params.get("particle_spacing"))

        validated = not overrides or overrides == dict(scenario.case["overrides"])
        job_id = _job_id()
        derived_name = f"{scenario_name}_web_{job_id.split('_', 1)[1]}"

        doc = _load_json(scenario.path)
        doc["name"] = derived_name
        doc["display_name"] = (
            f"{doc.get('display_name', scenario_name)} (web run)"
        )
        doc["description"] = (
            f"Generated by the Hydro Twin web app for job {job_id}. "
            f"Parameters: {json.dumps(params, sort_keys=True)}"
        )
        doc.setdefault("case", {})["overrides"] = overrides

        (SCENARIOS_DIR / f"{derived_name}.json").write_text(
            json.dumps(doc, indent=2) + "\n", encoding="utf-8"
        )

        sim_time = float(effective.get("simulation_time", 6.0))
        time_out = float(effective.get("time_out", 0.05)) or 0.05
        expected_frames = int(sim_time / time_out + 1e-9) + 1

        case_dir = scenario.case["generator"].parent
        job = self._new_record(
            job_id=job_id,
            job_type="simulation",
            stages=SIMULATION_STAGES,
            scenario=scenario_name,
            scenario_display=scenario.display_name,
            dam_id=scenario.dam_id,
            params=params,
            overrides=overrides,
            validated=validated,
        )
        job.update(
            scenario_file=derived_name,
            expected_frames=expected_frames,
            time_out=time_out,
            runs_root=str(scenario.runs_root),
            geo_npz=str(scenario.outputs.get("sph_npz") or ""),
            dam_summary=str(case_dir / "terrain_case_summary.json"),
            scenario_path=str(scenario.path),
        )

        with self._lock:
            self._jobs[job_id] = job
            self._running = job_id
            self._save(job)

        thread = threading.Thread(
            target=self._execute,
            args=(job_id,),
            name=f"hydro-job-{job_id}",
            daemon=True,
        )
        thread.start()
        return copy.deepcopy(job)

    def create_import_job(
        self, zip_path: Path, original_name: str
    ) -> dict[str, Any]:
        job_id = _job_id("imp")
        job = self._new_record(
            job_id=job_id,
            job_type="import",
            stages=IMPORT_STAGES,
            scenario="import",
            scenario_display=original_name,
            dam_id=None,
            params={"file": original_name},
            overrides={},
            validated=False,
        )
        job["upload_zip"] = str(zip_path)
        with self._lock:
            self._jobs[job_id] = job
            self._save(job)
        thread = threading.Thread(
            target=self._execute_import,
            args=(job_id,),
            name=f"hydro-import-{job_id}",
            daemon=True,
        )
        thread.start()
        return copy.deepcopy(job)

    def _new_record(
        self,
        *,
        job_id: str,
        job_type: str,
        stages: list[tuple[str, str, float]],
        scenario: str,
        scenario_display: str,
        dam_id: str | None,
        params: dict[str, Any],
        overrides: dict[str, Any],
        validated: bool,
    ) -> dict[str, Any]:
        return {
            "id": job_id,
            "type": job_type,
            "source": "web",
            "status": "queued",
            "stage": None,
            "stages": [
                {
                    "id": sid,
                    "label": label,
                    "weight": weight,
                    "status": "pending",
                    "detail": None,
                    "progress": 0.0,
                }
                for sid, label, weight in stages
            ],
            "progress": 0.0,
            "scenario": scenario,
            "scenario_display": scenario_display,
            "dam_id": dam_id,
            "parameters": params,
            "overrides": overrides,
            "validated_config": validated,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "pid": None,
            "run_dir": None,
            "run_id": None,
            "simulation_output": None,
            "frame_progress": None,
            "error": None,
            "result": {"available": False, "path": None, "metrics": None},
        }

    def _job(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.get(job_id)

    # -- execution ----------------------------------------------------------

    def _execute(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "running"
            job["started_at"] = _now()
            self._set_stage(job, "validate", "reading scenario configuration")
            self._save(job)

        env = dict(**__import__("os").environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        # Critical: without this the child block-buffers stdout when it is a
        # pipe and every runner line arrives only at exit — live stage
        # tracking and the frame poller would never see intermediate states.
        env["PYTHONUNBUFFERED"] = "1"

        cmd = [
            sys.executable,
            "-u",
            str(RUN_SCENARIO),
            job["scenario_file"],
        ]
        self.append_log(job_id, f"$ {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except OSError as exc:
            self._fail(job_id, f"Cannot start the scenario runner: {exc}")
            return

        with self._lock:
            job = self._jobs[job_id]
            job["pid"] = proc.pid
            self._save(job)

        poller = threading.Thread(
            target=self._poll_simulation,
            args=(job_id,),
            name=f"hydro-poll-{job_id}",
            daemon=True,
        )
        poller.start()

        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\r")
            self.append_log(job_id, line)
            self._handle_line(job_id, line)
        return_code = proc.wait()

        if return_code != 0:
            self._fail_from_log(job_id, return_code)
            return

        self._finalize(job_id)

    def _handle_line(self, job_id: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["status"] != "running":
                return

            run_dir_match = _RUN_DIR_RE.search(line)
            if run_dir_match and not job.get("run_dir"):
                job["run_dir"] = str(
                    (REPO_ROOT / run_dir_match.group(1)).resolve()
                )

            if "HYDRO TWIN" in line and "RUN COMPLETE" in line:
                # The final report re-prints every step marker; anything
                # after this line must not re-trigger stage transitions.
                job["_summary"] = True
                self._save(job)
                return
            if job.get("_summary"):
                return

            if "HYDRO TWIN SCENARIO CHECK" in line:
                self._set_stage(job, "validate", None)
                self._save(job)
                return

            if "Environment validation:" in line:
                if "PASSED" in line:
                    self._finish_stage(job, "validate", detail="checks passed")
                else:
                    self._set_stage(job, "validate", "FAILED", failed=True)
                self._save(job)
                return

            match = _STEP_RE.match(line.strip())
            if not match:
                return
            icon, text = match.groups()

            if icon == "→":
                label = text.replace(" ...", "").strip()
                self._on_step_start(job, label)
            elif icon == "✓":
                label = _strip_duration(text)
                self._on_step_done(job, label, skipped=False)
            elif icon == "↷":
                label = text.split(" — ")[0].strip()
                self._on_step_done(job, label, skipped=True)
            elif icon == "✗":
                label = text.split(" — ")[0].strip()
                self._on_step_failed(job, label)
            self._save(job)

    def _on_step_start(self, job: dict[str, Any], label: str) -> None:
        stage = _STEP_STAGE.get(label, "postprocess")
        if stage == "simulation":
            # One runner step, three of our stages — begin with GenCase.
            self._set_stage(job, "gencase", "generating case files")
            job["active_step"] = "simulation"
        elif stage == "postprocess":
            self._set_stage(job, "postprocess", label)
        elif stage == "geometry":
            self._set_stage(job, "geometry", label)
        else:
            self._set_stage(job, stage, label)
            job["active_step"] = label

    def _on_step_done(
        self, job: dict[str, Any], label: str, skipped: bool
    ) -> None:
        stage = _STEP_STAGE.get(label, "postprocess")
        suffix = " (reused existing output)" if skipped else None

        if stage == "simulation":
            current = job.get("stage") or "partvtk"
            # The combined runner step (GenCase + DualSPHysics + PartVTK) is
            # done, so every frame exists — the last poll can trail, recount.
            solver_detail = self._final_solver_detail(job)
            detail = suffix
            if detail is None and current == "partvtk":
                detail = "VTK frames written"
            if detail is None and current == "solver":
                detail = solver_detail
            self._finish_stage(job, current, detail=detail)
            # The poller usually closed the solver stage a tick early with a
            # stale frame count — refresh it with the final recount.
            solver_entry = self._stage(job, "solver")
            if (
                solver_detail
                and solver_entry is not None
                and solver_entry["status"] == "done"
                and current != "solver"
            ):
                solver_entry["detail"] = solver_detail
            job.pop("active_step", None)
        elif stage == "case":
            self._finish_stage(job, "case", detail=suffix)
            # Geometry validation runs inside the case generator; make it
            # visible as its own stage from here until the preview/sim step.
            self._set_stage(
                job, "geometry", suffix or "case geometry validated"
            )
        elif stage == "geometry":
            self._finish_stage(job, "geometry", detail=suffix)
        elif stage == "simulation-internal":
            pass
        else:
            # terrain / sph / postprocess steps — advance within the stage.
            self._bump_stage_progress(job, stage, suffix or label)

    def _on_step_failed(self, job: dict[str, Any], label: str) -> None:
        stage = _STEP_STAGE.get(label)
        if stage in ("gencase", "solver", "partvtk"):
            stage = job.get("stage")
        if stage and stage != "simulation":
            self._set_stage(job, stage, f"FAILED: {label}", failed=True)

    # -- stage machinery ----------------------------------------------------

    def _stage(self, job: dict[str, Any], stage_id: str) -> dict[str, Any] | None:
        for entry in job["stages"]:
            if entry["id"] == stage_id:
                return entry
        return None

    def _set_stage(
        self,
        job: dict[str, Any],
        stage_id: str,
        detail: str | None,
        failed: bool = False,
    ) -> None:
        current_id = job.get("stage")
        if current_id and current_id != stage_id:
            current = self._stage(job, current_id)
            if current and current["status"] == "active":
                current["status"] = "done"
                current["progress"] = 1.0
        entry = self._stage(job, stage_id)
        if entry is None:
            return
        entry["status"] = "failed" if failed else "active"
        if not failed and entry["status"] != "failed":
            entry["detail"] = detail if detail is not None else entry["detail"]
        elif failed:
            entry["detail"] = detail
        if entry["status"] == "active" and detail is not None:
            entry["detail"] = detail
        job["stage"] = stage_id
        job["stage_started_at"] = _now()
        self._recompute_progress(job)

    def _finish_stage(
        self, job: dict[str, Any], stage_id: str, detail: str | None = None
    ) -> None:
        entry = self._stage(job, stage_id)
        if entry is None or entry["status"] in ("done", "failed"):
            return
        entry["status"] = "done"
        entry["progress"] = 1.0
        if detail:
            entry["detail"] = detail
        if job.get("stage") == stage_id:
            job["stage"] = None
        self._recompute_progress(job)

    def _bump_stage_progress(
        self, job: dict[str, Any], stage_id: str, detail: str | None
    ) -> None:
        entry = self._stage(job, stage_id)
        if entry is None:
            return
        total = self._stage_step_total(job, stage_id)
        if total:
            done = min(total, int(round(entry["progress"] * total)) + 1)
            entry["progress"] = done / total
            entry["detail"] = (
                f"{detail} ({done}/{total})" if detail else f"{done}/{total}"
            )
            if done >= total:
                entry["status"] = "done"
                if job.get("stage") == stage_id:
                    job["stage"] = None
        elif detail:
            entry["detail"] = detail
        self._recompute_progress(job)

    def _stage_step_total(self, job: dict[str, Any], stage_id: str) -> int:
        cached = job.setdefault("_step_totals", {})
        if stage_id in cached:
            return cached[stage_id]
        _ensure_scripts_on_path()
        try:
            from scenario_runner.config import load_scenario

            scenario = load_scenario(job["scenario"])
            totals = {
                "terrain": 3 + (1 if scenario.clip else 0),
                "sph": 2,
                "postprocess": max(1, len(scenario.postprocess)),
            }
        except Exception:
            totals = {"terrain": 3, "sph": 2, "postprocess": 1}
        cached.update(totals)
        return totals.get(stage_id, 1)

    def _recompute_progress(self, job: dict[str, Any]) -> None:
        total = 0.0
        for entry in job["stages"]:
            if entry["status"] in ("done", "failed"):
                total += entry["weight"]
            elif entry["status"] == "active":
                total += entry["weight"] * max(0.0, entry.get("progress") or 0.0)
        job["progress"] = round(min(total, 0.999), 4)

    def _mark_stages_failed(self, job: dict[str, Any]) -> None:
        for entry in job.get("stages", []):
            if entry["status"] == "active":
                entry["status"] = "failed"

    # -- simulation sub-phase polling ---------------------------------------

    def _final_solver_detail(self, job: dict[str, Any]) -> str | None:
        """Frame detail for the solver stage once the step has finished."""
        run_dir = job.get("run_dir")
        if not run_dir:
            return None
        sim_dir = self._find_sim_dir(Path(run_dir))
        if sim_dir is None:
            return None
        data_dir = sim_dir / "HADR_DamBreak_out" / "data"
        if not data_dir.is_dir():
            return None
        done = len(list(data_dir.glob("Part_*.bi4")))
        total = int(job.get("expected_frames") or 0) or done
        time_out = float(job.get("time_out") or 0.05)
        job["frame_progress"] = {"done": done, "total": total}
        t = max(done - 1, 0) * time_out
        return f"frame {min(done, total)}/{total} · t≈{t:.2f}s"

    def _poll_simulation(self, job_id: str) -> None:
        """Watch the run directory to split GenCase / solver / PartVTK."""
        last_signature: tuple | None = None
        while True:
            time.sleep(1.0)
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job["status"] != "running":
                    return
                stage = job.get("stage")
                run_dir = job.get("run_dir")
                if stage not in ("gencase", "solver", "partvtk") or not run_dir:
                    last_signature = None
                    continue

                sim_dir = self._find_sim_dir(Path(run_dir))
                if sim_dir is None:
                    continue
                out_dir = sim_dir / "HADR_DamBreak_out"
                case_xml = out_dir / "HADR_DamBreak.xml"
                solver_log = sim_dir / "solver.log"
                partvtk_log = sim_dir / "partvtk.log"

                if not case_xml.exists():
                    self._set_stage(job, "gencase", "generating case files")
                    signature = ("gencase",)
                elif not solver_log.exists():
                    total = int(job.get("expected_frames") or 0)
                    done = len(list((out_dir / "data").glob("Part_*.bi4"))) \
                        if (out_dir / "data").is_dir() else 0
                    time_out = float(job.get("time_out") or 0.05)
                    t = max(done - 1, 0) * time_out
                    detail = (
                        f"frame {done}/{total} · t≈{t:.2f}s"
                        if total
                        else f"frame {done}"
                    )
                    entry = self._stage(job, "solver")
                    if entry is not None:
                        entry["progress"] = (done / total) if total else 0.0
                    job["frame_progress"] = {"done": done, "total": total}
                    self._set_stage(job, "solver", detail)
                    signature = ("solver", done)
                elif not partvtk_log.exists():
                    self._set_stage(
                        job, "partvtk", "converting particle data to VTK"
                    )
                    signature = ("partvtk",)
                else:
                    signature = ("partvtk-done",)

                if signature != last_signature:
                    self._save(job)
                    last_signature = signature

    @staticmethod
    def _find_sim_dir(run_dir: Path) -> Path | None:
        try:
            children = [
                p for p in run_dir.iterdir()
                if p.is_dir() and re.fullmatch(r"[0-9a-f]{8}", p.name)
            ]
        except OSError:
            return None
        return children[0] if children else None

    # -- completion / failure ------------------------------------------------

    def _fail(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "failed"
            job["error"] = message
            job["finished_at"] = _now()
            if job.get("started_at"):
                job["duration_seconds"] = _elapsed(job["started_at"])
            if job.get("stage"):
                self._set_stage(
                    job, job["stage"], f"FAILED: {message[:120]}", failed=True
                )
            else:
                self._mark_stages_failed(job)
            self._cleanup_scenario_file(job)
            if self._running == job_id:
                self._running = None
            self._save(job)

    def _fail_from_log(self, job_id: str, return_code: int) -> None:
        lines, _ = self.read_log(job_id, 0)
        message = self._extract_error(lines)
        if not message:
            message = f"The scenario runner exited with code {return_code}."
        else:
            message = f"{message} (exit code {return_code})"
        self._fail(job_id, message)

    @staticmethod
    def _extract_error(lines: list[str]) -> str:
        """Pull the most useful error text out of the runner's output."""
        collected: list[str] = []
        capture = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Configuration error:"):
                capture = True
                collected = [stripped]
                continue
            if stripped.startswith("Error:"):
                capture = True
                collected = [stripped]
                continue
            if capture:
                if stripped.startswith("Step:") or stripped.startswith("=" * 10):
                    continue
                if stripped:
                    collected.append(stripped)
                if len(collected) >= 6:
                    break
        return " · ".join(collected[:6])

    def _finalize(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            # Close any stage still open; on a successful run every stage
            # either ran or was not required (e.g. simulation disabled).
            for entry in job["stages"]:
                if entry["status"] == "active":
                    entry["status"] = "done"
                    entry["progress"] = 1.0
                elif entry["status"] == "pending":
                    entry["status"] = "done"
                    entry["progress"] = 1.0
                    entry["detail"] = "not required"
            job["stage"] = None
            if job.get("run_dir"):
                job["run_id"] = Path(job["run_dir"]).name
            self._save(job)

        # Read the runner's metadata for authoritative timings/paths.
        meta_path = None
        if job.get("run_dir"):
            candidate = Path(job["run_dir"]) / "metadata.json"
            if candidate.exists():
                meta_path = candidate
        meta: dict[str, Any] = {}
        if meta_path:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        with self._lock:
            job = self._jobs[job_id]
            job["simulation_output"] = meta.get("simulation_output")
            job["parameters_run"] = meta.get("simulation_params")
            job["steps"] = meta.get("steps")
            if meta.get("duration_seconds") is not None:
                job["duration_seconds"] = meta["duration_seconds"]
            if not job["simulation_output"]:
                # Runner succeeded but the simulation step was skipped
                # (e.g. simulation disabled) — complete without a package.
                self._finish_stage(
                    job, "postprocess", detail="no simulation output"
                )
                job["status"] = "completed"
                job["progress"] = 1.0
                job["finished_at"] = _now()
                if job.get("started_at") and job.get("duration_seconds") is None:
                    job["duration_seconds"] = _elapsed(job["started_at"])
                job["result"] = {
                    "available": False,
                    "path": None,
                    "metrics": None,
                }
                self._cleanup_scenario_file(job)
                if self._running == job_id:
                    self._running = None
                self._save(job)
                return
            self._set_stage(job, "postprocess", "preparing result package")
            self._save(job)

        # Result packaging (part of the post-processing stage).
        try:
            manifest = self._process_result(job_id, meta)
        except Exception as exc:
            self._fail(
                job_id,
                f"Simulation finished, but result processing failed: "
                f"{type(exc).__name__}: {exc}",
            )
            return

        with self._lock:
            job = self._jobs[job_id]
            self._finish_stage(job, "postprocess", detail="result package ready")
            job["status"] = "completed"
            job["progress"] = 1.0
            job["finished_at"] = _now()
            if job.get("started_at") and job.get("duration_seconds") is None:
                job["duration_seconds"] = _elapsed(job["started_at"])
            job["result"] = {
                "available": True,
                "path": str(JOBS_DIR / job_id / "result"),
                "metrics": manifest.get("metrics"),
            }
            self._cleanup_scenario_file(job)
            if self._running == job_id:
                self._running = None
            self._save(job)

    def _process_result(
        self, job_id: str, meta: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            job = dict(self._jobs.get(job_id) or {})
        sim_output = job.get("simulation_output") or meta.get(
            "simulation_output"
        )
        if not sim_output:
            raise RuntimeError("no simulation output directory recorded")

        geo_path = job.get("geo_npz")
        geo = load_geo_transform(Path(geo_path)) if geo_path else None

        dam_summary = None
        summary_path = job.get("dam_summary")
        if summary_path and Path(summary_path).exists():
            try:
                dam_summary = json.loads(
                    Path(summary_path).read_text(encoding="utf-8")
                )
            except Exception:
                dam_summary = None

        time_out = job.get("time_out")
        if time_out is None and meta.get("simulation_params"):
            time_out = meta["simulation_params"].get("time_out")
        return process_run(
            sim_output=Path(sim_output),
            out_dir=JOBS_DIR / job_id / "result",
            source={
                "type": "simulation",
                "job_id": job_id,
                "scenario": job.get("scenario"),
                "dam_id": job.get("dam_id"),
                "run_id": job.get("run_id"),
                "parameters": job.get("parameters"),
                "created_at": job.get("created_at"),
            },
            geo=geo,
            dam_summary=dam_summary,
            time_out=float(time_out) if time_out else None,
        )

    def _cleanup_scenario_file(self, job: dict[str, Any]) -> None:
        name = job.get("scenario_file")
        if name and _DERIVED_SCENARIO_RE.match(name):
            try:
                (SCENARIOS_DIR / f"{name}.json").unlink()
            except OSError:
                pass

    # -- processing existing (CLI) runs -------------------------------------

    def process_runner_run(self, job_id: str) -> dict[str, Any]:
        """Build/refresh the result package for a run launched via the CLI."""
        job = self.get(job_id)
        if job is None:
            raise JobError(f"Simulation not found: {job_id}")
        if job.get("status") != "completed":
            raise JobError(
                f"Simulation {job_id} did not complete successfully."
            )
        sim_output = job.get("simulation_output")
        if not sim_output or not Path(sim_output).exists():
            raise JobError(
                f"Simulation output no longer exists for {job_id}."
            )

        scenario_name = job.get("scenario") or ""
        geo = None
        dam_summary = None
        time_out = None
        try:
            _ensure_scripts_on_path()
            from scenario_runner.config import load_scenario

            scenario = load_scenario(scenario_name)
            geo = load_geo_transform(scenario.outputs.get("sph_npz"))
            generator = scenario.case.get("generator")
            if generator:
                summary = Path(generator).parent / "terrain_case_summary.json"
                if summary.exists():
                    dam_summary = json.loads(
                        summary.read_text(encoding="utf-8")
                    )
            params = job.get("parameters") or {}
            time_out = params.get("time_out")
            if time_out is None:
                effective = dict(scenario.case["base_config_data"])
                effective.update(scenario.case["overrides"])
                time_out = effective.get("time_out")
        except Exception:
            pass

        manifest = process_run(
            sim_output=Path(sim_output),
            out_dir=JOBS_DIR / job_id / "result",
            source={
                "type": "runner_run",
                "scenario": scenario_name,
                "run_id": job.get("run_id"),
                "created_at": job.get("created_at"),
            },
            geo=geo,
            dam_summary=dam_summary,
            time_out=float(time_out) if time_out else None,
        )
        return manifest

    # -- imports -------------------------------------------------------------

    def _execute_import(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            job["status"] = "running"
            job["started_at"] = _now()
            self._set_stage(job, "validate", "unpacking upload")
            self._save(job)

        work = JOBS_DIR / job_id / "upload"
        try:
            zip_path = Path(job["upload_zip"])
            self._safe_extract(zip_path, work)
            vtk_frames = sorted(work.rglob("PartFluid_*.vtk"))
            if not vtk_frames:
                raise JobError(
                    "No PartFluid_*.vtk frames found in the package. "
                    "Include the PartVTK output (particles/PartFluid_*.vtk); "
                    "raw .bi4/.obi4/.ibi4 files are not parsed directly."
                )
            # Validate: first and last frame must parse.
            from .vtk_particles import read_vtk_polydata

            read_vtk_polydata(vtk_frames[0])
            read_vtk_polydata(vtk_frames[-1])

            frames_dir = vtk_frames[0].parent
            if frames_dir.name == "particles":
                # Standard PartVTK layout: <sim_output>/particles/*.vtk
                sim_output = frames_dir.parent
            else:
                # Normalise a flat layout into <sim_output>/particles/*.vtk so
                # process_run sees the directory structure it expects.
                target = frames_dir / "particles"
                target.mkdir(exist_ok=True)
                for frame in vtk_frames:
                    shutil.move(str(frame), str(target / frame.name))
                frames_dir = target
                vtk_frames = sorted(frames_dir.glob("PartFluid_*.vtk"))
                sim_output = frames_dir.parent
            # Pull companion files (Bound VTK, Run.csv, ...) into place so
            # terrain + frame times are available when present.
            for name in (
                "HADR_DamBreak_Bound.vtk",
                "Run.csv",
                "RunPARTs.csv",
            ):
                if (sim_output / name).exists():
                    continue
                matches = list(work.rglob(name))
                if matches:
                    shutil.copy2(matches[0], sim_output / matches[0].name)

            with self._lock:
                job = self._jobs[job_id]
                self._finish_stage(job, "validate", detail=f"{len(vtk_frames)} frames found")
                self._set_stage(job, "postprocess", "reading frames")
                self._save(job)

            manifest = process_run(
                sim_output=sim_output,
                out_dir=JOBS_DIR / job_id / "result",
                source={
                    "type": "import",
                    "file": Path(job["upload_zip"]).name,
                    "frames": len(vtk_frames),
                    "created_at": job.get("created_at"),
                },
            )

            with self._lock:
                job = self._jobs[job_id]
                self._finish_stage(job, "postprocess", detail="result package ready")
                job["status"] = "completed"
                job["progress"] = 1.0
                job["finished_at"] = _now()
                job["duration_seconds"] = _elapsed(job["started_at"])
                job["result"] = {
                    "available": True,
                    "path": str(JOBS_DIR / job_id / "result"),
                    "metrics": manifest.get("metrics"),
                }
                self._save(job)
            zip_path.unlink(missing_ok=True)
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, JobError)
                else f"{type(exc).__name__}: {exc}"
            )
            self._fail(job_id, f"Import failed: {message}")

    @staticmethod
    def _safe_extract(zip_path: Path, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            dest_resolved = dest.resolve()
            for info in archive.infolist():
                target = (dest / info.filename).resolve()
                if not str(target).startswith(str(dest_resolved)):
                    raise JobError(
                        f"Refusing unsafe path in archive: {info.filename}"
                    )
            archive.extractall(dest)


def _strip_duration(text: str) -> str:
    """'Condition DEM (0.3s)' -> 'Condition DEM'."""
    return re.sub(r"\s*\([\d.]+s\)\s*$", "", text).strip()


def _elapsed(started_iso: str) -> float | None:
    try:
        start = datetime.fromisoformat(started_iso)
        return round(
            (datetime.now() - start).total_seconds(), 3
        )
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# module-level singleton
# ---------------------------------------------------------------------------

_manager: JobManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> JobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = JobManager()
        return _manager
