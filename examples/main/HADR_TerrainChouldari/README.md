# HADR Terrain Chouldari — Experimental DualSPHysics Terrain Integration Case

This is an experimental terrain-import integration case.

It is separate from the validated synthetic dam-break prototype.

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
- Intermediate terrain: `data/terrain/chouldari/chouldari_sph_terrain.npz`
- Terrain mesh: `data/terrain/chouldari/chouldari_sph_terrain.stl`
- Numerical prototype scale: stored in the NPZ, currently approximately `0.02`

The STL coordinates are already in scaled simulation coordinates:

- `x_sim`
- `y_sim`
- `z_sim`

No additional scaling is applied by this case generator.

## Generated files

Run the generator to create:

```text
terrain/chouldari_sph_terrain.stl
HADR_TerrainChouldari_Def.xml
HADR_TerrainChouldari_runner_Def.xml
terrain_case_summary.json