import React, { useEffect, useMemo, useState } from 'react'
import { createJob } from '../services/api'
import { useSystemStatus } from '../services/useSystemStatus'
import type {
  CreateJobRequest,
  ScenarioInfo,
} from '../types/simulation'

interface ConfigurePageProps {
  scenarios: ScenarioInfo[]
  loading: boolean
  /** Dam preselected from the dams page, if any. */
  preferredScenario?: string | null
  onStarted: (jobId: string) => void
  onWatchRunning: (jobId: string) => void
  /** Open the Settings page (DualSPHysics configuration). */
  onOpenSettings?: () => void
}

type ScenarioType = 'normal' | 'partial' | 'full' | 'extreme'

/** Breach-width multipliers per scenario type (frontend presets only). */
const TYPE_MULTIPLIERS: Record<ScenarioType, number> = {
  partial: 0.5,
  normal: 1,
  full: 2,
  extreme: 3,
}

const TYPE_LABELS: { id: ScenarioType; label: string }[] = [
  { id: 'partial', label: 'Partial' },
  { id: 'normal', label: 'Normal' },
  { id: 'full', label: 'Full' },
  { id: 'extreme', label: 'Extreme' },
]

interface FormState {
  reservoirLevel: number
  breachWidth: number
  breachTime: number
  simulationTime: number
  particleSpacing: number
}

const formFromScenario = (s: ScenarioInfo): FormState => {
  const p = s.parameters
  return {
    reservoirLevel: 100,
    breachWidth: p?.breach_width ?? 1,
    breachTime: p?.breach_time ?? 2,
    simulationTime: p?.simulation_time ?? 6,
    particleSpacing: p?.particle_spacing ?? 0.1,
  }
}

export const ConfigurePage: React.FC<ConfigurePageProps> = ({
  scenarios,
  loading,
  preferredScenario,
  onStarted,
  onWatchRunning,
  onOpenSettings,
}) => {
  const readyScenarios = useMemo(
    () => scenarios.filter((s) => s.ready),
    [scenarios],
  )

  // Warn up-front when DualSPHysics cannot be resolved — the run would fail
  // anyway, and the user should know where to fix it.
  const { status: systemStatus } = useSystemStatus()

  const [scenarioName, setScenarioName] = useState<string>('')
  const [custom, setCustom] = useState(false)
  const [scenarioType, setScenarioType] = useState<ScenarioType>('normal')
  const [form, setForm] = useState<FormState | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runningJob, setRunningJob] = useState<string | null>(null)

  const scenario = useMemo(
    () => readyScenarios.find((s) => s.name === scenarioName) ?? null,
    [readyScenarios, scenarioName],
  )

  // Pick the initial scenario (preferred dam's scenario when provided).
  useEffect(() => {
    void (async () => {
      if (scenarioName || readyScenarios.length === 0) return
      const preferred =
        (preferredScenario &&
          readyScenarios.find((s) => s.name === preferredScenario)) ||
        readyScenarios.find((s) => s.name === 'chouldari') ||
        readyScenarios[0]
      setScenarioName(preferred.name)
      setForm(formFromScenario(preferred))
    })()
  }, [readyScenarios, scenarioName, preferredScenario])

  const resetTo = (s: ScenarioInfo) => {
    setForm(formFromScenario(s))
    setCustom(false)
    setScenarioType('normal')
    setError(null)
  }

  const base = scenario?.parameters ?? null
  const timeOut = base?.time_out ?? 0.05

  const validated =
    scenario != null &&
    form != null &&
    form.reservoirLevel === 100 &&
    form.breachWidth === (base?.breach_width ?? form.breachWidth) &&
    form.breachTime === (base?.breach_time ?? form.breachTime) &&
    form.simulationTime === (base?.simulation_time ?? form.simulationTime) &&
    form.particleSpacing === (base?.particle_spacing ?? form.particleSpacing)

  const applyType = (type: ScenarioType) => {
    if (!scenario || !form) return
    setScenarioType(type)
    setCustom(true)
    const mult = TYPE_MULTIPLIERS[type]
    setForm({
      ...form,
      breachWidth: round3((base?.breach_width ?? 1) * mult),
      reservoirLevel: type === 'extreme' ? 100 : form.reservoirLevel,
    })
  }

  const submit = async () => {
    if (!scenario || !form) return
    setSubmitting(true)
    setError(null)
    setRunningJob(null)

    const diff = (value: number, baseValue: number | undefined) =>
      baseValue != null && Math.abs(value - baseValue) < 1e-9 ? null : value

    const body: CreateJobRequest = {
      scenario: scenario.name,
      parameters: {
        reservoir_level: form.reservoirLevel,
        breach_width: diff(form.breachWidth, base?.breach_width),
        breach_time: diff(form.breachTime, base?.breach_time),
        simulation_time: diff(form.simulationTime, base?.simulation_time),
        particle_spacing: diff(form.particleSpacing, base?.particle_spacing),
        scenario_type: scenarioType,
      },
    }

    try {
      const res = await createJob(body)
      onStarted(res.job_id)
    } catch (err) {
      const response = (
        err as {
          response?: { status?: number; data?: { detail?: string } }
        }
      ).response
      const detail = response?.data?.detail ?? 'Could not start the job.'
      if (response?.status === 409) {
        const match = /job_[0-9a-f]+/.exec(detail)
        setRunningJob(match ? match[0] : null)
      }
      setError(detail)
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="page">
        <div className="loading-block">
          <span className="spinner" /> Loading scenarios…
        </div>
      </div>
    )
  }

  if (readyScenarios.length === 0) {
    return (
      <div className="page">
        <div className="empty-state">
          No ready scenarios on the backend. Start the API server and check
          the scenario definitions under <code>scenarios/</code>.
        </div>
      </div>
    )
  }

  const estimatedFrames =
    form != null ? Math.floor(form.simulationTime / timeOut) + 1 : 0

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">New simulation</h1>
          <p className="page__subtitle">
            Configure a scenario and launch it through the Scenario Runner.
            The backend executes GenCase → DualSPHysics → PartVTK →
            post-processing; progress streams back here.
          </p>
        </div>
        <div className="page__actions">
          <span className={`chip${validated ? ' chip--success' : ' chip--warn'}`}>
            <strong>
              {validated ? 'Validated configuration' : 'Custom overrides'}
            </strong>
          </span>
        </div>
      </div>

      {systemStatus && !systemStatus.dualsphysics.ready && (
        <div className="error-banner">
          <span>
            ⚠ DualSPHysics is{' '}
            {systemStatus.dualsphysics.configured
              ? `configured but not ready (${systemStatus.dualsphysics.missing.join(', ') || systemStatus.dualsphysics.error || 'binaries missing'})`
              : 'not configured'}{' '}
            — simulations will fail until it is set up.
          </span>
          {onOpenSettings && (
            <button
              type="button"
              className="btn btn--sm"
              onClick={onOpenSettings}
            >
              Open Settings
            </button>
          )}
        </div>
      )}

      {error && (
        <div className="error-banner">
          <span>{error}</span>
          {runningJob && (
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => onWatchRunning(runningJob)}
            >
              Watch running job
            </button>
          )}
        </div>
      )}

      <div className="config-layout">
        <div className="panel">
          <div className="panel__title">Scenario</div>

          <div className="form-group">
            <label className="form-label" htmlFor="scenario-select">
              Base scenario
            </label>
            <select
              id="scenario-select"
              className="form-select"
              value={scenarioName}
              onChange={(e) => {
                const next = readyScenarios.find(
                  (s) => s.name === e.target.value,
                )
                if (next) {
                  setScenarioName(next.name)
                  resetTo(next)
                }
              }}
            >
              {readyScenarios.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.display_name}
                </option>
              ))}
            </select>
            {scenario?.description && (
              <p className="form-hint">{scenario.description}</p>
            )}
          </div>

          <div className="scenario-section">
            <div className="scenario-section__title">Preset</div>
            <div className="segment">
              <button
                type="button"
                className={`segment__btn${!custom ? ' segment__btn--active' : ''}`}
                onClick={() => scenario && resetTo(scenario)}
              >
                Validated preset
              </button>
              <button
                type="button"
                className={`segment__btn${custom ? ' segment__btn--active' : ''}`}
                onClick={() => setCustom(true)}
              >
                Custom
              </button>
            </div>
            {!custom && (
              <p className="param-note">
                Reservoir at 100% of the validated fill depth, validated breach
                width / timing, no overrides written to the case.
              </p>
            )}
          </div>

          <div className="scenario-section">
            <div className="scenario-section__title">
              Scenario type (breach width preset)
            </div>
            <div className="segment">
              {TYPE_LABELS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={`segment__btn${scenarioType === t.id && custom ? ' segment__btn--active' : ''}`}
                  onClick={() => applyType(t.id)}
                  disabled={!custom}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <p className="param-note">
              Sets the breach width to {TYPE_MULTIPLIERS[scenarioType]}× the
              scenario base ({base?.breach_width ?? '—'}) ={' '}
              <strong>
                {form ? round3((base?.breach_width ?? 1) * TYPE_MULTIPLIERS[scenarioType]) : '—'}
              </strong>
              . Switch to Custom to edit values directly.
            </p>
          </div>

          <div className="scenario-section">
            <div className="scenario-section__title">Parameters</div>
            <div className="form-row">
              <div className="form-group">
                <label className="form-label" htmlFor="reservoir">
                  Reservoir level (% of fill depth)
                </label>
                <input
                  id="reservoir"
                  className="form-input"
                  type="number"
                  min={1}
                  max={100}
                  step={1}
                  disabled={!custom || !form}
                  value={form?.reservoirLevel ?? 100}
                  onChange={(e) =>
                    form &&
                    setForm({
                      ...form,
                      reservoirLevel: clamp(Number(e.target.value), 1, 100),
                    })
                  }
                />
                <p className="form-hint">
                  100% = {base?.reservoir_water_depth ?? '—'} m (validated fill
                  depth)
                </p>
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="breach-width">
                  Breach width (m)
                </label>
                <input
                  id="breach-width"
                  className="form-input"
                  type="number"
                  min={0.05}
                  max={50}
                  step={0.1}
                  disabled={!custom || !form}
                  value={form?.breachWidth ?? ''}
                  onChange={(e) =>
                    form &&
                    setForm({ ...form, breachWidth: Number(e.target.value) })
                  }
                />
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="breach-time">
                  Breach time (s)
                </label>
                <input
                  id="breach-time"
                  className="form-input"
                  type="number"
                  min={0}
                  max={60}
                  step={0.05}
                  disabled={!custom || !form}
                  value={form?.breachTime ?? ''}
                  onChange={(e) =>
                    form &&
                    setForm({ ...form, breachTime: Number(e.target.value) })
                  }
                />
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="sim-time">
                  Simulation duration (s)
                </label>
                <input
                  id="sim-time"
                  className="form-input"
                  type="number"
                  min={0.5}
                  max={600}
                  step={0.5}
                  disabled={!custom || !form}
                  value={form?.simulationTime ?? ''}
                  onChange={(e) =>
                    form &&
                    setForm({
                      ...form,
                      simulationTime: Number(e.target.value),
                    })
                  }
                />
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="spacing">
                  Particle spacing (m)
                </label>
                <input
                  id="spacing"
                  className="form-input"
                  type="number"
                  min={0.02}
                  max={0.5}
                  step={0.01}
                  disabled={!custom || !form}
                  value={form?.particleSpacing ?? ''}
                  onChange={(e) =>
                    form &&
                    setForm({
                      ...form,
                      particleSpacing: Number(e.target.value),
                    })
                  }
                />
                <p className="form-hint">
                  Smaller spacing = more particles = slower runs
                </p>
              </div>
            </div>

            {custom && form && (
              <p className="param-note param-note--warn">
                Custom values are validated on the backend against the
                supported override ranges; invalid values are rejected before
                anything runs.
              </p>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel__title">Run summary</div>
          <ul className="summary-list">
            <li>
              <span className="k">Scenario</span>
              <span className="v">{scenario?.display_name ?? '—'}</span>
            </li>
            <li>
              <span className="k">Dam</span>
              <span className="v">{scenario?.dam_id ?? '—'}</span>
            </li>
            <li>
              <span className="k">Reservoir</span>
              <span className="v">{form?.reservoirLevel ?? '—'}%</span>
            </li>
            <li>
              <span className="k">Breach width</span>
              <span className="v">{form?.breachWidth ?? '—'} m</span>
            </li>
            <li>
              <span className="k">Breach time</span>
              <span className="v">{form?.breachTime ?? '—'} s</span>
            </li>
            <li>
              <span className="k">Duration</span>
              <span className="v">{form?.simulationTime ?? '—'} s</span>
            </li>
            <li>
              <span className="k">Particle spacing</span>
              <span className="v">{form?.particleSpacing ?? '—'} m</span>
            </li>
            <li>
              <span className="k">Output frames</span>
              <span className="v">≈ {estimatedFrames}</span>
            </li>
            <li>
              <span className="k">Output interval</span>
              <span className="v">{timeOut} s</span>
            </li>
          </ul>

          <button
            type="button"
            className="btn btn--accent btn--lg mt-4"
            style={{ width: '100%' }}
            onClick={() => void submit()}
            disabled={submitting || !form || !scenario}
          >
            {submitting ? (
              <>
                <span className="spinner" /> Starting…
              </>
            ) : (
              '▶ Run simulation'
            )}
          </button>

          <p className="param-note mt-3">
            One simulation can run at a time. The derived scenario file is
            temporary and removed when the job finishes; the tracked base
            scenario is never modified.
          </p>
        </div>
      </div>
    </div>
  )
}

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min
  return Math.min(max, Math.max(min, value))
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000
}
