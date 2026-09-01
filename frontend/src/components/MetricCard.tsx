import React from 'react'

interface MetricCardProps {
  label: string
  value: string | number
  unit: string
  description?: string
  status?: 'normal' | 'warning' | 'critical'
  icon?: React.ReactNode
}

export const MetricCard: React.FC<
  MetricCardProps
> = ({
  label,
  value,
  unit,
  description,
  status = 'normal',
  icon,
}) => {
  return (
    <article
      className={`metric-card metric-card--${status}`}
    >
      <div className="metric-card__header">
        {icon && (
          <div className="metric-card__icon">
            {icon}
          </div>
        )}

        <span className="metric-card__label">
          {label}
        </span>
      </div>

      <div className="metric-card__body">
        <span className="metric-card__value">
          {value}
        </span>

        <span className="metric-card__unit">
          {unit}
        </span>
      </div>

      {description && (
        <div className="metric-card__description">
          {description}
        </div>
      )}
    </article>
  )
}