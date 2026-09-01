import { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

function FloodMap() {
  const mapContainer = useRef<HTMLDivElement | null>(null)
  const map = useRef<maplibregl.Map | null>(null)

  useEffect(() => {
    if (!mapContainer.current || map.current) {
      return
    }

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      center: [78.9629, 20.5937],
      zoom: 4.5,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: [
              'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            ],
            tileSize: 256,
            attribution:
              '© OpenStreetMap contributors',
          },
        },
        layers: [
          {
            id: 'osm',
            type: 'raster',
            source: 'osm',
          },
        ],
      },
    })

    map.current.addControl(
      new maplibregl.NavigationControl(),
      'top-right',
    )

    return () => {
      map.current?.remove()
      map.current = null
    }
  }, [])

  return <div ref={mapContainer} className="flood-map" />
}

export default FloodMap