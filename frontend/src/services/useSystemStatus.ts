import { useCallback, useEffect, useState } from 'react'
import { fetchSystemStatus } from './api'
import type { SystemStatus } from '../types/system'

/**
 * Loads the backend's system-readiness report (backend, dam data, DEM
 * pipeline, Scenario Runner, DualSPHysics). Used by the Dashboard,
 * Configure page and Settings — one shared, honest status source.
 */
export interface UseSystemStatus {
  status: SystemStatus | null
  loading: boolean
  error: string | null
  refresh: () => void
}

export const useSystemStatus = (): UseSystemStatus => {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  const refresh = useCallback(() => setTick((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await fetchSystemStatus()
        if (!cancelled) setStatus(data)
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : 'Cannot reach the backend.',
          )
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [tick])

  return { status, loading, error, refresh }
}
