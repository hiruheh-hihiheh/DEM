import React, { useState } from 'react'
import { uploadDem, generatePreview, prepareSite, runPipeline, createJob } from '../services/api'
import { MapToolBar } from '../components/MapToolBar'
import maplibregl from 'maplibre-gl'

export const OnboardPage: React.FC<{ onNavigate?: (v: any) => void }> = ({ onNavigate }) => {
  const [step, setStep] = useState(1)
  const [file, setFile] = useState<File | null>(null)
  const [siteSlug, setSiteSlug] = useState('')
  const [siteName, setSiteName] = useState('')
  const [meta, setMeta] = useState<any>(null)
  const [lon, setLon] = useState(76.4361)
  const [lat, setLat] = useState(31.4111)
  const [widthKm, setWidthKm] = useState(10)
  const [heightKm, setHeightKm] = useState(10)
  const [preview, setPreview] = useState<any>(null)
  const [prepareRes, setPrepareRes] = useState<any>(null)
  const [pipelineRes, setPipelineRes] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const handleUpload = async () => {
    if (!file) return
    setLoading(true)
    setMsg(null)
    try {
      const res = await uploadDem(file, siteSlug || undefined, siteName || undefined)
      setMeta(res.meta)
      setSiteSlug(res.site_slug)
      setMsg('DEM uploaded and validated')
      setStep(2)
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || 'Upload failed')
    } finally {
      setLoading(false)
    }
  }

  const handlePreview = async () => {
    setLoading(true)
    setMsg(null)
    try {
      const res = await generatePreview(siteSlug, { lon: parseFloat(lon as any), lat: parseFloat(lat as any), width_km: widthKm, height_km: heightKm })
      setPreview(res)
      setMsg('Preview generated')
      setStep(3)
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || 'Preview failed')
    } finally {
      setLoading(false)
    }
  }

  const handlePrepare = async () => {
    setLoading(true)
    setMsg(null)
    try {
      const res = await prepareSite(siteSlug, {
        lon: parseFloat(lon as any),
        lat: parseFloat(lat as any),
        width_km: widthKm,
        height_km: heightKm,
        reservoir_water_depth: 0.66,
        breach_width: 1.0,
        breach_time: 0.25,
        simulation_time: 6.0,
        particle_spacing: 0.1,
        fluid_bed_clearance: 0.16,
        dam_crest_height: 0.8,
        dam_thickness: 0.5,
      })
      setPrepareRes(res)
      setMsg('Scenario prepared')
      setStep(4)
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || 'Prepare failed')
    } finally {
      setLoading(false)
    }
  }

  const handleRunPipeline = async () => {
    setLoading(true)
    setMsg(null)
    try {
      const res = await runPipeline(siteSlug, false)
      setPipelineRes(res)
      setMsg(res.ok ? 'Pipeline completed' : 'Pipeline failed')
      setStep(5)
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || 'Pipeline failed')
    } finally {
      setLoading(false)
    }
  }

  const handleCreateJob = async () => {
    setLoading(true)
    setMsg(null)
    try {
      const res = await createJob({ scenario: siteSlug, params: { reservoir_level: 100, breach_width: 1.0, breach_time: 0.25, simulation_time: 6.0, particle_spacing: 0.1 } })
      setMsg('Job created: ' + res.job_id)
      if (onNavigate) onNavigate('run')
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || 'Create job failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: 20, maxWidth: 900 }}>
      <h2>Add New Dam / Import DEM</h2>
      <div style={{ marginBottom: 12 }}>
        <strong>Step {step} of 5</strong> | {['Upload', 'Validate & Select Location', 'Study Area & Preview', 'Prepare', 'Validate/Pipeline'][step-1]}
      </div>
      {msg && <div style={{ background: '#f0f0f0', padding: 8, marginBottom: 12 }}>{msg}</div>}

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
          <pre style={{ background:'#f9f9f9', padding:8, overflow:'auto' }}>{JSON.stringify(meta, null, 2)}</pre>
          <div style={{ marginBottom:8 }}>
            Dam location (lon, lat): <input value={lon} onChange={e=>setLon(parseFloat(e.target.value)||lon)} /> <input value={lat} onChange={e=>setLat(parseFloat(e.target.value)||lat)} />
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
              <pre style={{ background:'#f9f9f9', padding:8 }}>{JSON.stringify(preview, null, 2)}</pre>
              <button disabled={loading} onClick={()=>setStep(4)}>Next</button>
            </div>
          )}
        </div>
      )}

      {step===4 && (
        <div>
          <h3>Prepare Scenario & Case</h3>
          <button disabled={loading} onClick={handlePrepare}>Prepare</button>
          {prepareRes && (
            <div style={{ marginTop:12 }}>
              <pre style={{ background:'#f9f9f9', padding:8 }}>{JSON.stringify(prepareRes, null, 2)}</pre>
              <button disabled={loading} onClick={()=>setStep(5)}>Next</button>
            </div>
          )}
        </div>
      )}

      {step===5 && (
        <div>
          <h3>Validate & Run Pipeline</h3>
          <button disabled={loading} onClick={handleRunPipeline}>Run Terrain+SPH Pipeline</button>
          {pipelineRes && (
            <div style={{ marginTop:12 }}>
              <pre style={{ background:'#f9f9f9', padding:8, maxHeight:400, overflow:'auto' }}>{pipelineRes.stdout || pipelineRes.stderr}</pre>
              <button disabled={loading || !pipelineRes.ok} onClick={handleCreateJob}>Create Simulation Job</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
