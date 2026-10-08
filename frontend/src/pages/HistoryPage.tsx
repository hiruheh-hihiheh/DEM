import React, { useMemo, useState } from 'react'
import { ImportDropzone } from '../components/ImportDropzone'
import { processJobResult } from '../services/api'
import type { JobStatus, JobSummary } from '../types/simulation'

interface HistoryPageProps {
  jobs: JobSummary[]
  onRefresh: () => void
  onOpenRun: (jobId: string) => void
  onViewResult: (jobId: string) => void
}

const STATUS_FILTERS: { id: JobStatus | 'all'; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'running', label: 'Running' },
  { id: 'completed', label: 'Completed' },
  { id: 'failed', label: 'Failed' },
]

const fmtDuration = (seconds: number | null): string => {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const m = Math.floor(seconds / 60)
  return `${m}m ${Math.round(seconds - m * 60)}s`
}

export const HistoryPage: React.FC<HistoryPageProps> = ({
  jobs,
  onRefresh,
  onOpenRun,
  onViewResult,
}) => {
  const [filter, setFilter] = useState<JobStatus | 'all'>('all')
  const [showImport, setShowImport] = useState(false)
  const [processingId, setProcessingId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const rows = useMemo(() => {
    const filtered =
      filter === 'all' ? jobs : jobs.filter((j) => j.status === filter)
    return [...filtered].sort((a, b) =>
      (b.created_at ?? '').localeCompare(a.created_at ?? ''),
    )
  }, [jobs, filter])

  const process = async (jobId: string) => {
    setProcessingId(jobId)
    setNotice(null)
    try {
      await processJobResult(jobId)
      setNotice(`Result package for ${jobId} is ready.`)
      onRefresh()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        .response?.data?.detail
      setNotice(detail ?? `Processing ${jobId} failed.`)
    } finally {
      setProcessingId(null)
    }
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Simulation history</h1>
          <p className="page__subtitle">
            Web jobs and CLI runs ({jobs.length} total). Result packages can be
            processed on demand and opened in the viewer.
          </p>
        </div>
        <div className="page__actions">
          <div className="segment">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                className={`segment__btn${filter === f.id ? ' segment__btn--active' : ''}`}
                onClick={() => setFilter(f.id)}
              >
                {f.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="btn"
            onClick={() => setShowImport((v) => !v)}
          >
            {showImport ? 'Close import' : '⬆ Import ZIP'}
          </button>
          <button type="button" className="btn" onClick={onRefresh}>
            ↻ Refresh
          </button>
        </div>
      </div>

      {showImport && (
        <ImportDropzone
          onImported={(jobId) => {
            setShowImport(false)
            onOpenRun(jobId)
          }}
        />
      )}

      {notice && (
        <div className="param-note mb-3" role="status">
          {notice}
        </div>
      )}

      {rows.length === 0 ? (
        <div className="empty-state">
          No simulations match this filter yet. Configure one from
          “New simulation”.
        </div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Scenario</th>
                <th>Dam</th>
                <th>Started</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Particles</th>
                <th>Frames</th>
                <th>Result</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((job) => {
                const metrics = job.result.available ? job.result.metrics : null
                const needsProcess =
                  job.status === 'completed' &&
                  !job.result.available &&
                  job.type !== 'import'
                return (
                  <tr key={job.id}>
                    <td className="mono">{job.id}</td>
                    <td>{job.scenario_display}</td>
                    <td>{job.dam_id ?? job.scenario}</td>
                    <td>
                      {job.created_at
                        ? new Date(job.created_at).toLocaleString()
                        : '—'}
                    </td>
                    <td>
                      <span className={`status-pill status-pill--${job.status}`}>
                        {job.status === 'running' && (
                          <span className="status-pill__dot" />
                        )}
                        {job.status}
                      </span>
                    </td>
                    <td>{fmtDuration(job.duration_seconds)}</td>
                    <td>{metrics ? metrics.particles.max.toLocaleString() : '—'}</td>
                    <td>{metrics ? metrics.frames : job.frame_progress?.total ?? '—'}</td>
                    <td>
                      {job.result.available ? (
                        <span className="chip chip--success">
                          <strong>ready</strong>
                        </span>
                      ) : job.status === 'completed' ? (
                        <span className="muted">not processed</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <div className="row-actions">
                        {job.result.available && (
                          <button
                            type="button"
                            className="btn btn--sm btn--accent"
                            onClick={() => onViewResult(job.id)}
                          >
                            View
                          </button>
                        )}
                        {job.status === 'running' || job.status === 'queued' ? (
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => onOpenRun(job.id)}
                          >
                            Watch
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => onOpenRun(job.id)}
                          >
                            Details
                          </button>
                        )}
                        {needsProcess && (
                          <button
                            type="button"
                            className="btn btn--sm"
                            disabled={processingId === job.id}
                            onClick={() => void process(job.id)}
                          >
                            {processingId === job.id ? 'Processing…' : 'Process'}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
