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

  const selectedDam = useMemo(() => {
    if (!damsData || !scenario.dam) {
      return null
    }

    return (
      damsData.features.find(
        (feature) =>
          (feature.properties.pic ?? feature.id) ===
          scenario.dam,
      ) ?? null
    )
  }, [damsData, scenario.dam])

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
  onRunSimulation={handleRunSimulation}
  isSimulating={isSimulating}
  riversList={riversList}
  damsList={filteredDams}
  isLoadingDams={isLoadingDams}
  damLoadError={damLoadError}
  selectedDamData={selectedDam}
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
          label="Flood Area"
          value="--"
          unit="km²"
          description="Awaiting simulation"
        />

        <MetricCard
          label="Maximum Depth"
          value="--"
          unit="m"
          description="Awaiting simulation"
        />

        <MetricCard
          label="Maximum Velocity"
          value="--"
          unit="m/s"
          description="Awaiting simulation"
        />

        <MetricCard
          label="Population Exposed"
          value="--"
          unit="people"
          description="Awaiting simulation"
        />
      </footer>
    </div>
  )
}