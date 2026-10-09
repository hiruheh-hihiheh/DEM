import React, { useCallback, useEffect, useRef, useState } from 'react'
import { fetchJob, processJobResult } from '../services/api'
import { LogConsole } from '../components/LogConsole'
import { OutputPaths } from '../components/OutputPaths'
import { ProgressStages } from '../components/ProgressStages'
import type { JobSummary } from '../types/simulation'

interface RunPageProps {
  jobId: string
  onOpenResult: (jobId: string) => void
  onBack: () => void
}

const POLL_MS = 1500

export const RunPage: React.FC<RunPageProps> = ({
  jobId,
  onOpenResult,
  onBack,
}) => {
  const [job, setJob] = useState<JobSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [processing, setProcessing] = useState(false)
  const seenJobId = useRef<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const next = await fetchJob(jobId)
      setJob(next)
      setError(null)
      return next
    } catch (err) {
      const status = (err as { response?: { status?: number } }).response?.status
      if (status === 404) {
        setError('This job no longer exists (records are kept under simulations/jobs/).')
      } else {
        setError('Backend unreachable — is the API server running?')
      }
      return null
    }
  }, [jobId])

  // Initial load + reset when switching jobs.
  useEffect(() => {
    if (seenJobId.current !== jobId) {
      seenJobId.current = jobId
      setJob(null)
      setError(null)
      void refresh()
    }
  }, [jobId, refresh])

  // Poll while active.
  useEffect(() => {
    if (!job) return
    if (job.status !== 'queued' && job.status !== 'running') return
    const timer = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(timer)
  }, [job, refresh])

  const processResult = async () => {
    setProcessing(true)
    try {
      await processJobResult(jobId)
      await refresh()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        .response?.data?.detail
      setError(detail ?? 'Processing failed.')
    } finally {
      setProcessing(false)
    }
  }

  if (!job && !error) {
    return (
      <div className="page">
        <div className="loading-block">
          <span className="spinner" /> Loading job…
        </div>
      </div>
    )
  }

  if (!job) {
    return (
      <div className="page">
        <div className="error-banner">{error}</div>
        <button type="button" className="btn" onClick={onBack}>
          ← Back to history
        </button>
      </div>
    )
  }

  const active = job.status === 'queued' || job.status === 'running'
  const pct = Math.round(job.progress * 100)
  const frames = job.frame_progress
  const hasResult = job.result.available

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">{job.dam_name ?? job.scenario_display}</h1>
          <p className="page__subtitle">
            <span className="mono">{job.id}</span>
            {' · '}
            {job.source === 'web' ? 'web job' : 'CLI run'}
            {job.run_id && (
              <>
                {' · '}Run <span className="mono">{job.run_id}</span>
              </>
            )}
            {job.created_at && ` · started ${new Date(job.created_at).toLocaleString()}`}
          </p>
        </div>
        <div className="page__actions">
          <span className={`status-pill status-pill--${job.status}`}>
            {job.status === 'running' && <span className="status-pill__dot" />}
            {job.status}
          </span>
          <button type="button" className="btn" onClick={onBack}>
            History
          </button>
          {hasResult && (
            <button
              type="button"
              className="btn btn--accent"
              onClick={() => onOpenResult(jobId)}
            >
              Open result viewer →
            </button>
          )}
        </div>
      </div>

      {job.error && <div className="error-banner">{job.error}</div>}
      {error && job && <div className="error-banner">{error}</div>}

      <div className="run-layout">
        <div className="panel">
          <div className="panel__title">
            {job.status === 'completed'
              ? 'Run complete'
              : job.status === 'failed'
                ? 'Run failed'
                : active
                  ? 'Simulation in progress'
                  : 'Queued'}
          </div>

          <div className="overall-bar">
            <div
              className={`overall-bar__fill${job.status === 'failed' ? ' overall-bar__fill--failed' : ''}`}
              style={{ width: `${pct}%` }}
            />
          </div>

          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 'var(--font-xs)',
              color: 'var(--color-text-muted)',
            }}
          >
            <span>{pct}%</span>
            <span>
              {frames
                ? `frame ${frames.done}/${frames.total}`
                : job.duration_seconds != null
                  ? `${job.duration_seconds.toFixed(1)} s elapsed`
                  : '—'}
            </span>
          </div>

          {job.duration_seconds != null && (
            <p className="param-note">
              Total duration: {job.duration_seconds.toFixed(1)} s
              {job.validated_config === false && ' · custom overrides applied'}
            </p>
          )}

          <div className="mt-4">
            <ProgressStages stages={job.stages} />
          </div>

          {hasResult && job.result.metrics && (
            <div className="mt-4" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              <span className="chip">
                Peak speed <strong>{job.result.metrics.peak_speed.value.toFixed(2)}</strong>
              </span>
              <span className="chip">
                Wet area <strong>{job.result.metrics.wet_area.max.toFixed(2)}</strong>
              </span>
              <span className="chip">
                Particles <strong>{job.result.metrics.particles.max.toLocaleString()}</strong>
              </span>
              <span className="chip">
                Frames <strong>{job.result.metrics.frames}</strong>
              </span>
            </div>
          )}

          {job.status === 'completed' && !hasResult && job.type !== 'import' && (
            <button
              type="button"
              className="btn btn--accent mt-4"
              onClick={() => void processResult()}
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

          {active && (
            <p className="param-note mt-3">
              <span className="spinner" /> {job.stage ?? 'waiting'} — this page
              refreshes automatically.
            </p>
          )}
        </div>

        <div className="panel">
          <div className="panel__title">Runner output</div>
          <LogConsole jobId={jobId} live={active} />
          <p className="param-note mt-3">
            Captured stdout/stderr of <code>scripts/run_scenario.py</code> for
            this job (also stored in <code>simulations/jobs/{jobId}.log</code>).
          </p>
        </div>
      </div>

      <OutputPaths
        paths={job.paths}
        note="Reported by the backend for this run (repo-relative unless outside the repository)."
      />
    </div>
  )
}
