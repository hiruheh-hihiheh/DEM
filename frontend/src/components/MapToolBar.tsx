import React from 'react'

export const MapToolbar: React.FC = () => {
  return (
    <div className="map-toolbar">
      <div className="map-toolbar__group">
        <button
          type="button"
          className="map-toolbar__btn"
          title="Map layers"
          aria-label="Toggle map layers"
        >
          Layers
        </button>

        <button
          type="button"
          className="map-toolbar__btn"
          title="Map legend"
          aria-label="Show map legend"
        >
          Legend
        </button>
      </div>

      <div className="map-toolbar__status">
        <span
          className="map-toolbar__indicator"
          aria-hidden="true"
        />

        Geospatial Engine Active
      </div>
    </div>
  )
}