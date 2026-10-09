import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  exportUrl,
  fetchJob,
  processJobResult,
} from '../services/api'
import { loadResult, sliceFrame, type LoadedResult } from '../services/resultData'
import { AnalyticsPanel } from '../components/viewer/AnalyticsPanel'
import { ParticleCanvas } from '../components/viewer/ParticleCanvas'
import {
  TimelineControls,
  type ViewerLayers,
} from '../components/viewer/TimelineControls'
import { ViewerMap } from '../components/viewer/ViewerMap'
import { OutputPaths } from '../components/OutputPaths'
import type { JobSummary, ResultManifest } from '../types/simulation'

interface ViewerPageProps {
  jobId: string
  onBack: () => void
}

type Tab = 'map' | 'satellite' | 'viewer' | 'analytics'

/** Human label for how a result was produced (from the backend record). */
const runTypeLabel = (job: JobSummary | null): string => {
  if (!job) return 'Result'
  if (job.type === 'import') return 'Imported result'
  if (job.source === 'runner') return 'CLI run'
  return 'Web simulation'
}

export const ViewerPage: React.FC<ViewerPageProps> = ({ jobId, onBack }) => {
  const [job, setJob] = useState<JobSummary | null>(null)
  const [result, setResult] = useState<LoadedResult | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [missing, setMissing] = useState(false)
  const [tab, setTab] = useState<Tab>('viewer')

  const [frame, setFrame] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [layers, setLayers] = useState<ViewerLayers>({
    terrain: true,
    water: true,
    dam: true,
  })
  const [resetKey, setResetKey] = useState(0)
  const [processing, setProcessing] = useState(false)
  const [showOutputs, setShowOutputs] = useState(false)

  const frameRef = useRef(0)
  // Keep the latest frame available to long-lived timers without writing the
  // ref during render.
  useEffect(() => {
    frameRef.current = frame
  })

  // ---- data loading --------------------------------------------------------
  useEffect(() => {
    let cancelled = false

    void (async () => {
      setResult(null)
      setLoadError(null)
      setMissing(false)
      setFrame(0)
      setPlaying(false)

      try {
        const summary = await fetchJob(jobId)
        if (!cancelled) setJob(summary)
      } catch {
        /* header-only; ignore */
      }
      try {
        const loaded = await loadResult(jobId)
        if (!cancelled) setResult(loaded)
      } catch (err) {
        if (cancelled) return
        const status = (err as { response?: { status?: number } }).response
          ?.status
        if (status === 404) {
          setMissing(true)
          setLoadError(
            'No processed result package for this run yet.',
          )
        } else {
          setLoadError('Could not load the result package from the backend.')
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [jobId])

  const processNow = async () => {
    setProcessing(true)
    try {
      await processJobResult(jobId)
      setMissing(false)
      setLoadError(null)
      const loaded = await loadResult(jobId)
      setResult(loaded)
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        .response?.data?.detail
      setLoadError(detail ?? 'Processing failed.')
    } finally {
      setProcessing(false)
    }
  }

  // ---- playback ------------------------------------------------------------
  const manifest = result?.manifest ?? null

  useEffect(() => {
    if (!playing || !manifest) return
    const count = manifest.frames.count
    if (count < 2) return

    // Simulated seconds per real second: frame interval derived from the
    // recorded frame times (or a nominal 0.05 s when unknown).
    let meanDt = 0.05
    if (manifest.frames.time_known && count > 1) {
      const t = manifest.frames.times
      meanDt = (t[count - 1] - t[0]) / (count - 1)
      if (!Number.isFinite(meanDt) || meanDt <= 0) meanDt = 0.05
    }

    let raf = 0
    let last = performance.now()
    let acc = 0 // accumulated frames

    const tick = (now: number) => {
      const dt = Math.min(0.25, (now - last) / 1000)
      last = now
      acc += (dt * speed) / meanDt

      let f = frameRef.current
      let advanced = false
      while (acc >= 1 && f < count - 1) {
        acc -= 1
        f += 1
        advanced = true
      }
      if (f >= count - 1 && acc >= 1) {
        acc = 0
        setFrame(count - 1)
        setPlaying(false)
        return
      }
      if (advanced) setFrame(f)
      raf = requestAnimationFrame(tick)
    }

    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed, manifest])

  const seek = useCallback((f: number) => {
    setFrame(f)
    frameRef.current = f
  }, [])

  const togglePlay = useCallback(() => {
    setPlaying((p) => {
      if (!p && manifest && frameRef.current >= manifest.frames.count - 1) {
        setFrame(0)
        frameRef.current = 0
      }
      return !p
    })
  }, [manifest])

  // ---- render --------------------------------------------------------------
  if (!result) {
    return (
      <div className="page page--viewer">
        <div className="viewer-header">
          <button type="button" className="btn btn--sm" onClick={onBack}>
            ← Back
          </button>
          <span className="viewer-header__title">{jobId}</span>
        </div>
        <div className="viewer-body">
          {loadError ? (
            <div style={{ padding: 'var(--space-5)' }}>
              <div className="error-banner">{loadError}</div>
              {missing && job && job.type !== 'import' && (
                <button
                  type="button"
                  className="btn btn--accent"
                  onClick={() => void processNow()}
                  disabled={processing}
                >
                  {processing ? (
                    <>
                      <span className="spinner" /> Processing…
                    </>
                  ) : (
                    'Process result package'
                  )}
                </button>
              )}
            </div>
          ) : (
            <div className="loading-block">
              <span className="spinner" /> Loading result package…
            </div>
          )}
        </div>
      </div>
    )
  }

  const m: ResultManifest = result.manifest
  const timeKnown = m.frames.time_known
  const simEndS = timeKnown
    ? (m.frames.times[m.frames.count - 1] ?? 0)
    : null
  const scenarioName = job?.scenario ?? m.source.scenario ?? null
  const runLabel =
    job?.run_id ?? (m.source.run_id as string | undefined) ?? null

  const timeline = (
    <TimelineControls
      frame={frame}
      frameCount={m.frames.count}
      timeKnown={timeKnown}
      times={m.frames.times}
      playing={playing}
      speed={speed}
      layers={layers}
      onSeek={seek}
      onTogglePlay={togglePlay}
      onSpeed={setSpeed}
      onToggleLayer={(key) =>
        setLayers((l) => ({ ...l, [key]: !l[key] }))
      }
      onResetCamera={() => setResetKey((k) => k + 1)}
    />
  )

  return (
    <div className="page page--viewer">
      <div className="viewer-header">
        <button type="button" className="btn btn--sm" onClick={onBack}>
          ← Back
        </button>
        <div className="viewer-header__info">
          <div className="viewer-header__title">
            {job?.dam_name ?? job?.scenario_display ?? m.source.scenario ?? 'Result'}
          </div>
          <div className="viewer-header__meta">
            <span>
              Scenario <strong>{scenarioName ?? '—'}</strong>
            </span>
            <span>·</span>
            <span>{runTypeLabel(job)}</span>
            {runLabel && (
              <>
                <span>·</span>
                <span>
                  Run <code className="viewer-header__id">{runLabel}</code>
                </span>
              </>
            )}
            <span>·</span>
            <span>{m.frames.count} frames</span>
            <span>·</span>
            <span>
              Simulation time{' '}
              <strong>
                {simEndS != null ? `${simEndS.toFixed(2)} s` : 'unknown'}
              </strong>
            </span>
            <span>·</span>
            <span>
              {m.speed.global_max.toFixed(2)} {m.speed.unit} peak
            </span>
          </div>
          <div className="viewer-header__frame">
            Frame <strong>{frame + 1}</strong> / {m.frames.count}
            {timeKnown && (
              <>
                {' · '}t ={' '}
                <strong>{(m.frames.times[frame] ?? 0).toFixed(2)}</strong> s
              </>
            )}
          </div>
        </div>
        <div className="nav-spacer" />
        <button
          type="button"
          className={`btn btn--sm${showOutputs ? ' btn--accent' : ''}`}
          onClick={() => setShowOutputs((v) => !v)}
          aria-expanded={showOutputs}
          title="Output locations reported by the backend"
        >
          Outputs
        </button>
        <a className="btn" href={exportUrl(jobId)} download={`${jobId}.zip`}>
          ⬇ Export ZIP
        </a>
      </div>

      {showOutputs && (
        <OutputPaths
          paths={job?.paths}
          note="Reported by the backend for this run (repo-relative unless outside the repository)."
        />
      )}

      <div className="viewer-tabs">
        {(
          [
            ['map', '2D Map'],
            ['satellite', 'Satellite'],
            ['viewer', '3D Simulation'],
            ['analytics', 'Analytics'],
          ] as [Tab, string][]
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={`nav-tab${tab === id ? ' nav-tab--active' : ''}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="viewer-body">
        {(tab === 'map' || tab === 'satellite') && (
          <>
            <ViewerMap
              jobId={jobId}
              manifest={m}
              frame={frame}
              scenario={scenarioName}
              basemap={tab === 'satellite' ? 'satellite' : 'standard'}
            />
            {timeline}
          </>
        )}

        {tab === 'viewer' && (
          <>
            <div className="viewer-3d">
              <ParticleCanvas
                manifest={m}
                frames={result.frames}
                terrain={result.terrain}
                frame={frame}
                layers={layers}
                resetKey={resetKey}
              />
              <div className="viewer-hud">
                <div className="hud-card">
                  Frame <strong>{frame + 1}</strong> / {m.frames.count}
                  {timeKnown && (
                    <>
                      {' · '}t ={' '}
                      <strong>{(m.frames.times[frame] ?? 0).toFixed(2)}</strong> s
                    </>
                  )}
                </div>
                <div className="hud-card">
                  Speed{' '}
                  <strong>
                    {(m.frames.counts[frame] > 0
                      ? speedAtFrame(m, result.frames, frame)
                      : 0
                    ).toFixed(2)}
                  </strong>{' '}
                  {m.speed.unit} (current frame max)
                  <div className="speed-legend">
                    <span className="muted">0</span>
                    <span className="speed-legend__bar" />
                    <span className="muted">{m.speed.global_max.toFixed(1)}</span>
                  </div>
                </div>
              </div>
              <div className="viewer-hint">
                drag orbit · wheel zoom · shift/right-drag pan
              </div>
            </div>
            {timeline}
          </>
        )}

        {tab === 'analytics' && (
          <AnalyticsPanel manifest={m} jobId={jobId} frame={frame} />
        )}
      </div>
    </div>
  )
}

/** Max particle speed of one frame (for the HUD readout). */
function speedAtFrame(
  manifest: ResultManifest,
  frames: Float32Array,
  frame: number,
): number {
  const { speed } = sliceFrame(manifest, frames, frame)
  let max = 0
  for (let i = 0; i < speed.length; i += 1) {
    if (speed[i] > max) max = speed[i]
  }
  return max
}
