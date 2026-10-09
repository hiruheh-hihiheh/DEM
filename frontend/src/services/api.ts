import axios from 'axios'
import type { DamGeoJSON } from '../types/dam'
import type {
  CreateJobRequest,
  CreateJobResponse,
  JobsListResponse,
  JobSummary,
  ResultManifest,
  ScenarioInfo,
} from '../types/simulation'
import type {
  DemHillshade,
  DualSPHysicsStatus,
  SystemStatus,
} from '../types/system'

/**
 * Backend base URL. Configurable per deployment via the environment
 * (`VITE_API_BASE`); the local default matches the documented uvicorn
 * address — no other hardcoded host/ports exist in the app code.
 */
export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/+$/, '') ||
  'http://127.0.0.1:8000/api'

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
})

export const fetchDams = async (): Promise<DamGeoJSON> => {
  const response = await api.get<DamGeoJSON>('/dams')
  return response.data
}

// ---- scenario catalogue ----------------------------------------------------

export const fetchScenarios = async (): Promise<ScenarioInfo[]> => {
  const response = await api.get<{ scenarios: ScenarioInfo[] }>(
    '/simulations/scenarios',
  )
  return response.data.scenarios
}

// ---- jobs ------------------------------------------------------------------

export const fetchJobs = async (): Promise<JobsListResponse> => {
  const response = await api.get<JobsListResponse>('/simulations/jobs')
  return response.data
}

export const fetchJob = async (jobId: string): Promise<JobSummary> => {
  const response = await api.get<JobSummary>(`/simulations/jobs/${jobId}`)
  return response.data
}

export const createJob = async (
  body: CreateJobRequest,
): Promise<CreateJobResponse> => {
  const response = await api.post<CreateJobResponse>(
    '/simulations/jobs',
    body,
  )
  return response.data
}

export interface LogResponse {
  lines: string[]
  offset: number
}

export const fetchJobLogs = async (
  jobId: string,
  offset = 0,
): Promise<LogResponse> => {
  const response = await api.get<LogResponse>(
    `/simulations/jobs/${jobId}/logs`,
    { params: { offset } },
  )
  return response.data
}

export const processJobResult = async (
  jobId: string,
): Promise<{ job_id: string; available: boolean }> => {
  const response = await api.post(`/simulations/jobs/${jobId}/process`)
  return response.data
}

export const fetchResultManifest = async (
  jobId: string,
): Promise<ResultManifest> => {
  const response = await api.get<ResultManifest>(
    `/simulations/jobs/${jobId}/result`,
  )
  return response.data
}

/** Binary result files are fetched as array buffers (viewer) ... */
export const fetchResultBinary = async (
  jobId: string,
  filename: 'frames.bin' | 'terrain.bin',
): Promise<ArrayBuffer> => {
  const response = await api.get<ArrayBuffer>(
    `/simulations/jobs/${jobId}/result/${filename}`,
    { responseType: 'arraybuffer', timeout: 120000 },
  )
  return response.data
}

/** ... and offered for direct download / map sources via URL. */
export const resultFileUrl = (
  jobId: string,
  filename: string,
): string => `${API_BASE}/simulations/jobs/${jobId}/result/${filename}`

export const exportUrl = (jobId: string): string =>
  `${API_BASE}/simulations/jobs/${jobId}/export`

// ---- import ----------------------------------------------------------------

export const uploadImport = async (
  file: File,
): Promise<CreateJobResponse> => {
  const form = new FormData()
  form.append('file', file)
  const response = await api.post<CreateJobResponse>('/simulations/imports', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  })
  return response.data
}

// ---- system readiness + DualSPHysics configuration -------------------------
// All of these delegate to the backend, which validates through the SAME
// code the Scenario Runner uses (scripts/scenario_runner/env.py).

export const fetchSystemStatus = async (): Promise<SystemStatus> => {
  const response = await api.get<SystemStatus>('/system/status', {
    timeout: 15000,
  })
  return response.data
}

export const fetchDualSPHysics = async (): Promise<DualSPHysicsStatus> => {
  const response = await api.get<DualSPHysicsStatus>('/system/dualsphysics', {
    timeout: 15000,
  })
  return response.data
}

/** Validate an installation path without saving it. */
export const validateDualSPHysics = async (
  root: string,
): Promise<DualSPHysicsStatus> => {
  const response = await api.post<DualSPHysicsStatus>(
    '/system/dualsphysics/validate',
    { root },
    { timeout: 20000 },
  )
  return response.data
}

/**
 * Persist the path to `scenarios/local.json` (machine-local, git-ignored).
 * Passing an empty string clears the machine-local setting.
 */
export const saveDualSPHysics = async (
  root: string | null,
): Promise<DualSPHysicsStatus> => {
  const response = await api.put<DualSPHysicsStatus>(
    '/system/dualsphysics',
    { root },
    { timeout: 20000 },
  )
  return response.data
}

// ---- terrain preview -------------------------------------------------------

/** Grayscale + hillshade preview of a scenario's real DEM (WGS84-placed). */
export const fetchDemHillshade = async (
  scenario: string,
): Promise<DemHillshade> => {
  const response = await api.get<DemHillshade>(
    `/simulations/scenarios/${encodeURIComponent(scenario)}/dem-hillshade`,
    { timeout: 30000 },
  )
  return response.data
}

export default api
