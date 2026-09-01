import React from 'react'

export type StatusType =
  | 'idle'
  | 'loading'
  | 'live'
  | 'simulation'
  | 'error'
  | 'success'

interface StatusBadgeProps {
  status: StatusType
  text: string
}

export const StatusBadge: React.FC<
  StatusBadgeProps
> = ({ status, text }) => {
  return (
    <span
      className={`status-badge status-badge--${status}`}
      role="status"
    >
      <span
        className="status-badge__dot"
        aria-hidden="true"
      />

      {text}
    </span>
  )
}