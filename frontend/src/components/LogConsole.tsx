import React, { useEffect, useRef, useState } from 'react'
import { fetchJobLogs } from '../services/api'

interface LogConsoleProps {
  jobId: string
  /** Poll for new lines while true (i.e. the job is still running). */
  live: boolean
  height?: number
}

/**
 * Follows a job's captured runner output, polling incrementally by offset.
 */
export const LogConsole: React.FC<LogConsoleProps> = ({
  jobId,
  live,
  height,
}) => {
  const [lines, setLines] = useState<string[]>([])
  const [offset, setOffset] = useState(0)
  const boxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let cancelled = false
    let nextOffset = 0
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      try {
        const data = await fetchJobLogs(jobId, nextOffset)
        if (!cancelled && (data.lines.length > 0 || nextOffset === 0)) {
          setLines((prev) => [...prev, ...data.lines])
          nextOffset = data.offset
          setOffset(data.offset)
        }
      } catch {
        // backend unreachable — keep the last content
      }
      if (!cancelled && live) {
        timer = setTimeout(poll, 1000)
      }
    }

    queueMicrotask(() => {
      setLines([])
      setOffset(0)
    })
    void poll()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [jobId, live])

  useEffect(() => {
    const box = boxRef.current
    if (box) {
      box.scrollTop = box.scrollHeight
    }
  }, [lines])

  return (
    <div
      ref={boxRef}
      className="log-console"
      style={height ? { height } : undefined}
      role="log"
      aria-label="Runner output"
    >
      {lines.length === 0 && <span className="muted">Waiting for runner output…</span>}
      {lines.map((line, index) => (
        <div key={`${index}-${line.slice(0, 12)}`}>{line || ' '}</div>
      ))}
      {live && <span className="log-console__cursor" />}
      <span hidden>{offset}</span>
    </div>
  )
}
