import type { Cut } from '../api'
import { cutWorth } from '../lib/compose'
import { formatKm, formatSignedMinutes, minutes } from '../lib/format'

interface CutListProps {
  cuts: Cut[]
  selected: ReadonlySet<string>
  onToggle: (id: string) => void
}

/** The menu of highway cuts, best trades first (worth = curviness-boosted highway
 * time shed per extra minute; the map stays spatial, the list is the ranking). */
export default function CutList({ cuts, selected, onToggle }: CutListProps) {
  const ranked = [...cuts].sort((a, b) => cutWorth(b) - cutWorth(a))
  return (
    <ul className="cut-list">
      {ranked.map((cut) => {
        const on = selected.has(cut.id)
        const free = cut.extra_duration_s <= 0
        return (
          <li key={cut.id}>
            <button
              type="button"
              role="switch"
              aria-checked={on}
              className={`cut-row${on ? ' cut-on' : ''}`}
              onClick={() => onToggle(cut.id)}
            >
              <span className="cut-switch" aria-hidden="true" />
              <span className="cut-body">
                <span className="cut-title">
                  Cut {cut.road ?? 'the highway'} · −{minutes(cut.avoided_highway_s)} min highway
                  {free && <span className="badge-free">free — jammed</span>}
                </span>
                <span className="cut-sub">
                  {formatSignedMinutes(cut.extra_duration_s)} min ·{' '}
                  {formatKm(cut.detour_distance_m)} of country road · {cut.curviness.toFixed(1)}×
                  curvier
                </span>
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
