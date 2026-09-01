import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import api from '../services/api'
import type { DamFeatureCollection } from '../types/dam'

function FloodMap() {
  const mapContainer = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!mapContainer.current) {
      return
    }

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://demotiles.maplibre.org/style.json',
      center: [78.9629, 20.5937],
      zoom: 4.5,
    })

    map.addControl(
      new maplibregl.NavigationControl(),
      'top-right',
    )

    const handleLoad = async () => {
      try {
        const response =
          await api.get<DamFeatureCollection>('/api/dams')

        const damData = response.data

        map.addSource('dams', {
          type: 'geojson',
          data: damData,
        })

        map.addLayer({
          id: 'dam-points',
          type: 'circle',
          source: 'dams',
          paint: {
            'circle-radius': 4,
            'circle-opacity': 0.9,
            'circle-stroke-width': 1,
            'circle-stroke-color': '#ffffff',
          },
        })

        map.on('click', 'dam-points', (event) => {
          const feature = event.features?.[0]

          if (!feature) {
            return
          }

          const properties = feature.properties

          const name = properties?.name ?? 'Unknown Dam'
          const river = properties?.river ?? 'Unknown River'
          const state = properties?.state ?? 'Unknown State'
          const district = properties?.district ?? 'Unknown District'
          const height = properties?.height ?? '—'
          const purpose = properties?.purpose ?? '—'
          const year = properties?.completion_year ?? '—'

          new maplibregl.Popup({
            maxWidth: '320px',
          })
            .setLngLat(event.lngLat)
            .setHTML(`
              <div style="font-family: system-ui; line-height: 1.4;">
                <h3 style="margin: 0 0 10px;">
                  ${name}
                </h3>

                <div>
                  <strong>River:</strong> ${river}
                </div>

                <div>
                  <strong>State:</strong> ${state}
                </div>

                <div>
                  <strong>District:</strong> ${district}
                </div>

                <div>
                  <strong>Height:</strong> ${height} m
                </div>

                <div>
                  <strong>Completion:</strong> ${year}
                </div>

                <div>
                  <strong>Purpose:</strong> ${purpose}
                </div>

                <button
                  id="select-dam"
                  style="
                    margin-top: 12px;
                    width: 100%;
                    padding: 8px;
                    border: 0;
                    border-radius: 6px;
                    background: #111;
                    color: white;
                    cursor: pointer;
                  "
                >
                  Select Dam
                </button>
              </div>
            `)
            .addTo(map)
        })

        map.on('mouseenter', 'dam-points', () => {
          map.getCanvas().style.cursor = 'pointer'
        })

        map.on('mouseleave', 'dam-points', () => {
          map.getCanvas().style.cursor = ''
        })
      } catch (error) {
        console.error('Failed to load dam dataset:', error)
      }
    }

    map.once('load', () => {
      void handleLoad()
    })

    return () => {
      map.remove()
    }
  }, [])

  return <div ref={mapContainer} className="flood-map" />
}

export default FloodMap