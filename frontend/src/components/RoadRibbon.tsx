import type { FastestRoute } from '../api'
import type { ComposedRide } from '../lib/compose'
import { formatDuration } from '../lib/format'

interface RibbonSegment {
  className: string
  duration_s: number
}

interface BarProps {
  label: string
  segments: RibbonSegment[]
  totalS: number
  highwayS: number
  maxTotalS: number
}

function pct(part: number, whole: number): string {
  if (whole <= 0) return '0%' // malformed data guard: never emit NaN%/Infinity%
  return `${((part / whole) * 100).toFixed(2)}%`
}

function Bar({ label, segments, totalS, highwayS, maxTotalS }: BarProps) {
  const share = totalS > 0 ? Math.round((highwayS / totalS) * 100) : 0
  return (
    <div className="ribbon-row">
      <span className="eyebrow">{label}</span>
      <div className="ribbon-bar" style={{ width: pct(totalS, maxTotalS) }}>
        {segments
          .filter((segment) => segment.duration_s > 0)
          .map((segment, index) => (
            <span
              key={index}
              className={`ribbon-seg ${segment.className}`}
              style={{ width: pct(segment.duration_s, totalS) }}
            />
          ))}
      </div>
      <div className="ribbon-meta">
        <span>{formatDuration(totalS)}</span>
        <span>{share}% highway</span>
      </div>
    </div>
  )
}

interface RoadRibbonProps {
  fastest: FastestRoute
  ride: ComposedRide
}

/** Two-bar comparison: fastest route vs your ride, segments ∝ duration. */
export default function RoadRibbon({ fastest, ride }: RoadRibbonProps) {
  const maxTotalS = Math.max(fastest.duration_s, ride.duration_s, 1)

  const fastestSegments: RibbonSegment[] = [
    { className: 'seg-highway', duration_s: fastest.highway_duration_s },
    {
      className: 'seg-rest',
      duration_s: Math.max(fastest.duration_s - fastest.highway_duration_s, 0),
    },
  ]

  const rideSegments: RibbonSegment[] = ride.segments.map((segment) => ({
    className: `seg-${segment.kind}`,
    duration_s: segment.duration_s,
  }))

  return (
    <div className="ribbon">
      <Bar
        label="Fastest"
        segments={fastestSegments}
        totalS={fastest.duration_s}
        highwayS={fastest.highway_duration_s}
        maxTotalS={maxTotalS}
      />
      <Bar
        label="Your ride"
        segments={rideSegments}
        totalS={ride.duration_s}
        highwayS={ride.highway_duration_s}
        maxTotalS={maxTotalS}
      />
      <div className="legend">
        <span className="legend-item">
          <i className="dot dot-highway" aria-hidden="true" />
          highway
        </span>
        <span className="legend-item">
          <i className="dot dot-detour" aria-hidden="true" />
          detour
        </span>
        <span className="legend-item">
          <i className="dot dot-kept" aria-hidden="true" />
          kept
        </span>
      </div>
    </div>
  )
}
