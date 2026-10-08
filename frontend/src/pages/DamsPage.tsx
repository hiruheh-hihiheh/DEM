import React, { useMemo, useState } from 'react'
import { DamDigitalTwin } from '../components/dam/DamDigitalTwin'
import { buildDamDigitalTwin } from '../utils/damDigitalTwin'
import type { DamFeature, DamGeoJSON } from '../types/dam'
import type { ScenarioInfo } from '../types/simulation'

interface DamsPageProps {
  damsData: DamGeoJSON | null
  loading: boolean
  loadError: string | null
  selectedDamId: string | null
  onSelect: (damId: string | null) => void
  scenarios: ScenarioInfo[]
  onConfigure: (scenarioName: string) => void
}

const ROW_LIMIT = 200

const damIdOf = (f: DamFeature): string => f.properties.pic ?? f.id

export const DamsPage: React.FC<DamsPageProps> = ({
  damsData,
  loading,
  loadError,
  selectedDamId,
  onSelect,
  scenarios,
  onConfigure,
}) => {
  const [query, setQuery] = useState('')
  const [river, setRiver] = useState('')
  const [state, setState] = useState('')

  const rivers = useMemo(() => {
    if (!damsData) return []
    const set = new Set<string>()
    for (const f of damsData.features) {
      if (f.properties.river) set.add(f.properties.river)
    }
    return [...set].sort()
  }, [damsData])

  const states = useMemo(() => {
    if (!damsData) return []
    const set = new Set<string>()
    for (const f of damsData.features) {
      if (f.properties.state) set.add(f.properties.state)
    }
    return [...set].sort()
  }, [damsData])

  const filtered = useMemo(() => {
    if (!damsData) return []
    const q = query.trim().toLowerCase()
    return damsData.features.filter((f) => {
      if (river && f.properties.river !== river) return false
      if (state && f.properties.state !== state) return false
      if (!q) return true
      const p = f.properties
      return [p.name, p.river, p.district, p.state, p.pic]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q))
    })
  }, [damsData, query, river, state])

  const selected = useMemo(
    () =>
      selectedDamId
        ? (damsData?.features.find((f) => damIdOf(f) === selectedDamId) ??
          null)
        : null,
    [damsData, selectedDamId],
  )

  const scenarioForDam = useMemo(() => {
    if (!selectedDamId) return null
    return scenarios.find((s) => s.dam_id === selectedDamId && s.ready) ?? null
  }, [scenarios, selectedDamId])

  if (loading) {
    return (
      <div className="page">
        <div className="loading-block">
          <span className="spinner" /> Loading dam inventory…
        </div>
      </div>
    )
  }

  if (loadError || !damsData) {
    return (
      <div className="page">
        <div className="error-banner">
          {loadError ?? 'Dam data could not be loaded from the backend.'}
        </div>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Dams</h1>
          <p className="page__subtitle">
            {damsData.features.length.toLocaleString()} structures in the
            inventory — search, select one, and open its digital twin or a
            simulation scenario.
          </p>
        </div>
      </div>

      <div className="dams-layout">
        <div className="panel">
          <div className="dam-filters">
            <input
              className="form-input"
              type="search"
              placeholder="Search name, river, district, ID…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search dams"
            />
            <select
              className="form-select"
              value={river}
              onChange={(e) => setRiver(e.target.value)}
              aria-label="Filter by river"
            >
              <option value="">All rivers</option>
              {rivers.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <select
              className="form-select"
              value={state}
              onChange={(e) => setState(e.target.value)}
              aria-label="Filter by state"
            >
              <option value="">All states</option>
              {states.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>

          <p className="param-note">
            Showing {Math.min(filtered.length, ROW_LIMIT)} of{' '}
            {filtered.length.toLocaleString()} matches
          </p>

          <ul className="dam-list">
            {filtered.slice(0, ROW_LIMIT).map((f) => {
              const id = damIdOf(f)
              const hasScenario = scenarios.some(
                (s) => s.dam_id === id && s.ready,
              )
              return (
                <li
                  key={id}
                  className={`dam-list__item${id === selectedDamId ? ' dam-list__item--selected' : ''}`}
                  onClick={() => onSelect(id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') onSelect(id)
                  }}
                  tabIndex={0}
                  role="button"
                >
                  <div>
                    <div className="dam-list__name">
                      {f.properties.name ?? id}
                    </div>
                    <div className="dam-list__meta">
                      {[f.properties.river, f.properties.state]
                        .filter(Boolean)
                        .join(' · ') || id}
                    </div>
                  </div>
                  {hasScenario && (
                    <span className="dam-list__badge">scenario</span>
                  )}
                </li>
              )
            })}
          </ul>

          {filtered.length > ROW_LIMIT && (
            <p className="param-note">
              Narrow the search to see more results.
            </p>
          )}
        </div>

        <div>
          {selected ? (
            <div className="panel">
              <div className="dam-detail__actions">
                {scenarioForDam ? (
                  <button
                    type="button"
                    className="btn btn--accent"
                    onClick={() => onConfigure(scenarioForDam.name)}
                  >
                    Configure simulation →
                  </button>
                ) : (
                  <span className="chip chip--warn">
                    <strong>No simulation scenario</strong> configured for this
                    dam yet
                  </span>
                )}
                <button
                  type="button"
                  className="btn"
                  onClick={() => onSelect(null)}
                >
                  Clear selection
                </button>
              </div>
              <DamDigitalTwin data={buildDamDigitalTwin(selected)} />
            </div>
          ) : (
            <div className="panel">
              <div className="empty-state">
                Select a dam from the list to view its digital twin.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
