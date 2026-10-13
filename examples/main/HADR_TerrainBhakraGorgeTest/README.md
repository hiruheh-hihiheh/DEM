# HADR Terrain — Experimental DualSPHysics Terrain Integration Case

This is an experimental terrain-import integration case.

It is separate from the validated synthetic dam-break prototype.

The concrete case name, generated XML filenames, summary name and terrain
paths are read from `terrain_case_config.json` in this directory, so the same
generator and helper scripts work for any generated site case. The committed
example is `HADR_TerrainChouldari`; the site-onboarding API writes
site-specific copies such as `HADR_TerrainBhakraRepairTest`.

## Purpose

The first objective is only to prove that DualSPHysics can:

1. Load the real DEM-derived terrain STL.
2. Create fixed boundary particles from that STL.
3. Create a small reservoir fluid volume above the terrain.
4. Advance a short simulation on GPU.
5. Produce VTK output.

This is **not** the final SIH flood model.

## Terrain source

- Source DEM: Copernicus GLO-30 DSM
- Intermediate terrain NPZ: the `terrain_npz` path in `terrain_case_config.json`
- Terrain mesh STL: the `terrain_stl` path in `terrain_case_config.json`
- Numerical prototype scale: stored in the NPZ, currently approximately `0.02`

The STL coordinates are already in scaled simulation coordinates:

- `x_sim`
- `y_sim`
- `z_sim`

No additional scaling is applied by this case generator.

## Generated files

Run the generator to create (names come from `terrain_case_config.json`):

```text
<terrain_output_subdir>/<case_name>_sph_terrain.stl
<case_name>_Def.xml
<case_name>_runner_Def.xml
<summary_name>
```

Helper scripts in this directory:

- `preview_terrain_case.py` — reads this case's `terrain_case_summary.json`
  and `definition_name` XML and renders a geometry debug preview (PNG/VTK/JSON).
- `analyze_particle_outflow.py` — reads this case's solver `.out` / `solver.log`
  and VTK particles; `--out-dir` selects which run directory is inspected.
- `run_standalone_case.bat` — reads `case_name` from the config, then runs
  GenCase, DualSPHysics and PartVTK for this case.
- `run_with_backend_runner.py` — runs the generated `runner_definition_name`
  XML through the backend `SPHRunner`.

None of these scripts run GenCase or DualSPHysics unless explicitly invoked,
and none of them modify the terrain, the generated geometry or the XML.

## Validation status

This is a debug/experimental integration case. The dam, reservoir, breach and
flood geometry are terrain-anchored but are **not** a physically validated
model. See `terrain_case_summary.json` (validation status and messages) after
running the generator.
