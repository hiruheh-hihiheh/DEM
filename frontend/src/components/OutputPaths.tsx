import React, { useState } from 'react'

/**
 * Monospace OUTPUTS panel: every real output location of a run, as reported
 * by the backend (never hardcoded or invented client-side), each with a
 * [Copy] button. Paths are repo-relative with forward slashes when inside
 * the repository, absolute otherwise.
 */
interface OutputPathsProps {
  paths?: Record<string, string>
  /** Panel heading (default "OUTPUTS"). */
  title?: string
  /** Extra note shown under the heading. */
  note?: string
}

/** Human labels for the backend's path keys (keys not listed pass through). */
const PATH_LABELS: Record<string, string> = {
  scenario_dir: 'Scenario directory',
  processed_terrain: 'Processed terrain',
  sph_terrain: 'SPH terrain',
  manifest: 'Terrain manifest',
  runs_root: 'Runs directory',
  dem_input: 'Input DEM',
  run_dir: 'Simulation run',
  run_log: 'Run log',
  metadata: 'Metadata',
  job_log: 'Job log',
  simulation_output: 'Simulation output',
  case_xml: 'Generated case (XML)',
  result_package: 'Result package',
}

const labelFor = (key: string): string => PATH_LABELS[key] ?? key

/** Copy helper: clipboard API with a legacy fallback (works headless). */
const copyText = async (text: string): Promise<boolean> => {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}

export const OutputPaths: React.FC<OutputPathsProps> = ({
  paths,
  title = 'OUTPUTS',
  note,
}) => {
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  const entries = Object.entries(paths ?? {})
  if (entries.length === 0) return null

  const handleCopy = (key: string, value: string) => {
    void copyText(value).then((ok) => {
      if (ok) {
        setCopiedKey(key)
        window.setTimeout(() => setCopiedKey((k) => (k === key ? null : k)), 1500)
      }
    })
  }

  return (
    <section className="outputs-panel" aria-label="Output locations">
      <header className="outputs-panel__header">
        <h3>{title}</h3>
        <span className="outputs-panel__count">{entries.length} path{entries.length === 1 ? '' : 's'}</span>
      </header>
      {note ? <p className="outputs-panel__note">{note}</p> : null}
      <dl className="outputs-panel__list">
        {entries.map(([key, value]) => (
          <div className="outputs-panel__row" key={key}>
            <dt>{labelFor(key)}</dt>
            <dd>
              <code title={value}>{value}</code>
              <button
                type="button"
                className="outputs-panel__copy"
                onClick={() => handleCopy(key, value)}
                aria-label={`Copy ${labelFor(key)} path`}
              >
                {copiedKey === key ? 'Copied ✓' : 'Copy'}
              </button>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
