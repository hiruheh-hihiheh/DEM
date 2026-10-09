import React, { useCallback, useEffect, useState } from 'react'
import {
  fetchDualSPHysics,
  saveDualSPHysics,
  validateDualSPHysics,
} from '../services/api'
import { useSystemStatus } from '../services/useSystemStatus'
import type { DualSPHysicsStatus, SystemComponent } from '../types/system'

interface SettingsPageProps {
  onBack: () => void
}

/** One binary row of the DualSPHysics checklist. */
const BinaryRow: React.FC<{
  label: string
  binary: DualSPHysicsStatus['gencase']
}> = ({ label, binary }) => {
  const ok = Boolean(binary?.available)
  return (
    <li className={`dsp-binary${ok ? '' : ' dsp-binary--missing'}`}>
      <span className="dsp-binary__icon" aria-hidden="true">
        {ok ? '✓' : '✗'}
      </span>
      <span className="dsp-binary__name">{label}</span>
      <code className="dsp-binary__path" title={binary?.path}>
        {binary?.path ?? binary?.filename ?? 'not found'}
      </code>
    </li>
  )
}

const StatusLine: React.FC<{
  label: string
  component: SystemComponent
}> = ({ label, component }) => (
  <li className={`system-status__row${component.ready ? '' : ' system-status__row--warn'}`}>
    <span className="system-status__icon" aria-hidden="true">
      {component.ready ? '✓' : '⚠'}
    </span>
    <span className="system-status__label">{label}</span>
    <span className="system-status__detail" title={component.detail ?? undefined}>
      {component.detail ?? (component.ready ? 'ready' : 'not ready')}
    </span>
  </li>
)

/**
 * Environment / configuration page: DualSPHysics installation (validated by
 * the backend through the SAME resolver the Scenario Runner uses) plus the
 * full system-readiness report.
 */
export const SettingsPage: React.FC<SettingsPageProps> = ({ onBack }) => {
  const [dsp, setDsp] = useState<DualSPHysicsStatus | null>(null)
  const [dspLoading, setDspLoading] = useState(true)
  const [dspError, setDspError] = useState<string | null>(null)
  const [rootInput, setRootInput] = useState('')
  const [busy, setBusy] = useState<'validate' | 'save' | null>(null)
  const [notice, setNotice] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const {
    status,
    loading: statusLoading,
    error: statusError,
    refresh,
  } = useSystemStatus()

  const loadDsp = useCallback(async () => {
    setDspLoading(true)
    setDspError(null)
    try {
      const data = await fetchDualSPHysics()
      setDsp(data)
      setRootInput((prev) => (prev === '' ? (data.root ?? '') : prev))
    } catch (err) {
      setDspError(
        err instanceof Error
          ? err.message
          : 'Backend unreachable — cannot check DualSPHysics.',
      )
    } finally {
      setDspLoading(false)
    }
  }, [])

  useEffect(() => {
    void Promise.resolve().then(loadDsp)
  }, [loadDsp])

  const handleValidate = async () => {
    setBusy('validate')
    setNotice(null)
    try {
      const result = await validateDualSPHysics(rootInput)
      setDsp(result)
      setNotice(
        result.ready
          ? { kind: 'ok', text: 'Valid — all required binaries found (not saved yet).' }
          : {
              kind: 'err',
              text: result.error
                ? `Not valid — ${result.error}`
                : `Not valid — missing: ${result.missing.join(', ')}`,
            },
      )
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        .response?.data?.detail
      setNotice({ kind: 'err', text: detail ?? 'Validation failed.' })
    } finally {
      setBusy(null)
    }
  }

  const handleSave = async () => {
    setBusy('save')
    setNotice(null)
    try {
      const result = await saveDualSPHysics(rootInput.trim() || null)
      setDsp(result)
      setNotice({
        kind: 'ok',
        text: result.root
          ? `Saved to scenarios/local.json (git-ignored). Runner will use: ${result.root}`
          : 'Machine-local setting cleared.',
      })
      refresh()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        .response?.data?.detail
      setNotice({ kind: 'err', text: detail ?? 'Saving failed.' })
    } finally {
      setBusy(null)
    }
  }

  const handleClear = async () => {
    setRootInput('')
    setBusy('save')
    setNotice(null)
    try {
      const result = await saveDualSPHysics(null)
      setDsp(result)
      setNotice({ kind: 'ok', text: 'Machine-local setting cleared.' })
      refresh()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        .response?.data?.detail
      setNotice({ kind: 'err', text: detail ?? 'Clearing failed.' })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Settings</h1>
          <p className="page__subtitle">
            Environment configuration — DualSPHysics installation and system
            readiness. Nothing here is hardcoded; values are validated by the
            backend through the Scenario Runner's own resolver.
          </p>
        </div>
        <div className="page__actions">
          <button type="button" className="btn" onClick={onBack}>
            ← Dashboard
          </button>
        </div>
      </div>

      {/* ---- DualSPHysics ------------------------------------------------- */}
      <section className="panel dsp-card" aria-label="DualSPHysics configuration">
        <div className="panel__title">
          DualSPHysics installation
          {dsp && (
            <span
              className={`status-pill ${dsp.ready ? 'status-pill--completed' : 'status-pill--failed'}`}
            >
              {dsp.ready ? 'READY' : 'NOT READY'}
            </span>
          )}
        </div>

        {dspLoading && !dsp ? (
          <div className="empty-state">
            <span className="spinner" /> Checking installation…
          </div>
        ) : dspError ? (
          <div className="error-banner">{dspError}</div>
        ) : dsp ? (
          <>
            <dl className="dsp-meta">
              <div>
                <dt>Path</dt>
                <dd>
                  <code>{dsp.root ?? '— not configured —'}</code>
                </dd>
              </div>
              <div>
                <dt>Resolved from</dt>
                <dd>{dsp.source ?? 'no source (not configured)'}</dd>
              </div>
            </dl>

            {dsp.env_override && (
              <p className="param-note">
                ⚠ The <code>DUALSPHYSICS_ROOT</code> environment variable is set
                and takes precedence over everything below:{' '}
                <code>{dsp.env_override}</code>
              </p>
            )}

            <ul className="dsp-binaries">
              <BinaryRow label="GenCase" binary={dsp.gencase} />
              <BinaryRow label="DualSPHysics solver" binary={dsp.solver} />
              <BinaryRow label="PartVTK" binary={dsp.partvtk} />
            </ul>

            {dsp.error && <div className="error-banner">{dsp.error}</div>}
            {!dsp.ready && !dsp.error && dsp.missing.length > 0 && (
              <div className="error-banner">
                Missing binaries: {dsp.missing.join(', ')}
              </div>
            )}

            <div className="dsp-form">
              <label className="dsp-form__label" htmlFor="dsp-root">
                Installation folder
              </label>
              <input
                id="dsp-root"
                className="dsp-form__input mono"
                type="text"
                spellCheck={false}
                placeholder="E:\path\to\DualSPHysics_v5.4"
                value={rootInput}
                onChange={(e) => setRootInput(e.target.value)}
              />
              <div className="dsp-form__actions">
                <button
                  type="button"
                  className="btn"
                  disabled={busy !== null || rootInput.trim() === ''}
                  onClick={() => void handleValidate()}
                >
                  {busy === 'validate' ? 'Validating…' : 'Validate'}
                </button>
                <button
                  type="button"
                  className="btn btn--accent"
                  disabled={busy !== null}
                  onClick={() => void handleSave()}
                >
                  {busy === 'save' ? 'Saving…' : 'Save local configuration'}
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={busy !== null}
                  onClick={() => void handleClear()}
                >
                  Clear
                </button>
              </div>
              {notice && (
                <p
                  className={`param-note ${notice.kind === 'err' ? 'dsp-form__error' : 'dsp-form__ok'}`}
                  role="status"
                >
                  {notice.text}
                </p>
              )}
            </div>

            <div className="param-note dsp-help">
              <p>
                The saved value is written to{' '}
                <code>scenarios/local.json</code> — a machine-local,
                <strong> git-ignored</strong> file. Committed scenario files
                never contain machine paths, so nothing breaks on other
                machines.
              </p>
              <p>
                Resolution order used by the Scenario Runner (and by this
                page):<br />
                1. <code>--dualsphysics-root</code> CLI flag →<br />
                2. <code>DUALSPHYSICS_ROOT</code> environment variable →<br />
                3. <code>dualsphysics_root</code> in the scenario file →<br />
                4. <code>scenarios/local.json</code> →<br />
                5. clear error message.
              </p>
              <p>
                Note: folder pickers are unreliable in embedded browsers — paste
                the path into the field above instead.
              </p>
            </div>
          </>
        ) : null}
      </section>

      {/* ---- System status ------------------------------------------------ */}
      <section className="panel system-status" aria-label="System status">
        <div className="panel__title">
          System status
          <button
            type="button"
            className="btn btn--sm system-status__open"
            onClick={() => {
              refresh()
              void loadDsp()
            }}
            disabled={statusLoading}
          >
            {statusLoading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
        {statusError ? (
          <div className="error-banner">
            {statusError} — start the backend with{' '}
            <code>uvicorn app.main:app --port 8000</code>.
          </div>
        ) : status ? (
          <ul className="system-status__list">
            <StatusLine label="Backend" component={status.backend} />
            <StatusLine label="Dam data" component={status.dam_data} />
            <StatusLine label="DEM pipeline" component={status.dem_pipeline} />
            <StatusLine label="Scenario Runner" component={status.scenario_runner} />
            <li
              className={`system-status__row${status.dualsphysics.ready ? '' : ' system-status__row--warn'}`}
            >
              <span className="system-status__icon" aria-hidden="true">
                {status.dualsphysics.ready ? '✓' : '⚠'}
              </span>
              <span className="system-status__label">DualSPHysics</span>
              <span className="system-status__detail">
                {status.dualsphysics.ready
                  ? `READY · ${status.dualsphysics.root}`
                  : status.dualsphysics.configured
                    ? `NOT READY · ${status.dualsphysics.missing.join(', ') || status.dualsphysics.error || 'see above'}`
                    : 'NOT CONFIGURED'}
              </span>
            </li>
          </ul>
        ) : (
          <div className="empty-state">
            <span className="spinner" /> Loading status…
          </div>
        )}
      </section>
    </div>
  )
}
