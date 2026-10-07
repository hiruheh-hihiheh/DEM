# Hydro Twin — DEM / Flood Analysis Platform

Hydro Twin is a dam-safety digital-twin application. It combines:

- a **dam inventory** served as GeoJSON,
- a **DEM / terrain processing pipeline** (clip, condition, flow analysis, flood
  domain, terrain-to-SPH conversion, mesh export),
- **SPH (Smoothed Particle Hydrodynamics) dam-break / flood simulations** driven
  by [DualSPHysics](https://www.dualsphysics.com/), and
- a **React + MapLibre frontend** that visualises dams, terrain and simulation
  results on an interactive map.

The repository is a monorepo: FastAPI backend, React frontend, GIS data and
simulation orchestration live side by side.

---

## Architecture

```
┌──────────────────────┐         HTTP (JSON / GeoJSON)        ┌──────────────────────────┐
│  frontend/           │ ───────────────────────────────────► │  backend/ (FastAPI)      │
│  React + TypeScript  │   http://127.0.0.1:8000/api          │  app/api      routes      │
│  Vite + MapLibre GL  │ ◄─────────────────────────────────── │  app/schemas  models      │
└──────────────────────┘                                      │  app/services business   │
                                                              └───────────┬──────────────┘
                                                                          │
                       ┌──────────────────────────────────────────────────┼──────────────┐
                       │                              │                   │              │
                data/dams/raw/*.geojson      data/terrain/**/*.tif   simulations/runs/  DUALSPHYSICS_ROOT
                 (dam inventory)             (DEM rasters + GIS       (generated SPH     (external solver
                                              products, see data/      outputs, ignored)   install, required
                                              README.md)                                   for /api/simulations)
```

- **API layer** (`backend/app/api/`): HTTP routes only.
- **Business logic** (`backend/app/services/`):
  - `dam_service.py` — loads and normalises the dam GeoJSON (DMS → decimal degrees).
  - `sph/` — scenario building, DualSPHysics XML generation, solver runner.
  - `terrain/` — the GIS pipeline (rasterio + numpy + matplotlib); each module is
    both importable and a standalone CLI.
- **Schemas** (`backend/app/schemas/`): Pydantic request/response models.
- **Frontend** (`frontend/src/`): pages, components (incl. `dam/` digital-twin
  panels and `map/` MapLibre components), API client (`services/api.ts`),
  types, utilities.

---

## Repository layout

```
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routers (dams, simulations)
│   │   ├── schemas/        # Pydantic request/response models
│   │   ├── services/
│   │   │   ├── sph/        # SPH scenario, XML generation, solver runner
│   │   │   └── terrain/    # DEM/GIS pipeline modules (each is a CLI)
│   │   ├── services/dam_service.py
│   │   └── main.py         # FastAPI app entry point
│   ├── requirements.txt
│   └── venv/               # local virtualenv (ignored)
├── frontend/               # Vite + React + TypeScript + MapLibre GL
│   └── src/
│       ├── pages/          # route-level screens (Dashboard)
│       ├── components/     # reusable UI + dam digital-twin panels
│       ├── map/            # FloodMap (MapLibre) components
│       ├── services/       # axios API client
│       ├── types/          # TypeScript models
│       └── utils/          # frontend helpers
├── data/
│   ├── dams/raw/           # dam inventory GeoJSON (tracked, required)
│   └── terrain/            # DEM rasters & generated GIS products (see data/README.md)
├── examples/
│   └── main/HADR_TerrainChouldari/   # experimental DualSPHysics terrain case
├── simulations/
│   ├── templates/          # generated SPH case XML (written at run time)
│   └── runs/               # per-run solver outputs (ignored, large)
├── .env.example            # environment-variable template (names only)
└── README.md
```

---

## Local setup

Prerequisites: **Python 3.11+**, **Node.js 20+**, and (only for simulations)
a local **DualSPHysics** installation.

### 1. Backend (FastAPI)

```powershell
cd backend
python -m venv venv
venv\Scripts\pip install -r requirements.txt     # macOS/Linux: venv/bin/pip
venv\Scripts\python -m uvicorn app.main:app --reload   # run from backend/
```

The server starts on <http://127.0.0.1:8000> (interactive docs at `/docs`).

> Run uvicorn **from the `backend/` directory** — the code imports `app.*`
> relative to that working directory.

### 2. Frontend (Vite + React)

```powershell
cd frontend
npm install
npm run dev          # dev server on http://localhost:5173
```

Other scripts: `npm run build` (type-check + production build),
`npm run lint`.

### 3. Environment variables

Variable **names** only — no secret values exist in this repository:

| Variable           | Purpose                                                              |
| ------------------ | -------------------------------------------------------------------- |
| `DUALSPHYSICS_ROOT`| Absolute path to a local DualSPHysics install. Required by `POST /api/simulations/sph`. |

The backend reads plain environment variables from the shell; it does **not**
load `.env` files automatically (`.env.example` is a template/reference):

```powershell
$env:DUALSPHYSICS_ROOT = "C:\path\to\DualSPHysics"
```

The frontend calls `http://127.0.0.1:8000/api` (see `frontend/src/services/api.ts`);
the backend allows CORS from `http://localhost:5173`.

---

## API

| Method | Route                   | Description                                                     |
| ------ | ----------------------- | --------------------------------------------------------------- |
| GET    | `/`                     | Health/status                                                    |
| GET    | `/api/dams`             | Dam inventory as a GeoJSON `FeatureCollection`                    |
| POST   | `/api/simulations/sph`  | Build + run an SPH dam-break scenario (needs `DUALSPHYSICS_ROOT`) |
| GET    | `/docs`                 | OpenAPI/Swagger UI                                               |

`POST /api/simulations/sph` body: `dam_id`, `scenario`
(`normal|partial|full|extreme`), `reservoir_level` (%), `breach_width` (m),
`breach_time` (s), `simulation_time` (s), `particle_spacing` (m).
The generated case XML goes to `simulations/templates/`, solver output to
`simulations/runs/<simulation_id>/` (both ignored by Git).

---

## GIS / DEM processing

All terrain modules live in `backend/app/services/terrain/` and can be run as
CLIs (each module has `--help`). They require `numpy`, `rasterio` and
`matplotlib` (included in `backend/requirements.txt`):

```powershell
cd backend
python -m app.services.terrain.clip --help
```

| Module                       | Purpose                                                        |
| ---------------------------- | -------------------------------------------------------------- |
| `dem.py`                     | Read and inspect DEM rasters                                    |
| `clip.py`                    | Clip/reproject a DEM to a target area                           |
| `condition.py`               | Hydrological conditioning (fill) of the DEM                     |
| `flow.py`                    | Flow accumulation analysis                                      |
| `domain.py`                  | Flood-domain extraction from the conditioned DEM                |
| `sph_terrain.py`             | Convert a conditioned DEM into SPH terrain points (NPZ/CSV)     |
| `mesh.py`                    | Triangulate terrain points into a surface mesh (STL)            |
| `preview.py`                 | Preview rasters and print terrain statistics                    |
| `analyze_dam_alignment.py`   | Check dam coordinates against the terrain raster                |

### Supported data formats

**Inputs:** GeoTIFF (DEM), GeoJSON (dam inventory).

**GIS outputs produced by the pipeline:** GeoTIFF (conditioned DEM, flow
accumulation, flood domain), NumPy `.npz` (terrain point cloud), STL (terrain
surface mesh), CSV (terrain points), PNG (preview/analysis images).

**Simulation outputs** (under `simulations/runs/<id>/`, ignored): DualSPHysics
`.bi4/.obi4/.ibi4` particle data, legacy VTK (`.vtk`) via PartVTK, XML case
definitions, CSV/OUT run logs.

**Served by the API:** GeoJSON.

> Shapefile/KML export is not currently supported.

---

## Data setup

See [`data/README.md`](data/README.md) for the full breakdown. Summary:

- `data/dams/raw/dam.geojson` — **required, tracked.** The API will not start
  serving dams without it.
- `data/terrain/chouldari/input/chouldari_5km.tif` - **tracked sample DEM**
  (clipped from the Copernicus GLO-30 DSM).
- `data/terrain/chouldari/sph/chouldari_sph_terrain.npz` + `.stl` - **tracked
  intermediates** consumed by the example terrain case; regenerate them with
  `sph_terrain.py` / `mesh.py` if needed.
- Everything else under `data/terrain/` is **generated** (previews, conditioned
  rasters, flow/domain outputs, local experiments) and ignored by Git.
- Real-world DEMs are **not** committed; download Copernicus GLO-30 (or an
  equivalent DEM) for your area of interest and clip it with `clip.py`.

## Running the example simulation case

```powershell
cd examples/main/HADR_TerrainChouldari
python generate_terrain_case.py           # writes the case XML + terrain copies
.\run_standalone_case.bat                 # GenCase + DualSPHysics + PartVTK
# or: python run_with_backend_runner.py   # run through backend/app/services/sph
```

Details: [`examples/main/HADR_TerrainChouldari/README.md`](examples/main/HADR_TerrainChouldari/README.md).

---

## Scenario runner

[`scripts/run_scenario.py`](scripts/run_scenario.py) orchestrates the whole
pipeline (terrain processing → SPH terrain/case generation → DualSPHysics
simulation → post-processing) for any scenario defined under
[`scenarios/*.json`](scenarios/chouldari.json). It reuses the existing
pipeline modules directly — no duplicated business logic.

```powershell
python scripts/run_scenario.py list                  # available scenarios
python scripts/run_scenario.py chouldari --dry-run   # validate without running
python scripts/run_scenario.py chouldari             # full end-to-end run
python scripts/run_scenario.py chouldari --step terrain   # run one stage
python scripts/run_scenario.py chouldari --step simulation
python scripts/run_scenario.py chouldari --force     # bypass freshness checks
python scripts/run_scenario.py chouldari --clean     # delete generated outputs, then run
```

- **`DUALSPHYSICS_ROOT` resolution** (highest priority first): `--dualsphysics-root` flag
  → `DUALSPHYSICS_ROOT` env var → per-scenario `dualsphysics_root` → gitignored
  [`scenarios/local.json`](scenarios/local.example.json) → clear error. The
  `GenCase_win64.exe`, `DualSPHysics5.4_win64.exe` and `PartVTK_win64.exe`
  binaries must exist.
- Freshness is tracked in `data/terrain/<scenario>/metadata/manifest.json`;
  unchanged steps are skipped. Simulation and post-processing always run.
- Every run lands in `simulations/<scenario>/runs/run_<timestamp>/` with
  `run.log` + `metadata.json`; `simulations/<scenario>/latest.json` points at
  the most recent successful one.
- Exit codes: `0` success, `1` validation/step failure, `2` usage/config error.

Full reference: [`docs/scenario-runner.md`](docs/scenario-runner.md).

---

## Git hygiene

Large or generated artifacts are intentionally **not** tracked:

- `simulations/runs/` — solver outputs (many GB)
- `simulations/templates/*.xml` — rewritten before every run
- `examples/**/*_out/` and other generated case files
- generated GIS products under `data/terrain/` (only the small sample inputs
  listed above are tracked)
- `node_modules/`, `venv/`, `__pycache__/`, build outputs, logs, `.env*`

Secrets: there are **no API keys or credentials in this repository**. Local
environment files (`.env`, `.env.*`) are ignored; only `.env.example`
(variable names) is tracked.
