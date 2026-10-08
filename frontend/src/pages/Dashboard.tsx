import React, { useMemo } from 'react'
import { FloodMap } from '../map/FloodMap'
import { MapToolbar } from '../components/MapToolBar'
import { MetricCard } from '../components/MetricCard'
import type { DamGeoJSON } from '../types/dam'
import type { JobSummary } from '../types/simulation'

interface DashboardProps {
  damsData: DamGeoJSON | null
  jobs: JobSummary[]
  onOpenDam: (damId: string) => void
  onNewSimulation: () => void
  onHistory: () => void
  onOpenRun: (jobId: string) => void
  onViewResult: (jobId: string) => void
}

const fmtAgo = (iso: string | null): string => {
  if (!iso) return '—'
  const delta = (Date.now() - new Date(iso).getTime()) / 1000
  if (delta < 60) return 'just now'
  if (delta < 3600) return `${Math.floor(delta / 60)} min ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)} h ago`
  return new Date(iso).toLocaleDateString()
}

export const Dashboard: React.FC<DashboardProps> = ({
  damsData,
  jobs,
  onOpenDam,
  onNewSimulation,
  onHistory,
  onOpenRun,
  onViewResult,
}) => {
  const active = jobs.filter(
    (j) => j.status === 'running' || j.status === 'queued',
  )
  const resultsReady = jobs.filter((j) => j.result.available)

  const recent = useMemo(
    () =>
      [...jobs]
        .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
        .slice(0, 6),
    [jobs],
  )

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Hydro Twin dashboard</h1>
          <p className="page__subtitle">
            Dam-break flood simulation powered by the Scenario Runner and
            DualSPHysics. Pick a dam, configure a scenario and run it — no
            terminal required.
          </p>
        </div>
        <div className="page__actions">
          <button type="button" className="btn" onClick={onHistory}>
            History
          </button>
          <button
            type="button"
            className="btn btn--accent btn--lg"
            onClick={onNewSimulation}
          >
            ▶ New simulation
          </button>
        </div>
      </div>

      <div className="app-metrics">
        <MetricCard
          label="Dams"
          value={damsData ? damsData.features.length.toLocaleString() : '—'}
          unit=""
          description="structures in the inventory"
        />
        <MetricCard
          label="Simulations"
          value={jobs.length}
          unit=""
          description="web jobs and CLI runs"
        />
        <MetricCard
          label="Active now"
          value={active.length}
          unit=""
          status={active.length > 0 ? 'warning' : 'normal'}
          description={
            active.length > 0 ? 'a simulation is running' : 'backend idle'
          }
        />
        <MetricCard
          label="Results ready"
          value={resultsReady.length}
          unit=""
          status="normal"
          description="processed result packages"
        />
      </div>

      <div className="dash-grid">
        <div>
          <div className="dash-map">
            <FloodMap
              damsData={damsData}
              onDamSelect={onOpenDam}
            />
            <MapToolbar />
          </div>
        </div>

        <div className="panel">
          <div className="panel__title">Recent simulations</div>
          {recent.length === 0 ? (
            <div className="empty-state">
              No simulations yet — run your first one.
            </div>
          ) : (
            <ul className="recent-list">
              {recent.map((job) => (
                <li key={job.id} className="recent-item">
                  <div>
                    <div className="recent-item__title">
                      {job.scenario_display}
                    </div>
                    <div className="recent-item__meta">
                      {fmtAgo(job.created_at)} ·{' '}
                      {job.duration_seconds != null
                        ? `${job.duration_seconds.toFixed(0)} s`
                        : job.stage ?? job.status}
                    </div>
                  </div>
                  <div className="row-actions">
                    <span className={`status-pill status-pill--${job.status}`}>
                      {job.status}
                    </span>
                    {job.status === 'running' || job.status === 'queued' ? (
                      <button
                        type="button"
                        className="btn btn--sm"
                        onClick={() => onOpenRun(job.id)}
                      >
                        Watch
                      </button>
                    ) : job.result.available ? (
                      <button
                        type="button"
                        className="btn btn--sm btn--accent"
                        onClick={() => onViewResult(job.id)}
                      >
                        View
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
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
