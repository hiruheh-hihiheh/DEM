import React from 'react'
import type { DamFeature } from '../types/dam'

export interface ScenarioState {
  river: string
  dam: string
  scenario: 'normal' | 'partial' | 'full' | 'extreme'
  reservoirLevel: number
}

interface ScenarioPanelProps {
  state: ScenarioState
  onChange: (state: Partial<ScenarioState>) => void
  onRunSimulation: () => void
  isSimulating: boolean
  riversList: string[]
  damsList: {
    id: string
    name: string
    river: string | null
  }[]
  isLoadingDams: boolean
  damLoadError: string | null
  selectedDamData: DamFeature | null
}

export const ScenarioPanel: React.FC<ScenarioPanelProps> = ({
  state,
  onChange,
  onRunSimulation,
  isSimulating,
  riversList,
  damsList,
  isLoadingDams,
  damLoadError,
  selectedDamData,
}) => {
  return (
    <div className="scenario-panel">
      <div className="scenario-section">
        <h2 className="scenario-section__title">
          Study Area
        </h2>

        <div className="form-group">
          <label
            htmlFor="river-select"
            className="form-label"
          >
            River Basin
          </label>

          <select
            id="river-select"
            className="form-select"
            value={state.river}
            onChange={(event) =>
              onChange({
                river: event.target.value,
                dam: '',
              })
            }
            disabled={
              isLoadingDams ||
              Boolean(damLoadError)
            }
          >
            <option value="">All Rivers</option>

            {riversList.map((river) => (
              <option key={river} value={river}>
                {river}
              </option>
            ))}
          </select>

          <span className="form-hint">
            Filter dams by river
          </span>
        </div>
      </div>

      <div className="scenario-section">
        <h2 className="scenario-section__title">
          Dam / Reservoir
        </h2>

        <div className="form-group">
          <label
            htmlFor="dam-select"
            className="form-label"
          >
            Select Dam
          </label>

          {isLoadingDams ? (
            <div className="form-select">
              Loading dam dataset...
            </div>
          ) : damLoadError ? (
            <div className="form-select">
              {damLoadError}
            </div>
          ) : (
            <select
              id="dam-select"
              className="form-select"
              value={state.dam}
              onChange={(event) =>
                onChange({
                  dam: event.target.value,
                })
              }
              disabled={isSimulating}
            >
              <option value="">
                Select a dam...
              </option>

              {damsList.map((dam) => (
                <option
                  key={dam.id}
                  value={dam.id}
                >
                  {dam.name}
                </option>
              ))}
            </select>
          )}

          <span className="form-hint">
            {damsList.length} dams available
          </span>
        </div>
      </div>

      {selectedDamData && (
        <div className="selected-dam">
          <div className="selected-dam__header">
            <span className="selected-dam__eyebrow">
              Selected Dam
            </span>

            <span className="selected-dam__status">
              Active
            </span>
          </div>

          <h3 className="selected-dam__name">
            {selectedDamData.properties.name ??
              'Unnamed Dam'}
          </h3>

          <div className="selected-dam__grid">
            <div>
              <span>River</span>
              <strong>
                {selectedDamData.properties.river ??
                  'N/A'}
              </strong>
            </div>

            <div>
              <span>State</span>
              <strong>
                {selectedDamData.properties.state ??
                  'N/A'}
              </strong>
            </div>

            <div>
              <span>Height</span>
              <strong>
                {selectedDamData.properties.height ??
                  '—'}{' '}
                m
              </strong>
            </div>

            <div>
              <span>Year</span>
              <strong>
                {selectedDamData.properties
                  .completion_year ?? '—'}
              </strong>
            </div>
          </div>
        </div>
      )}

      <div className="scenario-section">
        <h2 className="scenario-section__title">
          Failure Scenario
        </h2>

        <div className="form-group">
          <label
            htmlFor="scenario-select"
            className="form-label"
          >
            Breach Type
          </label>

          <select
            id="scenario-select"
            className="form-select"
            value={state.scenario}
            onChange={(event) =>
              onChange({
                scenario:
                  event.target
                    .value as ScenarioState['scenario'],
              })
            }
            disabled={!state.dam || isSimulating}
          >
            <option value="normal">
              Normal release
            </option>

            <option value="partial">
              Partial breach
            </option>

            <option value="full">
              Full breach
            </option>

            <option value="extreme">
              Extreme rainfall + breach
            </option>
          </select>
        </div>
      </div>

      <div className="scenario-section">
        <h2 className="scenario-section__title">
          Reservoir Parameters
        </h2>

        <div className="form-group">
          <label
            htmlFor="reservoir-level"
            className="form-label"
          >
            Initial Reservoir Level:{' '}
            {state.reservoirLevel}%
          </label>

          <input
            id="reservoir-level"
            type="range"
            className="form-slider"
            min="0"
            max="100"
            value={state.reservoirLevel}
            onChange={(event) =>
              onChange({
                reservoirLevel: Number(
                  event.target.value,
                ),
              })
            }
            disabled={
              !state.dam || isSimulating
            }
          />

          <div className="slider-labels">
            <span>Empty</span>
            <span>Full</span>
          </div>
        </div>
      </div>

      <div className="scenario-section scenario-actions">
        <button
          type="button"
          className="btn-primary"
          onClick={onRunSimulation}
          disabled={!state.dam || isSimulating}
        >
          {isSimulating ? (
            <>
              <span className="spinner" />
              Running Simulation...
            </>
          ) : (
            'Run Simulation'
          )}
        </button>

        {!state.dam && (
          <span className="form-hint scenario-hint">
            Select a dam to enable simulation
          </span>
        )}
      </div>
    </div>
  )
}