import React, { useMemo, useState } from 'react'
import { FloodMap } from '../map/FloodMap'
import { MapToolbar } from '../components/MapToolBar'
import { MetricCard } from '../components/MetricCard'
import type { BasemapMode } from '../map/basemaps'
import type { DamGeoJSON } from '../types/dam'
import type { JobSummary } from '../types/simulation'
import { useSystemStatus } from '../services/useSystemStatus'

interface DashboardProps {
  damsData: DamGeoJSON | null
  jobs: JobSummary[]
  onOpenDam: (damId: string) => void
  onNewSimulation: () => void
  onHistory: () => void
  onOpenRun: (jobId: string) => void
  onViewResult: (jobId: string) => void
  /** Open the Settings page (DualSPHysics + system status). */
  onOpenSettings: () => void
}

const fmtAgo = (iso: string | null): string => {
  if (!iso) return '—'
  const delta = (Date.now() - new Date(iso).getTime()) / 1000
  if (delta < 60) return 'just now'
  if (delta < 3600) return `${Math.floor(delta / 60)} min ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)} h ago`
  return new Date(iso).toLocaleDateString()
}

/** One row of the system-readiness card. */
const StatusRow: React.FC<{
  label: string
  ready: boolean
  detail?: string | null
}> = ({ label, ready, detail }) => (
  <li className={`system-status__row${ready ? '' : ' system-status__row--warn'}`}>
    <span className="system-status__icon" aria-hidden="true">
      {ready ? '✓' : '⚠'}
    </span>
    <span className="system-status__label">{label}</span>
    <span className="system-status__detail" title={detail ?? undefined}>
      {detail ?? (ready ? 'ready' : 'not ready')}
    </span>
  </li>
)

export const Dashboard: React.FC<DashboardProps> = ({
  damsData,
  jobs,
  onOpenDam,
  onNewSimulation,
  onHistory,
  onOpenRun,
  onViewResult,
  onOpenSettings,
}) => {
  const [basemap, setBasemap] = useState<BasemapMode>('standard')
  const { status: systemStatus, loading: statusLoading } = useSystemStatus()

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

  const damCount = damsData?.features.length ?? 0

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
          value={damsData ? damCount.toLocaleString() : '—'}
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

      {/* Map-first hero: full width, real height, resizable with the window. */}
      <section className="dash-hero" aria-label="Dam inventory map">
        <div className="dash-map dash-map--hero">
          <FloodMap
            key={basemap}
            basemap={basemap}
            damsData={damsData}
            onDamSelect={onOpenDam}
          />
          <MapToolbar
            basemap={basemap}
            onBasemapChange={setBasemap}
            statusText={
              damCount > 0
                ? `${damCount.toLocaleString()} dams loaded · ${basemap === 'satellite' ? 'Satellite' : 'Standard'} basemap`
                : 'Loading dam inventory…'
            }
          />
        </div>
      </section>

      <div className="dash-grid">
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
                      {job.dam_name ?? job.scenario_display}
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

        <div className="panel system-status">
          <div className="panel__title">
            System status
            <button
              type="button"
              className="btn btn--sm system-status__open"
              onClick={onOpenSettings}
            >
              Settings
            </button>
          </div>
          {statusLoading && !systemStatus ? (
            <div className="empty-state">Checking the backend…</div>
          ) : systemStatus ? (
            <>
              <ul className="system-status__list">
                <StatusRow
                  label="Backend"
                  ready={systemStatus.backend.ready}
                  detail={systemStatus.backend.detail}
                />
                <StatusRow
                  label="Dam data"
                  ready={systemStatus.dam_data.ready}
                  detail={systemStatus.dam_data.detail}
                />
                <StatusRow
                  label="DEM pipeline"
                  ready={systemStatus.dem_pipeline.ready}
                  detail={systemStatus.dem_pipeline.detail}
                />
                <StatusRow
                  label="Scenario Runner"
                  ready={systemStatus.scenario_runner.ready}
                  detail={systemStatus.scenario_runner.detail}
                />
                <StatusRow
                  label="DualSPHysics"
                  ready={systemStatus.dualsphysics.ready}
                  detail={
                    systemStatus.dualsphysics.ready
                      ? `READY · ${systemStatus.dualsphysics.root}`
                      : systemStatus.dualsphysics.configured
                        ? `NOT READY · ${systemStatus.dualsphysics.missing.join(', ') || systemStatus.dualsphysics.error || 'check Settings'}`
                        : 'NOT CONFIGURED — open Settings'
                  }
                />
              </ul>
              {!systemStatus.dualsphysics.ready && (
                <p className="system-status__hint">
                  Simulations need a valid DualSPHysics installation. Configure
                  it in <button type="button" className="link-btn" onClick={onOpenSettings}>Settings</button>.
                </p>
              )}
            </>
          ) : (
            <div className="empty-state">
              Status unavailable — is the backend running?
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
