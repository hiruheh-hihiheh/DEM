import React from 'react'

interface HeaderProps {
  status: React.ReactNode
}

export const Header: React.FC<HeaderProps> = ({
  status,
}) => {
  return (
    <header className="app-header">
      <div className="app-header__branding">
        <div className="app-header__title">
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
          >
            <path
              d="M12 2L2 7L12 12L22 7L12 2Z"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d="M2 17L12 22L22 17"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d="M2 12L12 17L22 12"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>

          <span>Flood-Twin</span>
        </div>

        <span className="app-header__subtitle">
          Hydrological Simulation &amp; Disaster Management
        </span>
      </div>

      <div className="app-header__status">
        {status}
      </div>
    </header>
  )
}