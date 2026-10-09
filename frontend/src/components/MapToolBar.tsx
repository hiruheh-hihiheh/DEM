import React from 'react'
import type { BasemapMode } from '../map/basemaps'

interface MapToolbarProps {
  /** Current basemap mode. */
  basemap: BasemapMode
  /** Called when the user picks the other basemap. */
  onBasemapChange: (mode: BasemapMode) => void
  /** Optional live status text shown on the right (e.g. "6,644 dams loaded"). */
  statusText?: string
}

/**
 * Map toolbar: a working `[Standard][Satellite]` basemap switch plus a
 * live status indicator. (The old Layers/Legend/Export buttons were dead
 * controls and have been replaced.)
 */
export const MapToolbar: React.FC<MapToolbarProps> = ({
  basemap,
  onBasemapChange,
  statusText,
}) => {
  return (
    <div className="map-toolbar">
      <div className="map-toolbar__group" role="group" aria-label="Basemap style">
        <span className="map-toolbar__label">Basemap</span>
        <button
          type="button"
          className={`map-toolbar__btn${basemap === 'standard' ? ' map-toolbar__btn--active' : ''}`}
          aria-pressed={basemap === 'standard'}
          onClick={() => onBasemapChange('standard')}
        >
          Standard
        </button>
        <button
          type="button"
          className={`map-toolbar__btn${basemap === 'satellite' ? ' map-toolbar__btn--active' : ''}`}
          aria-pressed={basemap === 'satellite'}
          onClick={() => onBasemapChange('satellite')}
        >
          Satellite
        </button>
      </div>

      <div className="map-toolbar__status">
        <span
          className="map-toolbar__indicator"
          aria-hidden="true"
        />
        {statusText ?? 'Geospatial Engine Active'}
      </div>
    </div>
  )
}
