import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import './styles/simulation.css'

import { Header } from './components/Header'
import { NavBar, type View } from './components/NavBar'
import { StatusBadge } from './components/StatusBadge'
import {
  fetchDams,
  fetchJobs,
  fetchScenarios,
  fetchSystemStatus,
} from './services/api'
import {
  BackendHealthTracker,
  classifyRequestFailure,
  describeRequestFailure,
  type Reachability,
} from './services/backendHealth'

import { ConfigurePage } from './pages/ConfigurePage'
import { Dashboard } from './pages/Dashboard'
import { DamsPage } from './pages/DamsPage'
import { HistoryPage } from './pages/HistoryPage'
import { RunPage } from './pages/RunPage'
import { SettingsPage } from './pages/SettingsPage'
import { ViewerPage } from './pages/ViewerPage'
import { OnboardPage } from './pages/OnboardPage'

import type { DamGeoJSON } from './types/dam'
import type {
  JobSummary,
  JobsListResponse,
  ScenarioInfo,
} from './types/simulation'

function App() {
  const [view, setView] = useState<View>('dashboard')

  // data
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([])
  const [scenariosLoading, setScenariosLoading] = useState(true)
  const [damsData, setDamsData] = useState<DamGeoJSON | null>(null)
  const [damsLoading, setDamsLoading] = useState(true)
  const [damsError, setDamsError] = useState<string | null>(null)
  const [backendUp, setBackendUp] = useState(true)
  const [jobsLoaded, setJobsLoaded] = useState(false)
  const [jobsError, setJobsError] = useState<string | null>(null)

  // Backend connectivity is owned by a single latest-wins tracker so an older
  // request can never override a newer one. It lives in a ref (stable across
  // renders) and is created lazily on first use.
  const healthRef = useRef<BackendHealthTracker | null>(null)
  const getHealth = useCallback((): BackendHealthTracker => {
    const existing = healthRef.current
    if (existing !== null) return existing
    const created = new BackendHealthTracker()
    healthRef.current = created
    return created
  }, [])

  // De-duplicate in-flight refreshes: a poll tick, a manual refresh and the
  // StrictMode double-invoke must not pile up concurrent requests.
  const jobsInFlight = useRef<Promise<JobsListResponse | null> | null>(null)

  // navigation targets
  const [runJobId, setRunJobId] = useState<string | null>(null)
  const [viewerJobId, setViewerJobId] = useState<string | null>(null)
  const [selectedDamId, setSelectedDamId] = useState<string | null>(null)
  const [preferredScenario, setPreferredScenario] = useState<string | null>(null)

  // ---- data loading ---------------------------------------------------------

  // Dedicated health probe (`/system/status`). Used to disambiguate a data
  // request that failed without a server response; it never changes the state
  // by itself — the tracker folds its result in.
  const probeBackendHealth = useCallback(async (): Promise<Reachability> => {
    try {
      await fetchSystemStatus()
      return 'reachable'
    } catch (err) {
      return classifyRequestFailure(err)
    }
  }, [])

  const refreshJobs = useCallback(async (): Promise<JobsListResponse | null> => {
    // Reuse the request already in flight instead of starting a duplicate.
    if (jobsInFlight.current) return jobsInFlight.current
    const health = getHealth()
    const seq = health.begin()
    const run = (async () => {
      try {
        const res = await fetchJobs()
        setJobs(res.jobs)
        setActiveJobId(res.active_job_id)
        setJobsError(null)
        setJobsLoaded(true)
        setBackendUp(health.settle(seq, 'reachable'))
        return res
      } catch (err) {
        setJobsLoaded(true)
        const outcome = classifyRequestFailure(err)
        if (outcome !== 'canceled') {
          setJobsError(
            describeRequestFailure(err, 'Could not load simulation jobs'),
          )
        }
        // A failing data endpoint does not by itself mark the backend offline:
        // settleDataRequest confirms with the health endpoint first, and
        // ignores the outcome entirely if this request is already stale.
        setBackendUp(
          await health.settleDataRequest(seq, outcome, probeBackendHealth),
        )
        return null
      } finally {
        jobsInFlight.current = null
      }
    })()
    jobsInFlight.current = run
    return run
  }, [getHealth, probeBackendHealth])

  useEffect(() => {
    void refreshJobs()
    void fetchScenarios()
      .then(setScenarios)
      .catch(() => setScenarios([]))
      .finally(() => setScenariosLoading(false))
    void fetchDams()
      .then(setDamsData)
      .catch((err: unknown) => {
        setDamsError(describeRequestFailure(err, 'Dam inventory failed to load'))
      })
      .finally(() => setDamsLoading(false))
  }, [refreshJobs])

  const anyRunning = jobs.some(
    (j) => j.status === 'running' || j.status === 'queued',
  )

  // Poll faster while a simulation is live, gently otherwise. The next tick is
  // scheduled only once the current one settles, so ticks can never pile up;
  // the in-flight de-dupe in refreshJobs handles manual refreshes too.
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const interval = anyRunning ? 2500 : 12000
    const tick = () => {
      void refreshJobs().finally(() => {
        if (!cancelled) timer = setTimeout(tick, interval)
      })
    }
    timer = setTimeout(tick, interval)
    return () => {
      cancelled = true
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [anyRunning, refreshJobs])

  // ---- handlers ---------------------------------------------------------------
  const openRun = useCallback((jobId: string) => {
    setRunJobId(jobId)
    setView('run')
  }, [])

  const openResult = useCallback((jobId: string) => {
    setViewerJobId(jobId)
    setView('viewer')
  }, [])

  const openDam = useCallback((damId: string) => {
    setSelectedDamId(damId)
    setView('dams')
  }, [])

  const openConfigure = useCallback((scenarioName: string | null) => {
    setPreferredScenario(scenarioName)
    setView('configure')
  }, [])

  const jobStarted = useCallback(
    (jobId: string) => {
      setRunJobId(jobId)
      setActiveJobId(jobId)
      setView('run')
      void refreshJobs()
    },
    [refreshJobs],
  )

  const currentRunJobId = runJobId ?? activeJobId

  const statusNode = useMemo(() => {
    if (!backendUp) {
      return <StatusBadge status="error" text="API offline" />
    }
    const active = jobs.find(
      (j) => j.id === (activeJobId ?? '') && j.status === 'running',
    )
    if (active) {
      return (
        <StatusBadge
          status="simulation"
          text={`Simulating · ${active.scenario_display}`}
        />
      )
    }
    return <StatusBadge status="success" text="Backend ready" />
  }, [backendUp, jobs, activeJobId])

  const renderView = () => {
    switch (view) {
      case 'dashboard':
        return (
          <Dashboard
            damsData={damsData}
            jobs={jobs}
            onOpenDam={openDam}
            onNewSimulation={() => openConfigure(null)}
            onHistory={() => setView('history')}
            onOpenRun={openRun}
            onViewResult={openResult}
            onOpenSettings={() => setView('settings')}
          />
        )
      case 'dams':
        return (
          <DamsPage
            damsData={damsData}
            loading={damsLoading}
            loadError={damsError}
            selectedDamId={selectedDamId}
            onSelect={setSelectedDamId}
            scenarios={scenarios}
            onConfigure={(name) => openConfigure(name)}
          />
        )
      case 'onboard':
        return (
          <OnboardPage
            onNavigate={(v, jobId) => {
              if (v === 'run' && jobId) {
                jobStarted(jobId)
                return
              }
              setView(v)
            }}
          />
        )
      case 'configure':
        return (
          <ConfigurePage
            scenarios={scenarios}
            loading={scenariosLoading}
            preferredScenario={preferredScenario}
            onStarted={jobStarted}
            onWatchRunning={openRun}
            onOpenSettings={() => setView('settings')}
          />
        )
      case 'run':
        return currentRunJobId ? (
          <RunPage
            key={currentRunJobId}
            jobId={currentRunJobId}
            onOpenResult={openResult}
            onBack={() => setView('history')}
          />
        ) : (
          <div className="page">
            <div className="empty-state">
              No simulation has been started yet.
              <div className="mt-4">
                <button
                  type="button"
                  className="btn btn--accent"
                  onClick={() => openConfigure(null)}
                >
                  Configure a simulation
                </button>
              </div>
            </div>
          </div>
        )
      case 'history':
        return (
          <HistoryPage
            jobs={jobs}
            error={jobsError}
            onRefresh={() => void refreshJobs()}
            onOpenRun={openRun}
            onViewResult={openResult}
          />
        )
      case 'viewer':
        return viewerJobId ? (
          <ViewerPage jobId={viewerJobId} onBack={() => setView('history')} />
        ) : (
          <div className="page">
            <div className="empty-state">No result selected.</div>
          </div>
        )
      case 'settings':
        return <SettingsPage onBack={() => setView('dashboard')} />
    }
  }

  return (
    <div className="app-shell">
      <Header status={statusNode} />
      <NavBar
        view={view}
        onNavigate={(v) => {
          if (v === 'run') {
            // Always surface the live job if one is running.
            if (activeJobId) {
              setRunJobId(activeJobId)
            } else if (!currentRunJobId) {
              setView('history')
              return
            }
          }
          setView(v)
        }}
        activeJobRunning={anyRunning}
        hasActiveJob={currentRunJobId != null}
      />
      <main className="app-main app-main--full">
        {renderView()}
      </main>
      {jobsLoaded && !backendUp && (
        <div className="error-banner" style={{ margin: 'var(--space-3)' }}>
          Backend unreachable — start it with{' '}
          <code>uvicorn app.main:app --port 8000</code> from{' '}
          <code>backend/</code>.
        </div>
      )}
    </div>
  )
}

export default App
