import React from 'react'

export interface ViewerLayers {
  terrain: boolean
  water: boolean
  dam: boolean
}

interface TimelineControlsProps {
  frame: number
  frameCount: number
  timeKnown: boolean
  times: number[]
  playing: boolean
  speed: number
  layers: ViewerLayers
  onSeek: (frame: number) => void
  onTogglePlay: () => void
  onSpeed: (speed: number) => void
  onToggleLayer: (layer: keyof ViewerLayers) => void
  onResetCamera: () => void
}

const SPEEDS = [0.5, 1, 2]

export const TimelineControls: React.FC<TimelineControlsProps> = ({
  frame,
  frameCount,
  timeKnown,
  times,
  playing,
  speed,
  layers,
  onSeek,
  onTogglePlay,
  onSpeed,
  onToggleLayer,
  onResetCamera,
}) => {
  const t = timeKnown ? (times[frame] ?? 0) : frame
  const tEnd = timeKnown ? (times[frameCount - 1] ?? t) : frameCount - 1

  return (
    <div className="timeline">
      <button
        type="button"
        className={`timeline__btn${playing ? ' timeline__btn--playing' : ''}`}
        onClick={onTogglePlay}
        aria-label={playing ? 'Pause' : 'Play'}
        disabled={frameCount === 0}
      >
        {playing ? '❚❚' : '▶'}
      </button>

      <button
        type="button"
        className="timeline__btn"
        onClick={() => onSeek(0)}
        aria-label="Jump to first frame"
        disabled={frameCount === 0}
      >
        ⏮
      </button>

      <input
        type="range"
        className="timeline__slider"
        min={0}
        max={Math.max(0, frameCount - 1)}
        value={frame}
        onChange={(e) => onSeek(Number(e.target.value))}
        disabled={frameCount === 0}
        aria-label="Frame"
      />

      <span className="timeline__time">
        {timeKnown ? (
          <>
            t = <strong>{t.toFixed(2)}</strong> s / {tEnd.toFixed(2)} s
          </>
        ) : (
          <>
            frame <strong>{frame + 1}</strong> / {frameCount}
          </>
        )}
        <span className="muted"> · {frame + 1}/{frameCount}</span>
      </span>

      <span className="timeline__speeds">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            className={`timeline__speed${speed === s ? ' timeline__speed--active' : ''}`}
            onClick={() => onSpeed(s)}
          >
            {s}×
          </button>
        ))}
      </span>

      <span className="layer-toggles">
        {(
          [
            ['terrain', 'Terrain'],
            ['water', 'Water'],
            ['dam', 'Dam'],
          ] as [keyof ViewerLayers, string][]
        ).map(([key, label]) => (
          <label key={key} className="layer-toggle">
            <input
              type="checkbox"
              checked={layers[key]}
              onChange={() => onToggleLayer(key)}
            />
            {label}
          </label>
        ))}
      </span>

      <button
        type="button"
        className="timeline__btn"
        onClick={onResetCamera}
        aria-label="Reset camera"
        title="Reset camera"
      >
        ⟳
      </button>
    </div>
  )
}
