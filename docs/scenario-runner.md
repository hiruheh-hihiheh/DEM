# Scenario Runner

`scripts/run_scenario.py` is a **single entry point** for the complete
Chouldari-style workflow — DEM conditioning → flow accumulation → flood domain →
SPH terrain → STL → DualSPHysics case → GenCase → DualSPHysics → PartVTK →
post-processing. It is an *orchestration layer*: every step calls the existing
project components, and the existing individual CLIs keep working unchanged.

```bat
:: Windows
scripts\run_scenario.bat chouldari --dry-run
```

```bash
# Cross-platform (Python 3.11+, same interpreter that has rasterio/numpy/matplotlib)
python scripts/run_scenario.py chouldari --dry-run
python scripts/run_scenario.py chouldari
```

## Quick start (Chouldari)

```bash
python scripts/run_scenario.py list                 # show available scenarios
python scripts/run_scenario.py chouldari --dry-run  # validate + show the plan
python scripts/run_scenario.py chouldari            # run everything
```

The first `--dry-run` prints a validation report. If it fails, it tells you
exactly what to fix (missing DEM, missing DualSPHysics, missing Python
modules, …). Nothing is executed or created during a dry run.

## Stages

| `--step`     | Steps executed                                                                 | Existing components reused |
| ------------ | ------------------------------------------------------------------------------ | -------------------------- |
| `terrain`    | (clip) → condition DEM → flow accumulation → flood domain                      | `backend/app/services/terrain/{clip,condition,flow,domain}.py` |
| `sph`        | SPH terrain NPZ/CSV → STL mesh → DualSPHysics case → geometry preview          | `…/terrain/{sph_terrain,mesh}.py`, `examples/main/<Case>/generate_terrain_case.py`, `preview_terrain_case.py` |
| `simulation` | GenCase → DualSPHysics solver → PartVTK                                        | `backend/app/services/sph/runner.py` (`SPHRunner`) |
| `postprocess`| analysis scripts listed by the scenario                                        | `examples/main/<Case>/analyze_particle_outflow.py` |

A run without `--step` executes all four stages **in order** and stops at the
first failing step (non-zero exit code; nothing downstream is attempted).

Stage dependencies are validated *before* anything runs — e.g. `--step simulation`
without a generated case fails with `Missing input … — generate it first: --step sph`.

> **Chouldari diagnostic notes.** Two pre-existing faults in
> `analyze_particle_outflow.py` were fixed so the post-process step works
> end-to-end: (1) it crashed printing the `➜` verdict on Windows cp1252
> consoles when stdout is captured — child processes now inherit
> `PYTHONIOENCODING=utf-8`; (2) its VTK parser only handled ASCII files, but
> PartVTK writes **legacy BINARY** polydata, so every snapshot showed
> `(parse failed)`. The parser now reads the file's `BINARY`/`ASCII` header
> line and handles both (big-endian floats/doubles via numpy). It is otherwise
> unchanged and still read-only.

## Scenario files

Scenarios live in `scenarios/<name>.json`. Chouldari is `scenarios/chouldari.json`.

Every **relative path** in a scenario file is resolved against the repository root.

```jsonc
{
  "name": "chouldari",                    // must match the file name
  "display_name": "Chouldari",
  "description": "…",

  "dam_id": "AN71MH0003",                 // id from data/dams/raw/dam.geojson (GET /api/dams)
                                          // alternative: explicit "longitude" + "latitude"

  "data_root": "data/terrain/chouldari",  // terrain products + manifest live here
  "runs_root": "simulations/chouldari/runs",

  "inputs": {
    "dem": "data/terrain/chouldari/input/chouldari_5km.tif",
    "secondary_dem": "…/optional.tif"     // optional, never required
  },

  "clip": {                               // OPTIONAL: generate the input DEM from a larger one
    "source_dem": "…/big_dem.tif",
    "width_km": 5.0, "height_km": 5.0
  },

  "terrain": {                            // parameters of the existing terrain CLIs
    "condition":   { "max_fill_depth": 5.0, "preview": true },
    "flow":        { "preview": true },
    "domain":      { "corridor_width_km": 1.0, "preview": true },
    "sph_terrain": { "target_resolution_m": 30.0, "scale": 0.02, "csv": true, "preview": true },
    "mesh":        { "max_edge_m": 1.5, "preview": true },
    "outputs": { /* optional path overrides — see below */ }
  },

  "case": {
    "generator": "examples/main/HADR_TerrainChouldari/generate_terrain_case.py",
    "config": "examples/main/HADR_TerrainChouldari/terrain_case_config.json",
    "overrides": {                       // merged over the case config (overrides win)
      "particle_spacing": 0.1, "simulation_time": 6, "time_out": 0.05,
      "breach_width": 1.0, "breach_time": 0.25
    },
    "geometry_preview": true             // run preview_terrain_case.py after case generation
  },

  "simulation": { "enabled": true },
  "dualsphysics_root": null,              // optional shared install path (see below)

  "postprocess": [
    { "name": "Particle outflow diagnostic",
      "script": "examples/main/HADR_TerrainChouldari/analyze_particle_outflow.py",
      "args": ["--out-dir", "{sim_run_dir}"] }
  ]
}
```

**Real parameters only.** Settings are validated strictly: unknown keys,
out-of-range numbers, and `case.overrides` that do not exist in the base case
config are all rejected before anything runs.

### Output paths

`terrain.outputs` defaults follow the project's existing naming and land in
`data/terrain/<site>/`:

```jsonc
"outputs": {
  "conditioned":        "data/terrain/<site>/processed/<dem>_conditioned.tif",
  "conditioned_preview":"data/terrain/<site>/processed/<dem>_conditioned_comparison.png",
  "flow_tif":           "data/terrain/<site>/processed/<dem>_flow_accumulation.tif",
  "flow_preview":       "data/terrain/<site>/processed/<dem>_flow_preview.png",
  "domain_tif":         "data/terrain/<site>/processed/<dem>_flood_domain.tif",
  "domain_preview":     "data/terrain/<site>/processed/<dem>_flood_domain_preview.png",
  "sph_npz":            "data/terrain/<site>/sph/<name>_sph_terrain.npz",
  "sph_csv":             "data/terrain/<site>/sph/<name>_sph_terrain.csv",
  "sph_preview":         "data/terrain/<site>/sph/<name>_sph_terrain_preview.png",
  "stl":                 "data/terrain/<site>/sph/<name>_sph_terrain.stl",
  "mesh_preview":        "data/terrain/<site>/sph/<name>_sph_terrain_mesh_preview.png"
}
```

Override only the keys you need. The Chouldari scenario relies entirely on
these defaults, which map onto the tracked sample files.

## DUALSPHYSICS_ROOT resolution

Checked **in order**, first hit wins. Nothing is ever written to the Windows
system environment:

1. `--dualsphysics-root PATH` — explicit per-run override
2. `DUALSPHYSICS_ROOT` environment variable
3. `"dualsphysics_root"` in the scenario file
4. `scenarios/local.json` (git-ignored; copy `scenarios/local.example.json`)

If none is set, the run **stops before any processing** with instructions for
all four options. The resolved installation is then validated by checking the
actual executables the workflow runs — not just that the folder exists:

```
GenCase:               …\bin\windows\GenCase_win64.exe
DualSPHysics solver:   …\bin\windows\DualSPHysics5.4_win64.exe
PartVTK:               …\bin\windows\PartVTK_win64.exe
```

These are exactly the binaries `backend/app/services/sph/runner.py` executes.

## Commands

```bash
python scripts/run_scenario.py --help
python scripts/run_scenario.py list                    # or: --list
python scripts/run_scenario.py chouldari --dry-run     # validate + plan only
python scripts/run_scenario.py chouldari               # full run
python scripts/run_scenario.py chouldari --step terrain
python scripts/run_scenario.py chouldari --step sph
python scripts/run_scenario.py chouldari --step simulation
python scripts/run_scenario.py chouldari --step postprocess
python scripts/run_scenario.py chouldari --force       # ignore freshness, re-run steps
python scripts/run_scenario.py chouldari --clean       # delete generated outputs, then run
python scripts/run_scenario.py chouldari --dualsphysics-root "E:/DualSPHysics_v5.4"
python scripts/run_scenario.py chouldari --debug       # full tracebacks
```

Exit codes: `0` success · `1` validation or step failure · `2` usage/config error.

### Skipping fresh steps (`manifest`)

Each executed step records its parameters, input files (path/mtime/size) and
output files (path/mtime/size) in

```
data/terrain/<site>/metadata/manifest.json
```

On the next run a step is skipped when **all** of the following hold:

- its parameters are unchanged,
- every input still has the recorded mtime/size,
- every output still exists with the recorded mtime/size.

So `✓ conditioned DEM already exists — skipping` is printed for up-to-date
artifacts, while an edited config, a replaced DEM, or a deleted output
triggers regeneration. `--force` bypasses the check entirely.

Simulation and post-processing steps are never skipped — each invocation is a
new run.

### `--force` and `--clean`

- `--force` re-runs every selected step regardless of freshness.
- `--clean` prints **each path it will delete**, then removes the scenario's
  generated artifacts: all step outputs, `metadata/manifest.json`, previous run
  directories and `latest.json`.
  **Raw inputs are never deleted**: the input DEM (unless it is itself produced
  by a configured `clip` step), the secondary DEM, the clip source, the
  scenario file, the case config, and the scripts. It also refuses to touch
  anything outside the repository.

## Where outputs go

```
data/terrain/<site>/
├── input/         raw DEM inputs (tracked sample / local-only files)
├── processed/     conditioned DEM, flow accumulation, flood domain, previews
├── sph/           terrain NPZ/CSV/STL + previews
└── metadata/manifest.json     freshness manifest (ignored)

examples/main/<Case>/           case generation output (unchanged behavior):
├── <case>_Def.xml, <case>_runner_Def.xml, <case>_gate_motion.txt,
└── terrain_case_summary.json, terrain/<stl copy>

simulations/<site>/
├── latest.json   pointer to the most recent successful run (used by --step postprocess)
└── runs/run_YYYYMMDD_HHMMSS/
    ├── run.log            everything printed + commands + subprocess output
    ├── metadata.json      scenario, steps, timings, statuses, paths, parameters
    ├── case_config.json   merged case config (only when overrides are set)
    └── <simulation_id>/   SPHRunner working dir
        ├── HADR_DamBreak_Def.xml, gencase.log, solver.log, partvtk.log
        └── HADR_DamBreak_out/   solver output (data/, particles/, *.vtk)
```

`simulations/` and `data/terrain/<site>/{processed,sph,metadata}` are
git-ignored — see the root `.gitignore`.

## Adding a new scenario

1. Prepare the site directory and DEM, e.g.
   `data/terrain/new_site/input/new_site_dem.tif`
   (un-ignore tracked sample inputs in `.gitignore` if they should be committed).
2. Copy `scenarios/chouldari.json` → `scenarios/new_site.json` and edit:
   `name`, `display_name`, `dam_id` (or lon/lat), `data_root`, `runs_root`,
   `inputs.dem`, terrain parameters, case generator/config, postprocess list.
3. Validate: `python scripts/run_scenario.py new_site --dry-run`
4. Run: `python scripts/run_scenario.py new_site`

Notes:

- Dam coordinates are resolved automatically from `data/dams/raw/dam.geojson`
  via the existing backend dam service — only `dam_id` is needed.
- Output paths default to the layout above, so most scenarios do not need a
  `terrain.outputs` block.
- The `case` section points at an existing case-family directory (generator +
  case config). To create a *new* geometry family, copy
  `examples/main/HADR_TerrainChouldari/` and adapt it, then point `case.generator`
  at the copy — the runner loads any generator that exposes
  `generate_case(config_path: Path) -> dict`.
- Machine-specific settings (like your DualSPHysics path) belong in
  `scenarios/local.json`, never in a committed scenario file.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `DUALSPHYSICS_ROOT not configured` | Follow the 4 options printed by the error (env var, flag, scenario, `scenarios/local.json`). |
| `Required Python module 'rasterio' …` | Run with the interpreter that has the GIS stack: `pip install -r backend/requirements.txt`. |
| `Missing input … generate it first: --step terrain` | Stages run in order; run the earlier stage or a full run. |
| `Post-processing needs a simulation output …` | Run `--step simulation` (or a full run) once; `--step postprocess` uses `simulations/<site>/latest.json`. |
| Solver fails inside `SPHRunner` | The error block ends with the tail of `solver.log`/`gencase.log` from the run directory; use `--debug` for full tracebacks. |
| Steps keep re-running unexpectedly | Something in the manifest fingerprint changed — check `metadata/manifest.json` vs. the config/inputs you touched. |
