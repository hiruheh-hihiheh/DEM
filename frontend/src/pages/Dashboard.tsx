import React, {
  useEffect,
  useMemo,
  useState,
} from 'react'

import { Header } from '../components/Header'
import {
  ScenarioPanel,
  type ScenarioState,
} from '../components/SenarioPanel'
import { FloodMap } from '../map/FloodMap'
import { MapToolbar } from '../components/MapToolBar'
import { MetricCard } from '../components/MetricCard'
import { StatusBadge } from '../components/StatusBadge'
import { fetchDams } from '../services/api'

import type { DamGeoJSON } from '../types/dam'

/* ---- Inline SVG icons for metric cards ---- */
const IconFloodArea = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 12C2 12 5 8 8 8C11 8 13 12 16 12C19 12 22 8 22 8" />
    <path d="M2 17C2 17 5 13 8 13C11 13 13 17 16 17C19 17 22 13 22 13" />
  </svg>
)

const IconDepth = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 2V22" />
    <path d="M8 6L12 2L16 6" />
    <path d="M8 18L12 22L16 18" />
    <line x1="4" y1="12" x2="20" y2="12" />
  </svg>
)

const IconVelocity = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
  </svg>
)

const IconPopulation = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 00-3-3.87" />
    <path d="M16 3.13a4 4 0 010 7.75" />
  </svg>
)

export const Dashboard: React.FC = () => {
  const [scenario, setScenario] =
    useState<ScenarioState>({
      river: '',
      dam: '',
      scenario: 'normal',
      reservoirLevel: 75,
    })

  const [damsData, setDamsData] =
    useState<DamGeoJSON | null>(null)

  const [isLoadingDams, setIsLoadingDams] =
    useState(true)

  const [damLoadError, setDamLoadError] =
    useState<string | null>(null)

  const [isSimulating, setIsSimulating] =
    useState(false)

  const [simulationComplete, setSimulationComplete] =
    useState(false)

  useEffect(() => {
    const loadDams = async () => {
      try {
        setIsLoadingDams(true)
        setDamLoadError(null)

        const data = await fetchDams()

        setDamsData(data)
      } catch (error) {
        console.error(
          'Failed to load dams:',
          error,
        )

        setDamLoadError(
          'Failed to connect to backend API.',
        )
      } finally {
        setIsLoadingDams(false)
      }
    }

    void loadDams()
  }, [])

  const riversList = useMemo(() => {
    if (!damsData) {
      return []
    }

    const rivers = new Set(
      damsData.features
        .map(
          (feature) =>
            feature.properties.river,
        )
        .filter(
          (
            river,
          ): river is string =>
            Boolean(river),
        ),
    )

    return Array.from(rivers).sort()
  }, [damsData])

  const filteredDams = useMemo(() => {
    if (!damsData) {
      return []
    }

    return damsData.features
      .filter(
        (feature) =>
          !scenario.river ||
          feature.properties.river ===
            scenario.river,
      )
      .filter(
        (feature) =>
          Boolean(feature.properties.name),
      )
      .map((feature) => ({
        id:
          feature.properties.pic ??
          feature.id,
        name:
          feature.properties.name ??
          'Unnamed Dam',
        river:
          feature.properties.river,
      }))
      .sort((a, b) =>
        a.name.localeCompare(b.name),
      )
  }, [damsData, scenario.river])

  const handleScenarioChange = (
    updates: Partial<ScenarioState>,
  ) => {
    setScenario((previous) => ({
      ...previous,
      ...updates,
    }))

    setSimulationComplete(false)
  }

  const handleDamSelect = (
    damId: string,
  ) => {
    setScenario((previous) => ({
      ...previous,
      dam: damId,
    }))

    setSimulationComplete(false)
  }

  const handleRunSimulation = () => {
    if (!scenario.dam) {
      return
    }

    setIsSimulating(true)
    setSimulationComplete(false)

    setTimeout(() => {
      setIsSimulating(false)
      setSimulationComplete(true)
    }, 2000)
  }

  const systemStatus =
    isSimulating
      ? 'simulation'
      : simulationComplete
        ? 'live'
        : 'idle'

  const statusText =
    isSimulating
      ? 'Running Simulation'
      : simulationComplete
        ? 'Simulation Complete'
        : 'System Ready'

  return (
    <div className="app-shell">
      <Header
        status={
          <StatusBadge
            status={systemStatus}
            text={statusText}
          />
        }
      />

      <main className="app-main">
        <aside className="app-sidebar">
          <ScenarioPanel
            state={scenario}
            onChange={handleScenarioChange}
            onRunSimulation={
              handleRunSimulation
            }
            isSimulating={isSimulating}
            riversList={riversList}
            damsList={filteredDams}
            isLoadingDams={
              isLoadingDams
            }
            damLoadError={
              damLoadError
            }
          />
        </aside>

        <section className="app-content">
          <MapToolbar />

          <FloodMap
            damsData={damsData}
            selectedDam={scenario.dam}
            onDamSelect={
              handleDamSelect
            }
          />
        </section>
      </main>

      <footer className="app-metrics">
        <MetricCard
          icon={IconFloodArea}
          label="Flood Area"
          value="--"
          unit="km²"
          description="Awaiting simulation"
        />

        <MetricCard
          icon={IconDepth}
          label="Maximum Depth"
          value="--"
          unit="m"
          description="Awaiting simulation"
        />

        <MetricCard
          icon={IconVelocity}
          label="Maximum Velocity"
          value="--"
          unit="m/s"
          description="Awaiting simulation"
        />

        <MetricCard
          icon={IconPopulation}
          label="Population Exposed"
          value="--"
          unit="people"
          description="Awaiting simulation"
        />
      </footer>
    </div>
  )
}