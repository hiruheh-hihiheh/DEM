// System readiness + local DualSPHysics configuration types.
// Mirrors backend/app/api/system.py — nothing here is invented client-side.

export interface SystemComponent {
  ready: boolean
  detail: string | null
}

export interface DualSPHysicsBinary {
  available: boolean
  filename: string
  path: string
}

export interface DualSPHysicsStatus {
  /** A root was resolved (env / scenario / local config / requested path). */
  configured: boolean
  root: string | null
  /** Where the resolved path came from (e.g. `scenarios/local.json`). */
  source: string | null
  /** Installation folder exists on disk. */
  exists: boolean
  /** Folder exists and all three required binaries were found. */
  ready: boolean
  missing: string[]
  error: string | null
  gencase: DualSPHysicsBinary | null
  solver: DualSPHysicsBinary | null
  partvtk: DualSPHysicsBinary | null
  /** DUALSPHYSICS_ROOT environment variable, if set (takes precedence). */
  env_override?: string | null
  /** Present on save responses. */
  saved?: boolean
}

export interface SystemStatus {
  backend: SystemComponent
  dam_data: SystemComponent
  dem_pipeline: SystemComponent
  scenario_runner: SystemComponent
  dualsphysics: DualSPHysicsStatus
}

/** Map overlay returned by `GET /api/simulations/scenarios/{name}/dem-hillshade`. */
export interface DemHillshade {
  available: boolean
  error?: string
  /** `data:image/png;base64,…` grayscale + hillshade preview of the real DEM. */
  image?: string
  /** MapLibre image-source corners: [[w,n],[e,n],[e,s],[w,s]] in WGS84. */
  coordinates?: [[number, number], [number, number], [number, number], [number, number]]
  elevation?: { min: number; max: number; unit: string }
  source?: string
  scenario?: string
}
