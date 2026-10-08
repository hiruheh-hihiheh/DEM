import React from 'react'
import type { JobStage, StageStatus } from '../types/simulation'

interface ProgressStagesProps {
  stages: JobStage[]
}

const ICONS: Record<StageStatus, string> = {
  done: '✓',
  active: '●',
  failed: '✗',
  pending: '○',
}

export const ProgressStages: React.FC<ProgressStagesProps> = ({
  stages,
}) => {
  return (
    <ol className="stage-list">
      {stages.map((stage) => (
        <li
          key={stage.id}
          className={`stage-item stage-item--${stage.status}`}
        >
          <span className="stage-icon" aria-hidden="true">
            {stage.status === 'active' ? (
              <span className="spinner" />
            ) : (
              ICONS[stage.status]
            )}
          </span>

          <div>
            <div className="stage-label">{stage.label}</div>

            {stage.detail && (
              <div className="stage-detail">{stage.detail}</div>
            )}

            {stage.status === 'active' && stage.progress > 0 && (
              <div className="stage-mini-bar">
                <div
                  style={{
                    width: `${Math.round(stage.progress * 100)}%`,
                  }}
                />
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}
