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
      zoom: 4,
    })

    map.addControl(
      new maplibregl.NavigationControl(),
      'top-right',
    )

    map.on('load', async () => {
      try {
        console.log('Map loaded')

        const response =
          await api.get<DamFeatureCollection>('/api/dams')

        const damData = response.data

        console.log(
          'Dam count:',
          damData.features.length,
        )

        console.log(
          'First dam:',
          damData.features[0],
        )

        map.addSource('dams', {
          type: 'geojson',
          data: damData,
        })

        map.addLayer({
          id: 'dam-points',
          type: 'circle',
          source: 'dams',
          paint: {
            'circle-radius': 7,
            'circle-color': '#ff1f1f',
            'circle-stroke-width': 2,
            'circle-stroke-color': '#ffffff',
            'circle-opacity': 1,
          },
        })

        console.log(
          'Dam source:',
          map.getSource('dams'),
        )

        console.log(
          'Dam layer exists:',
          map.getLayer('dam-points'),
        )

        map.on('click', 'dam-points', (event) => {
          const feature = event.features?.[0]

          if (!feature) {
            return
          }

          const properties = feature.properties

          new maplibregl.Popup({
            maxWidth: '320px',
          })
            .setLngLat(event.lngLat)
            .setHTML(`
              <div style="
                font-family: system-ui, sans-serif;
                line-height: 1.5;
              ">
                <h3 style="margin: 0 0 10px;">
                  ${properties?.name ?? 'Unknown Dam'}
                </h3>

                <div>
                  <strong>River:</strong>
                  ${properties?.river ?? 'Unknown'}
                </div>

                <div>
                  <strong>State:</strong>
                  ${properties?.state ?? 'Unknown'}
                </div>

                <div>
                  <strong>District:</strong>
                  ${properties?.district ?? 'Unknown'}
                </div>

                <div>
                  <strong>Height:</strong>
                  ${properties?.height ?? '—'} m
                </div>

                <div>
                  <strong>Purpose:</strong>
                  ${properties?.purpose ?? '—'}
                </div>
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
        console.error(
          'Failed to load dams:',
          error,
        )
      }
    })

    return () => {
      map.remove()
    }
  }, [])

  return (
    <div
      ref={mapContainer}
      className="flood-map"
    />
  )
}

export default FloodMap