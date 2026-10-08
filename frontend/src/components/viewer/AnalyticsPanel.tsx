import React, { useMemo } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { resultFileUrl } from '../../services/api'
import { frameTime } from '../../services/resultData'
import type { ResultManifest } from '../../types/simulation'

interface AnalyticsPanelProps {
  manifest: ResultManifest
  jobId: string
  /** Current playback frame — used to mark the live point on charts. */
  frame: number
}

const fmt = (value: number, digits = 2): string => {
  if (!Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs !== 0 && abs < 0.01) return value.toExponential(1)
  if (abs >= 10000) return value.toExponential(2)
  return value.toFixed(digits)
}

interface StatCardProps {
  label: string
  value: string
  unit?: string
  note?: string
}

const StatCard: React.FC<StatCardProps> = ({ label, value, unit, note }) => (
  <div className="stat-card">
    <div className="stat-card__label">{label}</div>
    <div className="stat-card__value">
      {value}
      {unit && <span className="stat-card__unit">{unit}</span>}
    </div>
    {note && <div className="stat-card__note">{note}</div>}
  </div>
)

/**
 * Analytics built strictly from metrics measured in the result package —
 * every number here is written by the post-processing step, nothing is
 * estimated or invented on the client.
 */
export const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({
  manifest,
  jobId,
  frame,
}) => {
  const m = manifest.metrics
  const series = manifest.series
  const tNow = frameTime(manifest, frame)

  const chartData = useMemo(() => {
    const n = series.t.length
    const rows: Record<string, number>[] = []
    for (let i = 0; i < n; i += 1) {
      rows.push({
        t: series.t[i],
        speed: series.speed_max[i],
        area: series.wet_area[i],
        extent: series.extent[i],
        reach: series.reach ? (series.reach[i] ?? NaN) : NaN,
        np: series.np[i],
      })
    }
    return rows
  }, [series])

  const tooltipStyle = {
    background: '#151b2e',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    fontSize: 12,
  }

  const axisProps = {
    stroke: '#5a6580',
    fontSize: 10,
    tickLine: false,
  }

  return (
    <div className="analytics">
      <div className="analytics__cards">
        <StatCard
          label="Peak particle speed"
          value={fmt(m.peak_speed.value)}
          unit={m.peak_speed.unit}
          note={`frame ${m.peak_speed.frame + 1} · t ≈ ${fmt(m.peak_speed.time)} s`}
        />
        <StatCard
          label="Max wet area"
          value={fmt(m.wet_area.max)}
          unit={m.wet_area.unit}
          note={`final ${fmt(m.wet_area.final)} ${m.wet_area.unit}`}
        />
        <StatCard
          label="Downstream reach"
          value={m.downstream_reach ? fmt(m.downstream_reach.max) : '—'}
          unit={m.downstream_reach?.unit}
          note={m.downstream_reach ? `from ${m.downstream_reach.reference}` : 'axis reference unavailable'}
        />
        <StatCard
          label="Flow extent"
          value={m.flow_extent ? fmt(m.flow_extent.max) : '—'}
          unit={m.flow_extent?.unit}
          note={m.flow_extent ? `along ${m.flow_extent.axis}` : undefined}
        />
        <StatCard
          label="Fluid volume"
          value={m.volume_estimate ? fmt(m.volume_estimate.value) : '—'}
          unit={m.volume_estimate?.unit}
          note={m.volume_estimate ? `max Np × dp³ (dp = ${m.volume_estimate.dp})` : undefined}
        />
        <StatCard
          label="Particles (max)"
          value={m.particles.max.toLocaleString()}
          note={`min ${m.particles.min.toLocaleString()} · ${
            m.parts_out != null ? `${m.parts_out.toLocaleString()} left domain` : 'outflow n/a'
          }`}
        />
      </div>

      <div className="analytics__charts">
        <div className="chart-card">
          <div className="chart-card__title">
            Peak particle speed <span>{m.peak_speed.unit}</span>
          </div>
          <div style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 6, right: 10, left: -14, bottom: 0 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="t" label={{ value: 't (s)', position: 'insideBottomRight', offset: -2, fontSize: 10, fill: '#5a6580' }} {...axisProps} />
                <YAxis {...axisProps} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#8b95b0' }} />
                <ReferenceLine x={tNow} stroke="#38bdf8" strokeDasharray="3 3" />
                <Line type="monotone" dataKey="speed" stroke="#f87171" dot={false} strokeWidth={1.8} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="chart-card">
          <div className="chart-card__title">
            Wet area <span>{m.wet_area.unit}</span>
          </div>
          <div style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 6, right: 10, left: -14, bottom: 0 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="t" label={{ value: 't (s)', position: 'insideBottomRight', offset: -2, fontSize: 10, fill: '#5a6580' }} {...axisProps} />
                <YAxis {...axisProps} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#8b95b0' }} />
                <ReferenceLine x={tNow} stroke="#38bdf8" strokeDasharray="3 3" />
                <Line type="monotone" dataKey="area" stroke="#38bdf8" dot={false} strokeWidth={1.8} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="chart-card">
          <div className="chart-card__title">
            Downstream reach &amp; flow extent{' '}
            <span>{m.downstream_reach?.unit ?? m.flow_extent?.unit ?? 'model units'}</span>
          </div>
          <div style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 6, right: 10, left: -14, bottom: 0 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="t" label={{ value: 't (s)', position: 'insideBottomRight', offset: -2, fontSize: 10, fill: '#5a6580' }} {...axisProps} />
                <YAxis {...axisProps} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#8b95b0' }} />
                <ReferenceLine x={tNow} stroke="#38bdf8" strokeDasharray="3 3" />
                {series.reach && (
                  <Line type="monotone" dataKey="reach" stroke="#34d399" dot={false} strokeWidth={1.8} isAnimationActive={false} connectNulls />
                )}
                <Line type="monotone" dataKey="extent" stroke="#fbbf24" dot={false} strokeWidth={1.8} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="chart-card">
          <div className="chart-card__title">
            Particle count <span>sampled</span>
          </div>
          <div style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 6, right: 10, left: -14, bottom: 0 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="t" label={{ value: 't (s)', position: 'insideBottomRight', offset: -2, fontSize: 10, fill: '#5a6580' }} {...axisProps} />
                <YAxis {...axisProps} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#8b95b0' }} />
                <ReferenceLine x={tNow} stroke="#38bdf8" strokeDasharray="3 3" />
                <Line type="monotone" dataKey="np" stroke="#818cf8" dot={false} strokeWidth={1.8} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <p className="param-note mt-3">
        {m.units_note ??
          'All values are measured from the simulation output and reported in model units.'}{' '}
        Frame times {manifest.frames.time_known ? 'come from RunPARTs.csv.' : 'are unknown for this run; charts use frame indices.'}
      </p>

      {manifest.files.analytics && (
        <p className="param-note">
          <a
            className="btn btn--sm mt-3"
            href={resultFileUrl(jobId, 'analytics.csv')}
            download="analytics.csv"
          >
            Download analytics.csv
          </a>
        </p>
      )}
    </div>
  )
}
