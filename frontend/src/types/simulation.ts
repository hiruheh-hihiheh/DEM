// Types shared by the simulation job API, result viewer and analytics.

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed'

export type StageStatus = 'pending' | 'active' | 'done' | 'failed'

export interface JobStage {
  id: string
  label: string
  weight: number
  status: StageStatus
  detail: string | null
  progress: number
}

export interface JobMetrics {
  frames: number
  duration_s: number
  time_known: boolean
  particles: {
    max: number
    min: number
    sampled_total: number
  }
  peak_speed: {
    value: number
    frame: number
    time: number
    unit: string
  }
  wet_area: {
    final: number
    max: number
    unit: string
  }
  downstream_reach?: {
    max: number
    final: number
    reference: string
    unit: string
    axis: string
  }
  flow_extent?: {
    min: number
    max: number
    axis: string
    unit: string
  }
  volume_estimate?: {
    value: number
    dp: number
    unit: string
  } | null
  parts_out?: number | null
  units_note?: string
}

export interface JobResultInfo {
  available: boolean
  path: string | null
  metrics: JobMetrics | null
}

export interface JobSummary {
  id: string
  type: 'simulation' | 'run' | 'import'
  source: 'web' | 'runner'
  status: JobStatus
  stage: string | null
  stages: JobStage[]
  progress: number
  scenario: string
  scenario_display: string
  dam_id: string | null
  parameters?: Record<string, unknown> | null
  overrides?: Record<string, unknown>
  validated_config?: boolean
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  duration_seconds: number | null
  run_id: string | null
  run_dir: string | null
  simulation_output: string | null
  frame_progress: { done: number; total: number } | null
  error: string | null
  result: JobResultInfo
}

export interface JobsListResponse {
  jobs: JobSummary[]
  active_job_id: string | null
}

export interface ScenarioParameters {
  reservoir_water_depth: number
  particle_spacing: number
  simulation_time: number
  time_out: number
  breach_width: number
  breach_time: number
  reservoir_length?: number
  fluid_bed_clearance?: number
}

export interface ScenarioInfo {
  name: string
  display_name: string
  description: string
  ready: boolean
  error: string | null
  dam_id: string | null
  parameters: ScenarioParameters | null
  simulation_enabled?: boolean
}

export interface CreateJobRequest {
  scenario: string
  parameters: {
    reservoir_level: number
    breach_width: number | null
    breach_time: number | null
    simulation_time: number | null
    particle_spacing: number | null
    scenario_type: 'normal' | 'partial' | 'full' | 'extreme'
  }
}

export interface CreateJobResponse {
  job_id: string
  status: JobStatus
  scenario: string
  validated_config: boolean
  message: string
}

// ---- result package -------------------------------------------------------

export interface ResultManifest {
  format: string
  created_at: string
  source: {
    type: string
    job_id?: string
    scenario?: string
    dam_id?: string
    run_id?: string
    file?: string
    parameters?: Record<string, unknown> | null
    created_at?: string
  }
  files: {
    frames: string
    terrain: string | null
    flood: string | null
    analytics: string | null
  }
  layout: {
    frame_bytes_per_particle: number
    frame_order: string
    terrain_record: string
  }
  frames: {
    count: number
    counts: number[]
    times: number[]
    time_known: boolean
  }
  bytes: { frames: number }
  bounds: {
    fluid_min: [number, number, number]
    fluid_max: [number, number, number]
    terrain: { min: number[]; max: number[] } | null
  }
  speed: { global_max: number; unit: string }
  geo: {
    available: boolean
    crs?: string
    dam_lon?: number | null
    dam_lat?: number | null
    bounds?: [number, number, number, number]
  } | null
  dam: {
    center_x?: number
    center_y?: number
    center_z?: number
    centerline_endpoints?: number[][]
    crest_z?: number
    thickness?: number
    span?: number
  } | null
  upstream: {
    flow_axis?: string
    upstream_sign?: number
    method?: string
  } | null
  solver: { run: Record<string, unknown> }
  metrics: JobMetrics
  series: {
    t: number[]
    np: number[]
    speed_max: number[]
    wet_area: number[]
    extent: number[]
    reach: number[] | null
  }
}
