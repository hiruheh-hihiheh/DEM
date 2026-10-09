import React, { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { FeatureCollection, Polygon } from 'geojson'
import { fetchDemHillshade, resultFileUrl } from '../../services/api'
import { basemapStyle, type BasemapMode } from '../../map/basemaps'
import type { DemHillshade } from '../../types/system'
import type { ResultManifest } from '../../types/simulation'

interface ViewerMapProps {
  jobId: string
  manifest: ResultManifest
  /** Currently displayed frame (flood extent follows playback). */
  frame: number
  /** Scenario name — used to fetch the DEM hillshade overlay. */
  scenario?: string | null
  /** Basemap style (2D map vs satellite imagery). */
  basemap?: BasemapMode
}

interface FloodFeatureProps {
  frame: number
  time: number
  area_units?: number
}

type FloodCollection = FeatureCollection<Polygon, FloodFeatureProps>

const DEM_LAYER_ID = 'dem-hillshade'
const DEM_SOURCE_ID = 'dem-image'

/**
 * MapLibre view of one result: geoprocessed DEM hillshade (grayscale +
 * lighting, on by default), dam marker, per-frame flood extent hull and the
 * final affected-region outline — all georeferenced via the manifest.
 * Supports the Standard and Satellite basemaps and stays in sync with its
 * container size.
 */
export const ViewerMap: React.FC<ViewerMapProps> = ({
  jobId,
  manifest,
  frame,
  scenario,
  basemap = 'standard',
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const styleLoadedRef = useRef(false)
  const [flood, setFlood] = useState<FloodCollection | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dem, setDem] = useState<DemHillshade | null>(null)
  const [demOn, setDemOn] = useState(true)
  const geo = manifest.geo

  // ---- load flood polygons once ------------------------------------------
  useEffect(() => {
    let cancelled = false
    if (!manifest.files.flood) return
    ;(async () => {
      try {
        const res = await fetch(resultFileUrl(jobId, 'flood.geojson'))
        if (!res.ok) throw new Error(String(res.status))
        const data = (await res.json()) as FloodCollection
        if (!cancelled) setFlood(data)
      } catch {
        if (!cancelled) setError('Flood extent is unavailable for this result.')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [jobId, manifest.files.flood])

  // ---- DEM hillshade overlay (real geoprocessed DEM, once per scenario) ---
  useEffect(() => {
    let cancelled = false

    void (async () => {
      if (!scenario || scenario === 'import') {
        setDem(null)
        return
      }
      setDem(null)
      try {
        const payload = await fetchDemHillshade(scenario)
        if (!cancelled) setDem(payload)
      } catch {
        // Backend older than this feature / offline — degrade silently.
        if (!cancelled) setDem({ available: false, error: 'unavailable' })
      }
    })()

    return () => {
      cancelled = true
    }
  }, [scenario])

  // ---- map init ------------------------------------------------------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    styleLoadedRef.current = false

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: basemapStyle(basemap),
      center: geo?.dam_lon && geo?.dam_lat ? [geo.dam_lon, geo.dam_lat] : [78.9629, 22.5937],
      zoom: geo?.available ? 14 : 4,
      attributionControl: { compact: true },
    })
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    mapRef.current = map

    map.on('load', () => {
      styleLoadedRef.current = true
      setError(null)

      // Dam marker
      if (geo?.dam_lon != null && geo.dam_lat != null) {
        map.addSource('dam', {
          type: 'geojson',
          data: {
            type: 'FeatureCollection',
            features: [
              {
                type: 'Feature',
                properties: { label: manifest.source.scenario ?? 'dam' },
                geometry: { type: 'Point', coordinates: [geo.dam_lon, geo.dam_lat] },
              },
            ],
          },
        })
        map.addLayer({
          id: 'dam-halo',
          type: 'circle',
          source: 'dam',
          paint: {
            'circle-radius': 14,
            'circle-color': 'rgba(245, 158, 11, 0.25)',
          },
        })
        map.addLayer({
          id: 'dam-point',
          type: 'circle',
          source: 'dam',
          paint: {
            'circle-radius': 6,
            'circle-color': '#f59e0b',
            'circle-stroke-width': 2,
            'circle-stroke-color': '#0b0f19',
          },
        })
        map.addLayer({
          id: 'dam-label',
          type: 'symbol',
          source: 'dam',
          layout: {
            'text-field': ['get', 'label'],
            'text-offset': [0, 1.6],
            'text-size': 11,
          },
          paint: { 'text-color': '#fbbf24', 'text-halo-color': '#0b0f19', 'text-halo-width': 1.5 },
        })
      }

      // Current-frame flood extent
      map.addSource('flood', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({
        id: 'flood-fill',
        type: 'fill',
        source: 'flood',
        paint: {
          'fill-color': '#38bdf8',
          'fill-opacity': 0.35,
        },
      })
      map.addLayer({
        id: 'flood-line',
        type: 'line',
        source: 'flood',
        paint: {
          'line-color': '#7dd3fc',
          'line-width': 1.5,
        },
      })

      // Final extent = affected region outline (always visible)
      map.addSource('affected', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({
        id: 'affected-line',
        type: 'line',
        source: 'affected',
        paint: {
          'line-color': '#f87171',
          'line-width': 1.5,
          'line-dasharray': [2, 2],
        },
      })

      // Fit to georeferenced scenario bounds
      if (geo?.bounds) {
        const [minLon, minLat, maxLon, maxLat] = geo.bounds
        map.fitBounds(
          [
            [minLon, minLat],
            [maxLon, maxLat],
          ],
          { padding: 60, duration: 0 },
        )
      }
    })

    // Surface style-load failures instead of hanging; ignore tile noise
    // after a successful load (e.g. a flaky imagery provider).
    map.on('error', (ev: maplibregl.ErrorEvent) => {
      if (styleLoadedRef.current) return
      const message = String(ev?.error?.message ?? '').trim()
      setError(
        message
          ? `Basemap failed to load — ${message}`
          : 'Basemap failed to load. Check the network connection.',
      )
    })

    // Keep the canvas in sync with layout/window resizes.
    const resize = () => map.resize()
    const observer =
      typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(resize)
        : null
    if (observer && containerRef.current) {
      observer.observe(containerRef.current)
    }
    window.addEventListener('resize', resize)

    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', resize)
      map.remove()
      mapRef.current = null
      styleLoadedRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basemap])

  // ---- attach the DEM image below the flood/dam layers ---------------------
  useEffect(() => {
    const map = mapRef.current
    if (!map || !dem?.available || !dem.image || !dem.coordinates) return

    // Capture primitives so the narrowing survives inside the closure.
    const demImage = dem.image
    const demCoordinates = dem.coordinates

    const addDem = () => {
      if (map.getSource(DEM_SOURCE_ID)) return
      try {
        map.addSource(DEM_SOURCE_ID, {
          type: 'image',
          url: demImage,
          coordinates: demCoordinates,
        })
        const beforeId = map.getLayer('dam-halo')
          ? 'dam-halo'
          : map.getLayer('flood-fill')
            ? 'flood-fill'
            : undefined
        map.addLayer(
          {
            id: DEM_LAYER_ID,
            type: 'raster',
            source: DEM_SOURCE_ID,
            paint: {
              'raster-opacity': 0.72,
              'raster-fade-duration': 120,
            },
          },
          beforeId,
        )
        if (!demOn) map.setLayoutProperty(DEM_LAYER_ID, 'visibility', 'none')
      } catch (err) {
        console.error('Failed to add DEM overlay:', err)
      }
    }

    if (map.isStyleLoaded()) addDem()
    else map.once('load', addDem)
  }, [dem, demOn, basemap])

  // DEM visibility toggle (the layer is shown by default).
  useEffect(() => {
    const map = mapRef.current
    if (!map || !dem?.available) return
    const apply = () => {
      if (!map.getLayer(DEM_LAYER_ID)) return
      map.setLayoutProperty(
        DEM_LAYER_ID,
        'visibility',
        demOn ? 'visible' : 'none',
      )
    }
    if (map.isStyleLoaded()) apply()
    else map.once('load', apply)
  }, [demOn, dem, basemap])

  // ---- frame -> sources ------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current
    if (!map || !flood) return

    const apply = () => {
      const feature = flood.features.find((f) => f.properties?.frame === frame)
      const floodSource = map.getSource('flood') as maplibregl.GeoJSONSource | undefined
      floodSource?.setData(
        feature
          ? { type: 'FeatureCollection', features: [feature] }
          : { type: 'FeatureCollection', features: [] },
      )

      const last = flood.features[flood.features.length - 1]
      const affectedSource = map.getSource('affected') as maplibregl.GeoJSONSource | undefined
      if (affectedSource && last && last.properties?.frame !== feature?.properties?.frame) {
        affectedSource.setData({ type: 'FeatureCollection', features: [last] })
      } else if (affectedSource) {
        affectedSource.setData({ type: 'FeatureCollection', features: [] })
      }
    }

    if (map.isStyleLoaded()) apply()
    else map.once('idle', apply)
  }, [flood, frame, basemap])

  const demReady = Boolean(dem?.available && dem.image)

  return (
    <div className="viewer-map">
      <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />

      {demReady && (
        <div className="viewer-map__dem-toggle">
          <button
            type="button"
            className={`viewer-map__chip${demOn ? ' viewer-map__chip--on' : ''}`}
            aria-pressed={demOn}
            onClick={() => setDemOn((v) => !v)}
            title="Toggle the geoprocessed DEM overlay (grayscale hillshade)"
          >
            DEM
          </button>
          {demOn && dem?.elevation && (
            <div className="viewer-map__dem-legend" aria-label="Elevation legend">
              <span className="viewer-map__dem-title">
                Elevation · {dem.elevation.min}–{dem.elevation.max} {dem.elevation.unit}
              </span>
              <span className="viewer-map__dem-ramp" aria-hidden="true" />
            </div>
          )}
        </div>
      )}

      {!geo?.available && (
        <div className="viewer-map__overlay">
          <span className="hud-card">
            Not georegistered — showing placeholder position.
          </span>
        </div>
      )}
      {error && (
        <div className="viewer-map__overlay viewer-map__overlay--top">
          <span className="hud-card">{error}</span>
        </div>
      )}
    </div>
  )
}
