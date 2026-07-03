import type { FastestRoute } from '../api'
import type { ComposedRide, ComposedSegment } from '../lib/compose'
import { formatSignedMinutes, minutes } from '../lib/format'
import RoadRibbon from './RoadRibbon'

interface TradePanelProps {
  fastest: FastestRoute
  fastestSegments: ComposedSegment[]
  ride: ComposedRide
  gmapsUrl: string
}

export default function TradePanel({ fastest, fastestSegments, ride, gmapsUrl }: TradePanelProps) {
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

      <a className="btn-secondary" href={gmapsUrl} target="_blank" rel="noopener noreferrer">
        Open in Google Maps
      </a>
    </section>
  )
}
