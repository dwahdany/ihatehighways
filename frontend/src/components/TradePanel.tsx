import type { PlanResponse } from '../api'
import { formatSignedMinutes, minutes } from '../lib/format'
import RoadRibbon from './RoadRibbon'
import DetourList from './DetourList'

interface TradePanelProps {
  plan: PlanResponse
}

export default function TradePanel({ plan }: TradePanelProps) {
  const { fastest, ride, detours } = plan
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

      <RoadRibbon fastest={fastest} ride={ride} />

      <a
        className="btn-secondary"
        href={ride.gmaps_url}
        target="_blank"
        rel="noopener noreferrer"
      >
        Open in Google Maps
      </a>

      {detours.length > 0 ? (
        <DetourList detours={detours} />
      ) : fastest.highway_duration_s === 0 ? (
        <p className="note">No highway on the fastest route — enjoy.</p>
      ) : (
        <p className="note">No detour fits that budget here. Try +10 more minutes.</p>
      )}
    </section>
  )
}
