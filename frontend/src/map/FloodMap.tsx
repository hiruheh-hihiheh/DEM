import React, {
  useEffect,
  useRef,
  useState,
} from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import type { DamGeoJSON } from '../types/dam'
import {
  basemapStyle,
  MAP_INITIAL_VIEW,
  type BasemapMode,
} from './basemaps'

interface FloodMapProps {
  damsData: DamGeoJSON | null
  selectedDam?: string
  onDamSelect?: (damId: string) => void
  /** Basemap mode (default `standard`). Remount via `key` to switch style. */
  basemap?: BasemapMode
}

export const FloodMap: React.FC<FloodMapProps> = ({
  damsData,
  selectedDam,
  onDamSelect,
  basemap = 'standard',
}) => {
  const mapContainer =
    useRef<HTMLDivElement | null>(null)

  const map =
    useRef<maplibregl.Map | null>(null)

  const styleLoadedRef = useRef(false)

  const [isLoading, setIsLoading] =
    useState(true)

  const [error, setError] =
    useState<string | null>(null)

  useEffect(() => {
    if (!mapContainer.current || map.current) {
      return
    }

    setIsLoading(true)
    setError(null)

    const mapInstance = new maplibregl.Map({
      container: mapContainer.current,
      style: basemapStyle(basemap),
      center: MAP_INITIAL_VIEW.center,
      zoom: MAP_INITIAL_VIEW.zoom,
      attributionControl: false,
    })

    map.current = mapInstance

    mapInstance.addControl(
      new maplibregl.NavigationControl({
        showCompass: true,
      }),
      'top-right',
    )

    mapInstance.addControl(
      new maplibregl.AttributionControl({
        compact: true,
      }),
      'bottom-right',
    )

    mapInstance.on('load', () => {
      styleLoadedRef.current = true
      setIsLoading(false)
      setError(null)
    })

    // Surface genuine style failures (e.g. offline) instead of spinning
    // forever; per-tile noise after a successful load is intentionally
    // ignored so a flaky provider never blanks the map.
    mapInstance.on('error', (ev: maplibregl.ErrorEvent) => {
      if (styleLoadedRef.current) return
      const message = String(ev?.error?.message ?? '').trim()
      setIsLoading(false)
      setError(
        message
          ? `Basemap failed to load — ${message}`
          : 'Basemap failed to load. Check the network connection.',
      )
    })

    // The map lives inside a responsive layout (window resizes, sidebar
    // toggles, orientation changes) — keep MapLibre's canvas in sync.
    const resize = () => mapInstance.resize()
    const observer =
      typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(resize)
        : null
    if (observer && mapContainer.current) {
      observer.observe(mapContainer.current)
    }
    window.addEventListener('resize', resize)

    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', resize)
      mapInstance.remove()
      map.current = null
      styleLoadedRef.current = false
    }
  }, [basemap])

  useEffect(() => {
    const mapInstance = map.current

    if (!mapInstance || !damsData) {
      return
    }

    const addDamLayer = () => {
      if (mapInstance.getSource('dams')) {
        return
      }

      try {
        mapInstance.addSource('dams', {
          type: 'geojson',
          data: damsData,
        })

        mapInstance.addLayer({
          id: 'dams-layer',
          type: 'circle',
          source: 'dams',
          paint: {
            'circle-radius': [
              'case',
              ['==', ['get', 'isSelected'], true],
              10,
              5,
            ],
            'circle-color': [
              'case',
              ['==', ['get', 'isSelected'], true],
              '#f59e0b',
              '#3b82f6',
            ],
            'circle-stroke-width': 1.5,
            'circle-stroke-color': '#ffffff',
            'circle-opacity': 0.9,
          },
        })

        mapInstance.on(
          'mouseenter',
          'dams-layer',
          () => {
            mapInstance.getCanvas().style.cursor =
              'pointer'
          },
        )

        mapInstance.on(
          'mouseleave',
          'dams-layer',
          () => {
            mapInstance.getCanvas().style.cursor =
              ''
          },
        )

 mapInstance.on(
  'click',
  'dams-layer',
  (event) => {
    const feature =
      event.features?.[0]

    if (!feature) {
      return
    }

    const properties =
      feature.properties

    const damId =
      properties?.pic

    const popup =
      new maplibregl.Popup({
        closeButton: true,
        closeOnClick: false,
        className: 'dam-popup',
      })

    popup
      .setLngLat(event.lngLat)
      .setHTML(`
        <div class="popup-content">
          <h3>
            ${properties?.name ?? 'Unknown Dam'}
          </h3>

          <p>
            <strong>River:</strong>
            ${properties?.river ?? 'N/A'}
          </p>

          <p>
            <strong>State:</strong>
            ${properties?.state ?? 'N/A'}
          </p>

          <button
            type="button"
            class="popup-more-info"
            data-dam-id="${damId ?? ''}"
          >
            More Info
          </button>
        </div>
      `)
      .addTo(mapInstance)

    popup.once('open', () => {
      const button =
        document.querySelector(
          '.popup-more-info',
        ) as HTMLButtonElement | null

      if (!button || !damId) {
        return
      }

      button.addEventListener(
        'click',
        () => {
          onDamSelect?.(
            String(damId),
          )

          popup.remove()
        },
      )
    })
  },
)
  
      } catch (err) {
        console.error(
          'Failed to add dam layer:',
          err,
        )

        setError(
          'Failed to display dam geospatial data.',
        )
      }
    }

    if (mapInstance.isStyleLoaded()) {
      addDamLayer()
    } else {
      mapInstance.once('load', addDamLayer)
    }
  }, [damsData, onDamSelect, basemap])

  // Fly only when the *selection* changes — not on basemap remounts.
  const prevSelectedRef = useRef<string | null | undefined>(null)

  useEffect(() => {
    const mapInstance = map.current

    if (!mapInstance || !damsData) {
      return
    }

    const source = mapInstance.getSource(
      'dams',
    ) as maplibregl.GeoJSONSource | undefined

    if (!source) {
      return
    }

    const features = damsData.features.map(
      (feature) => ({
        ...feature,
        properties: {
          ...feature.properties,
          isSelected:
            feature.properties.pic ===
            selectedDam,
        },
      }),
    )

    source.setData({
      type: 'FeatureCollection',
      features,
    })

    const selectionChanged =
      prevSelectedRef.current !== selectedDam
    prevSelectedRef.current = selectedDam

    if (!selectedDam) {
      if (selectionChanged) {
        mapInstance.flyTo({
          center: MAP_INITIAL_VIEW.center,
          zoom: MAP_INITIAL_VIEW.zoom,
          duration: 1000,
        })
      }

      return
    }

    const selectedFeature =
      damsData.features.find(
        (feature) =>
          feature.properties.pic ===
          selectedDam,
      )

    if (!selectedFeature) {
      return
    }

    if (selectionChanged) {
      mapInstance.flyTo({
        center:
          selectedFeature.geometry.coordinates,
        zoom: 9,
        duration: 1200,
      })
    }
  }, [selectedDam, damsData, basemap])

  return (
    <div className="flood-map-container">
      {isLoading && (
        <div className="map-overlay-loading">
          Loading Geospatial Data...
        </div>
      )}

      {error && (
        <div className="map-overlay-error">
          {error}
        </div>
      )}

      <div
        ref={mapContainer}
        className="maplibre-map"
      />
    </div>
  )
}