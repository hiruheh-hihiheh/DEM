import React, { useState } from 'react'
import { uploadDem, generatePreview, prepareSite, runPipeline, createJob } from '../services/api'
import type { View } from '../components/NavBar'

type SiteMeta = Record<string, unknown>
type PrepareResult = { ok?: boolean; scenario?: string; case_dir?: string }
type PipelineResult = {
  ok?: boolean
  stage?: string
  returncode?: number
  stdout?: string
  stderr?: string
  error?: string
  validation?: {
    status?: string
    checks_passed?: number
    errors?: string[]
  }
  validation_errors?: string[]
  limitations?: string[]
  terrain_diagnostic?: Record<string, unknown>
}

const OUT_PANEL: React.CSSProperties = {
  background: '#0b1220',
  color: '#e5e7eb',
  border: '1px solid #1f2937',
  borderRadius: 8,
  padding: 16,
  maxHeight: 420,
  overflow: 'auto',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: '0.875rem',
  lineHeight: 1.5,
  whiteSpace: 'pre-wrap',
  marginTop: 12,
}

const MSG_OK: React.CSSProperties = {
  background: '#052e16',
  color: '#bbf7d0',
  border: '1px solid #14532d',
  padding: 10,
  borderRadius: 6,
  marginBottom: 12,
}

const MSG_ERR: React.CSSProperties = {
  background: '#450a0a',
  color: '#fecaca',
  border: '1px solid #7f1d1d',
  padding: 10,
  borderRadius: 6,
  marginBottom: 12,
}

const MSG_INFO: React.CSSProperties = {
  background: '#0b1220',
  color: '#e5e7eb',
  border: '1px solid #1f2937',
  padding: 10,
  borderRadius: 6,
  marginBottom: 12,
}

const SUGGESTION: React.CSSProperties = {
  color: '#9ca3af',
  fontSize: '0.85rem',
  marginTop: 8,
}

/** Extract a human-readable message from an unknown thrown value. */
const errorDetail = (e: unknown, fallback: string): string => {
  if (typeof e === 'object' && e !== null) {
    const resp = (e as { response?: { data?: { detail?: unknown } } }).response
    const detail = resp?.data?.detail
    if (typeof detail === 'string' && detail) return detail
    const message = (e as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  if (typeof e === 'string' && e) return e
  return fallback
}

/**
 * Parse a numeric text field. Returns null when the field is empty or not a
 * finite number, so callers can show a real validation error instead of
 * silently substituting a default (which used to turn "" into 0/NaN and make
 * the field "jump back" while typing).
 */
const parseNumber = (value: string): number | null => {
  const text = value.trim()
  if (text === '') return null
  const n = Number(text)
  return Number.isFinite(n) ? n : null
}

type NumKey =
  | 'waterDepthM'
  | 'freeboardM'
  | 'crestHeightM'
  | 'thicknessM'
  | 'embedmentM'
  | 'reservoirLengthM'
  | 'bedClearanceM'
  | 'breachWidthM'

const NUM_FIELDS: { key: NumKey; label: string }[] = [
  { key: 'waterDepthM', label: 'Reservoir water depth (m)' },
  { key: 'freeboardM', label: 'Reservoir freeboard (m)' },
  { key: 'crestHeightM', label: 'Dam crest height (m)' },
  { key: 'thicknessM', label: 'Dam thickness (m)' },
  { key: 'embedmentM', label: 'Dam embedment (m)' },
  { key: 'reservoirLengthM', label: 'Reservoir length (m)' },
  { key: 'bedClearanceM', label: 'Fluid bed clearance (m)' },
  { key: 'breachWidthM', label: 'Breach width (m)' },
]

const NUM_DEFAULTS: Record<NumKey, string> = {
  waterDepthM: '33',
  freeboardM: '2.5',
  crestHeightM: '40',
  thicknessM: '25',
  embedmentM: '5',
  reservoirLengthM: '150',
  bedClearanceM: '8',
  breachWidthM: '50',
}

const STEP_LABELS = [
  'Upload',
  'Validate & Select Location',
  'Study Area & Preview',
  'Prepare',
  'Validate/Pipeline',
]

export const OnboardPage: React.FC<{ onNavigate?: (v: View, jobId?: string) => void }> = ({ onNavigate }) => {
  const [step, setStep] = useState(1)
  const [file, setFile] = useState<File | null>(null)
  const [siteSlug, setSiteSlug] = useState('')
  const [siteName, setSiteName] = useState('')
  const [meta, setMeta] = useState<SiteMeta | null>(null)
  // All numeric fields are held as strings so the user can clear them or type
  // a partial value (e.g. "0.") without the value being replaced underneath.
  const [lon, setLon] = useState('76.4361')
  const [lat, setLat] = useState('31.4111')
  const [widthKm, setWidthKm] = useState('10')
  const [heightKm, setHeightKm] = useState('10')
  // Optional explicit dam placement (blank => study-area centre) and orientation.
  const [damLon, setDamLon] = useState('')
  const [damLat, setDamLat] = useState('')
  const [damAxis, setDamAxis] = useState<'auto' | 'x' | 'y'>('auto')
  const [damSign, setDamSign] = useState<'auto' | '1' | '-1'>('auto')
  // Reservoir / dam geometry in METRES (converted to simulation units on the server).
  const [nums, setNums] = useState<Record<NumKey, string>>({ ...NUM_DEFAULTS })
  const [clipToWetReach, setClipToWetReach] = useState(false)
  const [preview, setPreview] = useState<SiteMeta | null>(null)
  const [prepareRes, setPrepareRes] = useState<PrepareResult | null>(null)
  const [pipelineRes, setPipelineRes] = useState<PipelineResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [msgIsError, setMsgIsError] = useState(false)

  const setNum = (key: NumKey, value: string) =>
    setNums((prev) => ({ ...prev, [key]: value }))

  const notify = (text: string, isError = false) => {
    setMsg(text)
    setMsgIsError(isError)
  }

  const back = (target: number) => {
    if (loading) return
    setMsg(null)
    setStep(target)
  }

  const handleUpload = async () => {
    if (!file || loading) return
    setLoading(true)
    setMsg(null)
    setMeta(null)
    try {
      const res = await uploadDem(file, siteSlug.trim() || undefined, siteName.trim() || undefined)
      setMeta((res.meta as SiteMeta) ?? null)
      setSiteSlug(String(res.site_slug ?? siteSlug))
      notify('DEM uploaded and validated')
      setStep(2)
    } catch (e: unknown) {
      notify(errorDetail(e, 'Upload failed'), true)
    } finally {
      setLoading(false)
    }
  }

  const handlePreview = async () => {
    if (loading) return
    // Validate the study area before spending a round-trip on the backend.
    const sLon = parseNumber(lon)
    const sLat = parseNumber(lat)
    const w = parseNumber(widthKm)
    const h = parseNumber(heightKm)
    if (sLon === null || sLat === null) {
      notify('Study-area longitude and latitude are required numbers.', true)
      return
    }
    if (sLon < -180 || sLon > 180) {
      notify('Study-area longitude must be between -180 and 180.', true)
      return
    }
    if (sLat < -90 || sLat > 90) {
      notify('Study-area latitude must be between -90 and 90.', true)
      return
    }
    if (w === null || w <= 0 || h === null || h <= 0) {
      notify('Study-area width and height must be positive numbers (km).', true)
      return
    }
    setLoading(true)
    setMsg(null)
    setPreview(null)
    try {
      const res = await generatePreview(siteSlug, { lon: sLon, lat: sLat, width_km: w, height_km: h })
      setPreview((res as SiteMeta) ?? null)
      notify('Preview generated')
    } catch (e: unknown) {
      notify(errorDetail(e, 'Preview failed'), true)
    } finally {
      setLoading(false)
    }
  }

  const handlePrepare = async () => {
    if (loading) return
    // Dam anchor coordinates must be supplied as a complete pair, or omitted
    // entirely to fall back to the study-area center. Mirror the server rules
    // so the user gets immediate feedback instead of a 400 round-trip.
    const sLon = parseNumber(lon)
    const sLat = parseNumber(lat)
    const w = parseNumber(widthKm)
    const h = parseNumber(heightKm)
    if (sLon === null || sLat === null) {
      notify('Study-area longitude and latitude are required numbers.', true)
      return
    }
    if (sLon < -180 || sLon > 180) {
      notify('Study-area longitude must be between -180 and 180.', true)
      return
    }
    if (sLat < -90 || sLat > 90) {
      notify('Study-area latitude must be between -90 and 90.', true)
      return
    }
    if (w === null || w <= 0 || h === null || h <= 0) {
      notify('Study-area width and height must be positive numbers (km).', true)
      return
    }
    const hasDamLon = damLon.trim() !== ''
    const hasDamLat = damLat.trim() !== ''
    if (hasDamLon !== hasDamLat) {
      notify('Dam anchor needs BOTH longitude and latitude, or leave both blank to use the study-area center.', true)
      return
    }
    if (hasDamLon) {
      const dLon = parseNumber(damLon)
      const dLat = parseNumber(damLat)
      if (dLon === null || dLat === null) {
        notify('Dam anchor longitude and latitude must be numbers.', true)
        return
      }
      if (dLon < -180 || dLon > 180) {
        notify('Dam anchor longitude must be between -180 and 180.', true)
        return
      }
      if (dLat < -90 || dLat > 90) {
        notify('Dam anchor latitude must be between -90 and 90.', true)
        return
      }
    }
    // Geometry is in metres and must be a complete, non-negative set.
    const geo = {} as Record<NumKey, number>
    for (const field of NUM_FIELDS) {
      const n = parseNumber(nums[field.key])
      if (n === null) {
        notify(`${field.label} must be a number.`, true)
        return
      }
      if (n < 0) {
        notify(`${field.label} cannot be negative.`, true)
        return
      }
      geo[field.key] = n
    }
    setLoading(true)
    setMsg(null)
    setPrepareRes(null)
    try {
      const res = await prepareSite(siteSlug, {
        lon: sLon,
        lat: sLat,
        width_km: w,
        height_km: h,
        // Dam placement/orientation (null => infer / study-area centre).
        dam_lon: hasDamLon ? parseNumber(damLon) : null,
        dam_lat: hasDamLat ? parseNumber(damLat) : null,
        dam_axis: damAxis === 'auto' ? null : damAxis,
        dam_upstream_sign: damSign === 'auto' ? null : Number(damSign),
        // Geometry in metres (same physical meaning at every site).
        reservoir_water_depth_m: geo.waterDepthM,
        reservoir_freeboard_m: geo.freeboardM,
        dam_crest_height_m: geo.crestHeightM,
        dam_thickness_m: geo.thicknessM,
        dam_embedment_m: geo.embedmentM,
        reservoir_length_m: geo.reservoirLengthM,
        fluid_bed_clearance_m: geo.bedClearanceM,
        breach_width_m: geo.breachWidthM,
        breach_time: 0.25,
        simulation_time: 6.0,
        particle_spacing: 0.1,
        reservoir_clip_to_wet_reach: clipToWetReach,
      })
      setPrepareRes((res as PrepareResult) ?? null)
      notify('Scenario prepared')
    } catch (e: unknown) {
      notify(errorDetail(e, 'Prepare failed'), true)
    } finally {
      setLoading(false)
    }
  }

  const handleRunPipeline = async () => {
    if (loading) return
    setLoading(true)
    setMsg(null)
    setPipelineRes(null)
    try {
      const res = await runPipeline(siteSlug, false)
      setPipelineRes((res as PipelineResult) ?? null)
      notify(res.ok ? 'Pipeline completed' : 'Pipeline failed', !res.ok)
    } catch (e: unknown) {
      notify(errorDetail(e, 'Pipeline failed'), true)
    } finally {
      setLoading(false)
    }
  }

  const handleCreateJob = async () => {
    // Never create a job from a failed pipeline.
    if (loading || !pipelineRes?.ok) return
    setLoading(true)
    setMsg(null)
    try {
      const res = await createJob({ scenario: siteSlug, parameters: { reservoir_level: 100, breach_width: 1.0, breach_time: 0.25, simulation_time: 6.0, particle_spacing: 0.1, scenario_type: 'normal' } })
      notify('Job created: ' + res.job_id)
      if (onNavigate) onNavigate('run', res.job_id)
    } catch (e: unknown) {
      notify(errorDetail(e, 'Create job failed'), true)
    } finally {
      setLoading(false)
    }
  }

  const busy = (label: string) => (loading ? 'Working…' : label)

  const damPairIncomplete = (damLon.trim() === '') !== (damLat.trim() === '')

  return (
    <div className="page">
      <div style={{ maxWidth: 900 }}>
        <h2>Add New Dam / Import DEM</h2>
        <div style={{ marginBottom: 12 }}>
          <strong>Step {step} of 5</strong> | {STEP_LABELS[step - 1]}
        </div>
        {msg && <div style={msgIsError ? MSG_ERR : MSG_OK}>{msg}</div>}

        {step === 1 && (
          <div>
            <div style={{ marginBottom: 8 }}>
              <label className="form-label" htmlFor="dem-file">GeoTIFF DEM (.tif / .tiff)</label>
              <input
                id="dem-file"
                type="file"
                accept=".tif,.tiff"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </div>
            <div style={{ marginBottom: 8 }}>
              <label className="form-label" htmlFor="site-name">Site name</label>
              <input
                id="site-name"
                className="form-input"
                value={siteName}
                onChange={(e) => setSiteName(e.target.value)}
              />
            </div>
            <div style={{ marginBottom: 8 }}>
              <label className="form-label" htmlFor="site-slug">Site slug (optional — derived from the name)</label>
              <input
                id="site-slug"
                className="form-input"
                value={siteSlug}
                onChange={(e) => setSiteSlug(e.target.value)}
              />
            </div>
            <button type="button" disabled={!file || loading} onClick={() => void handleUpload()}>
              {busy('Upload & Validate')}
            </button>
          </div>
        )}

        {step === 2 && meta && (
          <div>
            <h3>DEM Metadata</h3>
            <pre style={OUT_PANEL}>{JSON.stringify(meta, null, 2)}</pre>
            <div style={{ marginBottom: 8 }}>
              <label className="form-label">Dam location (longitude, latitude)</label>
              <input
                className="form-input"
                style={{ width: 160, marginRight: 8 }}
                inputMode="decimal"
                aria-label="Study-area longitude"
                value={lon}
                onChange={(e) => setLon(e.target.value)}
              />
              <input
                className="form-input"
                style={{ width: 160 }}
                inputMode="decimal"
                aria-label="Study-area latitude"
                value={lat}
                onChange={(e) => setLat(e.target.value)}
              />
            </div>
            <div style={{ marginBottom: 8 }}>
              <label className="form-label">Dam anchor lon/lat (optional — blank uses the study-area centre)</label>
              <input
                className="form-input"
                style={{ width: 160, marginRight: 8 }}
                inputMode="decimal"
                placeholder="auto"
                aria-label="Dam anchor longitude"
                value={damLon}
                onChange={(e) => setDamLon(e.target.value)}
              />
              <input
                className="form-input"
                style={{ width: 160 }}
                inputMode="decimal"
                placeholder="auto"
                aria-label="Dam anchor latitude"
                value={damLat}
                onChange={(e) => setDamLat(e.target.value)}
              />
              {damPairIncomplete && (
                <span style={{ color: '#fca5a5', fontSize: '0.85rem', marginLeft: 8 }}>
                  Provide both longitude and latitude, or leave both blank.
                </span>
              )}
            </div>
            <div style={{ marginBottom: 8 }}>
              <label className="form-label" htmlFor="flow-axis">Flow axis</label>
              <select
                id="flow-axis"
                className="form-select"
                style={{ width: 220, display: 'inline-block', marginRight: 12 }}
                value={damAxis}
                onChange={(e) => {
                  const next = e.target.value as 'auto' | 'x' | 'y'
                  setDamAxis(next)
                  // The upstream side is only meaningful relative to a fixed axis.
                  if (next === 'auto') setDamSign('auto')
                }}
              >
                <option value="auto">auto (infer from terrain)</option>
                <option value="x">x</option>
                <option value="y">y</option>
              </select>
              <label className="form-label" htmlFor="upstream-side">Upstream side</label>
              <select
                id="upstream-side"
                className="form-select"
                style={{ width: 220, display: 'inline-block' }}
                value={damSign}
                disabled={damAxis === 'auto'}
                title={damAxis === 'auto' ? 'Choose a flow axis first' : 'Which side of the dam is upstream'}
                onChange={(e) => setDamSign(e.target.value as 'auto' | '1' | '-1')}
              >
                <option value="auto">auto (infer from terrain)</option>
                <option value="1">+1</option>
                <option value="-1">-1</option>
              </select>
              {damAxis === 'auto' && (
                <div style={SUGGESTION}>Choose a flow axis to set the upstream side.</div>
              )}
            </div>
            <button type="button" className="btn" style={{ marginRight: 8 }} disabled={loading} onClick={() => back(1)}>Back</button>
            <button type="button" disabled={loading} onClick={() => setStep(3)}>Next</button>
          </div>
        )}

        {step === 3 && (
          <div>
            <div style={{ marginBottom: 8 }}>
              <label className="form-label" htmlFor="width-km">Study-area width (km)</label>
              <input
                id="width-km"
                className="form-input"
                style={{ width: 160, marginRight: 8 }}
                inputMode="decimal"
                value={widthKm}
                onChange={(e) => setWidthKm(e.target.value)}
              />
              <label className="form-label" htmlFor="height-km">Height (km)</label>
              <input
                id="height-km"
                className="form-input"
                style={{ width: 160 }}
                inputMode="decimal"
                value={heightKm}
                onChange={(e) => setHeightKm(e.target.value)}
              />
            </div>
            <button type="button" className="btn" style={{ marginRight: 8 }} disabled={loading} onClick={() => back(2)}>Back</button>
            <button type="button" disabled={loading} onClick={() => void handlePreview()}>
              {busy('Generate Preview')}
            </button>
            {preview && (
              <div style={{ marginTop: 12 }}>
                <pre style={OUT_PANEL}>{JSON.stringify(preview, null, 2)}</pre>
                <button type="button" disabled={loading} onClick={() => setStep(4)}>Next</button>
              </div>
            )}
          </div>
        )}

        {step === 4 && (
          <div>
            <h3>Prepare Scenario & Case</h3>
            <p style={{ color: '#9ca3af', fontSize: '0.85rem' }}>
              Reservoir and dam dimensions are in metres. The backend converts them to the terrain
              simulation scale so the same numbers mean the same physical size at every site.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(260px, 1fr))', gap: 8, marginBottom: 12 }}>
              {NUM_FIELDS.map((field) => (
                <label key={field.key} className="form-label" htmlFor={`num-${field.key}`}>
                  {field.label}
                  <input
                    id={`num-${field.key}`}
                    className="form-input"
                    type="text"
                    inputMode="decimal"
                    value={nums[field.key]}
                    onChange={(e) => setNum(field.key, e.target.value)}
                  />
                </label>
              ))}
            </div>
            <label style={{ display: 'block', marginBottom: 12 }}>
              <input type="checkbox" checked={clipToWetReach} onChange={(e) => setClipToWetReach(e.target.checked)} />{' '}
              Opt in: end reservoir segments at the waterline where the bed rises above it
              (off by default — changes the reservoir geometry)
            </label>
            <button type="button" className="btn" style={{ marginRight: 8 }} disabled={loading} onClick={() => back(3)}>Back</button>
            <button type="button" disabled={loading} onClick={() => void handlePrepare()}>
              {busy('Prepare')}
            </button>
            {prepareRes && (
              <div style={{ marginTop: 12 }}>
                <pre style={OUT_PANEL}>{JSON.stringify(prepareRes, null, 2)}</pre>
                <button type="button" disabled={loading} onClick={() => setStep(5)}>Next</button>
              </div>
            )}
          </div>
        )}

        {step === 5 && (
          <div>
            <h3>Validate & Run Pipeline</h3>
            <p style={{ color: '#9ca3af', fontSize: '0.85rem' }}>
              Runs terrain processing first, then SPH generation. SPH only starts if terrain succeeds.
            </p>
            <button type="button" className="btn" style={{ marginRight: 8 }} disabled={loading} onClick={() => back(4)}>Back</button>
            <button type="button" disabled={loading} onClick={() => void handleRunPipeline()}>
              {busy('Run Terrain+SPH Pipeline')}
            </button>
            {pipelineRes && (
              <div style={{ marginTop: 12 }}>
                <div style={pipelineRes.ok ? MSG_OK : MSG_ERR}>
                  {(pipelineRes.ok ? 'Pipeline succeeded' : 'Pipeline failed')}
                  {pipelineRes.stage ? ` · stage: ${pipelineRes.stage}` : ''}
                  {typeof pipelineRes.returncode === 'number' ? ` (exit code ${pipelineRes.returncode})` : ''}
                </div>
                {!pipelineRes.ok && pipelineRes.error && (pipelineRes.validation_errors?.length ?? 0) === 0 && (
                  <div style={{ ...MSG_ERR, whiteSpace: 'pre-wrap' }}>{pipelineRes.error}</div>
                )}
                {!pipelineRes.ok && (pipelineRes.validation_errors?.length ?? 0) > 0 && (
                  <div style={MSG_ERR}>
                    <strong>Physical validation failed</strong>
                    {typeof pipelineRes.validation?.checks_passed === 'number' && (
                      <> — {pipelineRes.validation.checks_passed} checks passed, {(pipelineRes.validation_errors ?? []).length} failed:</>
                    )}
                    <ul style={{ margin: '8px 0 0 0', paddingLeft: 20 }}>
                      {(pipelineRes.validation_errors ?? []).map((err, i) => (
                        <li key={i} style={{ marginBottom: 4 }}>{err}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {!pipelineRes.ok && (pipelineRes.limitations?.length ?? 0) > 0 && (
                  <details style={MSG_INFO}>
                    <summary style={{ cursor: 'pointer' }}>Terrain limitations</summary>
                    <ul style={{ margin: '8px 0 0 0', paddingLeft: 20, whiteSpace: 'pre-wrap' }}>
                      {(pipelineRes.limitations ?? []).map((lim, i) => (
                        <li key={i} style={{ marginBottom: 4 }}>{lim}</li>
                      ))}
                    </ul>
                  </details>
                )}
                <pre style={OUT_PANEL}>{pipelineRes.stdout || pipelineRes.stderr || JSON.stringify(pipelineRes, null, 2)}</pre>
                <button type="button" disabled={loading || !pipelineRes.ok} onClick={() => void handleCreateJob()}>
                  {busy('Create Simulation Job')}
                </button>
                {!pipelineRes.ok && (
                  <div style={{ ...MSG_INFO, marginTop: 12 }}>
                    Physical validation failed, so no simulation job was created. Fix the terrain or the
                    dam placement/anchor (see the errors above) and re-run the pipeline.
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
