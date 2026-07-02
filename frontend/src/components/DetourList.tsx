import type { Detour } from '../api'
import { formatKm, formatSignedMinutes } from '../lib/format'

interface DetourListProps {
  detours: Detour[]
}

export default function DetourList({ detours }: DetourListProps) {
  return (
    <div className="detour-block">
      <span className="eyebrow">Detours</span>
      <ul className="detour-list">
        {detours.map((detour, index) => (
          <li key={index} className="detour-row">
            <span className="detour-pay">{formatSignedMinutes(detour.extra_duration_s)} min</span>
            <span className="detour-sep"> · </span>
            <span className="detour-shed">−{formatKm(detour.avoided_highway_m)} highway</span>
            <span className="detour-sep"> · </span>
            <span>{detour.curviness.toFixed(1)}× curvier</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
