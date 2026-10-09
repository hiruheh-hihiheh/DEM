import type { StyleSpecification } from 'maplibre-gl'

/**
 * Basemap modes shared by the Dashboard and the result viewer.
 *
 * - `standard` — Carto's free dark vector basemap (no API key required).
 * - `satellite` — raster imagery tiles. Uses Esri World Imagery (free,
 *   attribution required and shown automatically by MapLibre's attribution
 *   control) unless overridden through the environment:
 *
 *       VITE_SATELLITE_TILE_URL      // must contain {z}/{y}/{x} (or {z}/{x}/{y})
 *       VITE_SATELLITE_ATTRIBUTION   // attribution string for your provider
 *
 * No API keys are committed anywhere; both modes work keyless out of the box.
 */
export type BasemapMode = 'standard' | 'satellite'

/** Free dark vector style (no key). */
export const STANDARD_STYLE_URL =
  'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'

const envTileUrl = (import.meta.env.VITE_SATELLITE_TILE_URL as string | undefined)?.trim()
const envAttribution = (
  import.meta.env.VITE_SATELLITE_ATTRIBUTION as string | undefined
)?.trim()

/** Raster tile URL template for Satellite mode (default: Esri World Imagery). */
export const SATELLITE_TILE_URL =
  envTileUrl && envTileUrl.length > 0
    ? envTileUrl
    : 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

/** Attribution for the satellite provider (shown in the map corner). */
export const SATELLITE_ATTRIBUTION =
  envAttribution && envAttribution.length > 0
    ? envAttribution
    : 'Esri, Maxar, Earthstar Geographics'

/** Build the satellite style (background + single raster layer). */
export function satelliteStyle(): StyleSpecification {
  return {
    version: 8,
    name: 'satellite',
    sources: {
      satellite: {
        type: 'raster',
        tiles: [SATELLITE_TILE_URL],
        tileSize: 256,
        maxzoom: 19,
        attribution: SATELLITE_ATTRIBUTION,
      },
    },
    layers: [
      {
        id: 'background',
        type: 'background',
        paint: { 'background-color': '#0b1220' },
      },
      {
        id: 'satellite-raster',
        type: 'raster',
        source: 'satellite',
        paint: { 'raster-opacity': 1, 'raster-fade-duration': 150 },
      },
    ],
  }
}

/** Initial view shared by both modes. */
export const MAP_INITIAL_VIEW = {
  center: [78.9629, 22.5937] as [number, number],
  zoom: 4,
}

/** Style for a basemap mode (URL for vector, inline style for raster). */
export function basemapStyle(mode: BasemapMode): string | StyleSpecification {
  return mode === 'satellite' ? satelliteStyle() : STANDARD_STYLE_URL
}
