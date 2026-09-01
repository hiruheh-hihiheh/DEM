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
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="12 2 2 7 12 12 22 7 12 2" />
            <polyline points="2 17 12 22 22 17" />
            <polyline points="2 12 12 17 22 12" />
          </svg>
          Layers
        </button>

        <button
          type="button"
          className="map-toolbar__btn"
          title="Map legend"
          aria-label="Show map legend"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </svg>
          Legend
        </button>

        <button
          type="button"
          className="map-toolbar__btn map-toolbar__btn--export"
          title="Export results"
          aria-label="Export simulation results"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          Export
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