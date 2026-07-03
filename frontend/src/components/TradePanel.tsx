import type { FastestRoute } from '../api'
import type { ComposedRide, ComposedSegment, Handoff } from '../lib/compose'
import { formatSignedMinutes, minutes } from '../lib/format'
import RoadRibbon from './RoadRibbon'

interface TradePanelProps {
  fastest: FastestRoute
  fastestSegments: ComposedSegment[]
  ride: ComposedRide
  handoff: Handoff
}

function handoffWarning(handoff: Handoff): string | null {
  if (handoff.dropped > 0) {
    return (
      `Too many cuts for Google Maps (9-waypoint cap): ${handoff.dropped} left out` +
      (handoff.loose > 0 ? ` and ${handoff.loose} only loosely pinned` : '') +
      '. Trim the selection for a faithful handoff.'
    )
  }
  if (handoff.loose > 0) {
    return `${handoff.loose} cut${handoff.loose === 1 ? ' is' : 's are'} loosely pinned (9-waypoint cap) — Google may straighten ${handoff.loose === 1 ? 'it' : 'them'}.`
  }
  return null
}

export default function TradePanel({ fastest, fastestSegments, ride, handoff }: TradePanelProps) {
  const avoidedHighwayS = Math.max(fastest.highway_duration_s - ride.highway_duration_s, 0)

  return (
    <section className="trade-panel" aria-label="Your ride">
      <div className="trade">
        <div className="trade-cell">
          <p className="trade-num trade-pay">
            {formatSignedMinutes(ride.extra_duration_s)}
            <span className="trade-unit"> min</span>
          </p>
          <p className="eyebrow">you trade</p>
        </div>
        <div className="trade-cell">
          <p className="trade-num trade-shed">
            −{minutes(avoidedHighwayS)}
            <span className="trade-unit"> min highway</span>
          </p>
          <p className="eyebrow">you shed</p>
        </div>
      </div>

      <RoadRibbon fastest={fastest} fastestSegments={fastestSegments} ride={ride} />

      <a className="btn-secondary" href={handoff.url} target="_blank" rel="noopener noreferrer">
        Open in Google Maps
      </a>
      {handoffWarning(handoff) && <p className="handoff-warning">{handoffWarning(handoff)}</p>}
      <p className="handoff-note">
        Google rebuilds the route between pins — small differences are normal.
      </p>
    </section>
  )
}
