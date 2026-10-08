import React from 'react'

export type View =
  | 'dashboard'
  | 'dams'
  | 'configure'
  | 'run'
  | 'history'
  | 'viewer'

interface NavBarProps {
  view: View
  onNavigate: (view: View) => void
  activeJobRunning: boolean
  hasActiveJob: boolean
}

const TABS: { id: View; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'dams', label: 'Dams' },
  { id: 'configure', label: 'New simulation' },
  { id: 'run', label: 'Progress' },
  { id: 'history', label: 'History' },
]

export const NavBar: React.FC<NavBarProps> = ({
  view,
  onNavigate,
  activeJobRunning,
  hasActiveJob,
}) => {
  return (
    <nav className="app-nav" aria-label="Primary">
      {TABS.map((tab) => {
        const isActive =
          view === tab.id ||
          (tab.id === 'run' && view === 'viewer' && hasActiveJob)

        return (
          <button
            key={tab.id}
            type="button"
            className={`nav-tab${isActive ? ' nav-tab--active' : ''}`}
            onClick={() => onNavigate(tab.id)}
          >
            {tab.label}
            {tab.id === 'run' && activeJobRunning && (
              <span className="nav-tab__badge nav-tab__badge--live">
                live
              </span>
            )}
            {tab.id === 'run' &&
              !activeJobRunning &&
              hasActiveJob &&
              view !== 'run' && (
                <span className="nav-tab__badge">1</span>
              )}
          </button>
        )
      })}
      <div className="nav-spacer" />
      <span className="nav-meta">
        Scenario Runner · DualSPHysics
      </span>
    </nav>
  )
}
