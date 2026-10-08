import React, { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { FeatureCollection, Polygon } from 'geojson'
import { resultFileUrl } from '../../services/api'
import type { ResultManifest } from '../../types/simulation'

interface ViewerMapProps {
  jobId: string
  manifest: ResultManifest
  /** Currently displayed frame (flood extent follows playback). */
  frame: number
}

interface FloodFeatureProps {
  frame: number
  time: number
  area_units?: number
}

type FloodCollection = FeatureCollection<Polygon, FloodFeatureProps>

/**
 * MapLibre view of one result: dam marker, per-frame flood extent hull and
 * the final affected-region outline, georeferenced via the manifest.
 */
export const ViewerMap: React.FC<ViewerMapProps> = ({
  jobId,
  manifest,
  frame,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const [flood, setFlood] = useState<FloodCollection | null>(null)
  const [error, setError] = useState<string | null>(null)
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

  // ---- map init ------------------------------------------------------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
      center: geo?.dam_lon && geo?.dam_lat ? [geo.dam_lon, geo.dam_lat] : [78.9629, 22.5937],
      zoom: geo?.available ? 14 : 4,
      attributionControl: { compact: true },
    })
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    mapRef.current = map

    map.on('load', () => {
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

      // Fit to georeferenced bounds
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

    return () => {
      map.remove()
      mapRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
  }, [flood, frame])

  return (
    <div className="viewer-map">
      <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />
      {!geo?.available && (
        <div className="viewer-map__overlay">
          <span className="hud-card">
            Not georegistered — showing placeholder position.
          </span>
        </div>
      )}
      {error && (
        <div className="viewer-map__overlay">
          <span className="hud-card">{error}</span>
        </div>
      )}
    </div>
  )
}
