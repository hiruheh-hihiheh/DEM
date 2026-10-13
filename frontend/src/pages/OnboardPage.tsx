import React, { useState } from 'react'
import { uploadDem, generatePreview, prepareSite, runPipeline, createJob } from '../services/api'
import type { View } from '../components/NavBar'

type SiteMeta = Record<string, unknown>
type PrepareResult = { ok?: boolean; scenario?: string; case_dir?: string }
type PipelineResult = { ok?: boolean; returncode?: number; stdout?: string; stderr?: string }

const OUT_PANEL: React.CSSProperties = {
  background: '#0b1220',
  color: '#e5e7eb',
  border: '1px solid #1f2937',
  borderRadius: 8,
  padding: 16,
  maxHeight: 600,
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

export const OnboardPage: React.FC<{ onNavigate?: (v: View, jobId?: string) => void }> = ({ onNavigate }) => {
  const [step, setStep] = useState(1)
  const [file, setFile] = useState<File | null>(null)
  const [siteSlug, setSiteSlug] = useState('')
  const [siteName, setSiteName] = useState('')
  const [meta, setMeta] = useState<SiteMeta | null>(null)
  const [lon, setLon] = useState(76.4361)
  const [lat, setLat] = useState(31.4111)
  const [widthKm, setWidthKm] = useState(10)
  const [heightKm, setHeightKm] = useState(10)
  // Optional explicit dam placement (blank => study-area centre) and orientation.
  const [damLon, setDamLon] = useState('')
  const [damLat, setDamLat] = useState('')
  const [damAxis, setDamAxis] = useState<'auto' | 'x' | 'y'>('auto')
  const [damSign, setDamSign] = useState<'auto' | '1' | '-1'>('auto')
  // Reservoir / dam geometry in METRES (converted to simulation units on the server).
  const [waterDepthM, setWaterDepthM] = useState(33)
  const [freeboardM, setFreeboardM] = useState(2.5)
  const [crestHeightM, setCrestHeightM] = useState(40)
  const [thicknessM, setThicknessM] = useState(25)
  const [embedmentM, setEmbedmentM] = useState(5)
  const [reservoirLengthM, setReservoirLengthM] = useState(150)
  const [bedClearanceM, setBedClearanceM] = useState(8)
  const [breachWidthM, setBreachWidthM] = useState(50)
  const [clipToWetReach, setClipToWetReach] = useState(false)
  const [preview, setPreview] = useState<SiteMeta | null>(null)
  const [prepareRes, setPrepareRes] = useState<PrepareResult | null>(null)
  const [pipelineRes, setPipelineRes] = useState<PipelineResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [msgIsError, setMsgIsError] = useState(false)

  const notify = (text: string, isError = false) => {
    setMsg(text)
    setMsgIsError(isError)
  }

  const handleUpload = async () => {
    if (!file || loading) return
    setLoading(true)
    setMsg(null)
    try {
      const res = await uploadDem(file, siteSlug || undefined, siteName || undefined)
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
    setLoading(true)
    setMsg(null)
    try {
      const res = await generatePreview(siteSlug, { lon, lat, width_km: widthKm, height_km: heightKm })
      setPreview((res as SiteMeta) ?? null)
      notify('Preview generated')
      setStep(3)
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
    if (lon < -180 || lon > 180) {
      notify('Study-area longitude must be between -180 and 180.', true)
      return
    }
    if (lat < -90 || lat > 90) {
      notify('Study-area latitude must be between -90 and 90.', true)
      return
    }
    const hasDamLon = damLon.trim() !== ''
    const hasDamLat = damLat.trim() !== ''
    if (hasDamLon !== hasDamLat) {
      notify('Dam anchor needs BOTH longitude and latitude, or leave both blank to use the study-area center.', true)
      return
    }
    if (hasDamLon) {
      const dLon = Number(damLon)
      const dLat = Number(damLat)
      if (!Number.isFinite(dLon) || !Number.isFinite(dLat)) {
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
    setLoading(true)
    setMsg(null)
    try {
      const res = await prepareSite(siteSlug, {
        lon,
        lat,
        width_km: widthKm,
        height_km: heightKm,
        // Dam placement/orientation (null => infer / study-area centre).
        dam_lon: damLon.trim() ? Number(damLon) : null,
        dam_lat: damLat.trim() ? Number(damLat) : null,
        dam_axis: damAxis === 'auto' ? null : damAxis,
        dam_upstream_sign: damSign === 'auto' ? null : Number(damSign),
        // Geometry in metres (same physical meaning at every site).
        reservoir_water_depth_m: waterDepthM,
        reservoir_freeboard_m: freeboardM,
        dam_crest_height_m: crestHeightM,
        dam_thickness_m: thicknessM,
        dam_embedment_m: embedmentM,
        reservoir_length_m: reservoirLengthM,
        fluid_bed_clearance_m: bedClearanceM,
        breach_width_m: breachWidthM,
        breach_time: 0.25,
        simulation_time: 6.0,
        particle_spacing: 0.1,
        reservoir_clip_to_wet_reach: clipToWetReach,
      })
      setPrepareRes((res as PrepareResult) ?? null)
      notify('Scenario prepared')
      setStep(4)
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
    try {
      const res = await runPipeline(siteSlug, false)
      setPipelineRes((res as PipelineResult) ?? null)
      notify(res.ok ? 'Pipeline completed' : 'Pipeline failed', !res.ok)
      setStep(5)
    } catch (e: unknown) {
      notify(errorDetail(e, 'Pipeline failed'), true)
    } finally {
      setLoading(false)
    }
  }

  const handleCreateJob = async () => {
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

  return (
    <div style={{ padding: 20, maxWidth: 900, color: '#e5e7eb' }}>
      <h2>Add New Dam / Import DEM</h2>
      <div style={{ marginBottom: 12 }}>
        <strong>Step {step} of 5</strong> | {['Upload', 'Validate & Select Location', 'Study Area & Preview', 'Prepare', 'Validate/Pipeline'][step-1]}
      </div>
      {msg && <div style={msgIsError ? MSG_ERR : MSG_OK}>{msg}</div>}

      {step===1 && (
        <div>
          <div style={{ marginBottom: 8 }}>
            <input type="file" accept=".tif,.tiff" onChange={e=>setFile(e.target.files?.[0]||null)} />
          </div>
          <div style={{ marginBottom: 8 }}>
            Site name: <input value={siteName} onChange={e=>setSiteName(e.target.value)} />
          </div>
          <div style={{ marginBottom: 8 }}>
            Site slug: <input value={siteSlug} onChange={e=>setSiteSlug(e.target.value)} />
          </div>
          <button disabled={!file || loading} onClick={handleUpload}>Upload & Validate</button>
        </div>
      )}

      {step===2 && meta && (
        <div>
          <h3>DEM Metadata</h3>
          <pre style={OUT_PANEL}>{JSON.stringify(meta, null, 2)}</pre>
          <div style={{ marginBottom:8 }}>
            Dam location (lon, lat): <input value={lon} onChange={e=>setLon(parseFloat(e.target.value)||lon)} /> <input value={lat} onChange={e=>setLat(parseFloat(e.target.value)||lat)} />
          </div>
          <div style={{ marginBottom:8 }}>
            Dam anchor lon/lat (optional — blank uses the study-area centre):{' '}
            <input placeholder="auto" value={damLon} onChange={e=>setDamLon(e.target.value)} />{' '}
            <input placeholder="auto" value={damLat} onChange={e=>setDamLat(e.target.value)} />
            {(damLon.trim() === '') !== (damLat.trim() === '') && (
              <span style={{ color:'#fca5a5', fontSize:'0.85rem', marginLeft:8 }}>
                Provide both longitude and latitude, or leave both blank.
              </span>
            )}
          </div>
          <div style={{ marginBottom:8 }}>
            Flow axis:{' '}
            <select
              value={damAxis}
              onChange={e=>{
                const next = e.target.value as 'auto' | 'x' | 'y'
                setDamAxis(next)
                // The upstream side is only meaningful relative to a fixed axis.
                if (next === 'auto') setDamSign('auto')
              }}
            >
              <option value="auto">auto (infer from terrain)</option>
              <option value="x">x</option>
              <option value="y">y</option>
            </select>{' '}
            Upstream side:{' '}
            <select
              value={damSign}
              disabled={damAxis === 'auto'}
              title={damAxis === 'auto' ? 'Choose a flow axis first' : 'Which side of the dam is upstream'}
              onChange={e=>setDamSign(e.target.value as 'auto' | '1' | '-1')}
            >
              <option value="auto">auto (infer from terrain)</option>
              <option value="1">+1</option>
              <option value="-1">-1</option>
            </select>
            {damAxis === 'auto' && (
              <span style={{ color:'#9ca3af', fontSize:'0.85rem', marginLeft:8 }}>
                Choose a flow axis to set the upstream side.
              </span>
            )}
          </div>
          <button disabled={loading} onClick={()=>setStep(3)}>Next</button>
        </div>
      )}

      {step===3 && (
        <div>
          <div style={{ marginBottom:8 }}>
            Width (km): <input value={widthKm} onChange={e=>setWidthKm(parseFloat(e.target.value)||10)} />
            Height (km): <input value={heightKm} onChange={e=>setHeightKm(parseFloat(e.target.value)||10)} />
          </div>
          <button disabled={loading} onClick={handlePreview}>Generate Preview</button>
          {preview && (
            <div style={{ marginTop:12 }}>
              <pre style={OUT_PANEL}>{JSON.stringify(preview, null, 2)}</pre>
              <button disabled={loading} onClick={()=>setStep(4)}>Next</button>
            </div>
          )}
        </div>
      )}

      {step===4 && (
        <div>
          <h3>Prepare Scenario & Case</h3>
          <p style={{ color: '#9ca3af', fontSize: '0.85rem' }}>
            Reservoir and dam dimensions are in metres. The backend converts them to the terrain
            simulation scale so the same numbers mean the same physical size at every site.
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(260px, 1fr))', gap: 8, marginBottom: 12 }}>
            <label>Reservoir water depth (m): <input type="number" value={waterDepthM} onChange={e=>setWaterDepthM(parseFloat(e.target.value)||0)} /></label>
            <label>Reservoir freeboard (m): <input type="number" value={freeboardM} onChange={e=>setFreeboardM(parseFloat(e.target.value)||0)} /></label>
            <label>Dam crest height (m): <input type="number" value={crestHeightM} onChange={e=>setCrestHeightM(parseFloat(e.target.value)||0)} /></label>
            <label>Dam thickness (m): <input type="number" value={thicknessM} onChange={e=>setThicknessM(parseFloat(e.target.value)||0)} /></label>
            <label>Dam embedment (m): <input type="number" value={embedmentM} onChange={e=>setEmbedmentM(parseFloat(e.target.value)||0)} /></label>
            <label>Reservoir length (m): <input type="number" value={reservoirLengthM} onChange={e=>setReservoirLengthM(parseFloat(e.target.value)||0)} /></label>
            <label>Fluid bed clearance (m): <input type="number" value={bedClearanceM} onChange={e=>setBedClearanceM(parseFloat(e.target.value)||0)} /></label>
            <label>Breach width (m): <input type="number" value={breachWidthM} onChange={e=>setBreachWidthM(parseFloat(e.target.value)||0)} /></label>
          </div>
          <label style={{ display: 'block', marginBottom: 12 }}>
            <input type="checkbox" checked={clipToWetReach} onChange={e=>setClipToWetReach(e.target.checked)} />{' '}
            Opt in: end reservoir segments at the waterline where the bed rises above it
            (off by default — changes the reservoir geometry)
          </label>
          <button disabled={loading} onClick={handlePrepare}>Prepare</button>
          {prepareRes && (
            <div style={{ marginTop:12 }}>
              <pre style={OUT_PANEL}>{JSON.stringify(prepareRes, null, 2)}</pre>
              <button disabled={loading} onClick={()=>setStep(5)}>Next</button>
            </div>
          )}
        </div>
      )}

      {step===5 && (
        <div>
          <h3>Validate & Run Pipeline</h3>
          <p style={{ color: '#9ca3af', fontSize: '0.85rem' }}>
            Runs terrain processing first, then SPH generation. SPH only starts if terrain succeeds.
          </p>
          <button disabled={loading} onClick={handleRunPipeline}>Run Terrain+SPH Pipeline</button>
          {pipelineRes && (
            <div style={{ marginTop:12 }}>
              <div style={pipelineRes.ok ? MSG_OK : MSG_ERR}>
                {(pipelineRes.ok ? 'Pipeline succeeded' : 'Pipeline failed')}
                {typeof pipelineRes.returncode === 'number' ? ` (exit code ${pipelineRes.returncode})` : ''}
              </div>
              <pre style={OUT_PANEL}>{pipelineRes.stdout || pipelineRes.stderr || JSON.stringify(pipelineRes, null, 2)}</pre>
              <button disabled={loading || !pipelineRes.ok} onClick={handleCreateJob}>Create Simulation Job</button>
              {!pipelineRes.ok && (
                <div style={{ ...MSG_INFO, marginTop: 12 }}>
                  Fix the pipeline failure above before creating a simulation job.
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
