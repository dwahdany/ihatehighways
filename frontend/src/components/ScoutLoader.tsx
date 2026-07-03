import type { ScoutProgress } from '../lib/progress'
import { formatDuration, formatKm, minutes } from '../lib/format'

interface Row {
  label: string
  state: 'pending' | 'active' | 'done'
  value: string
}

function buildRows(progress: ScoutProgress): Row[] {
  const routeDone = progress.fastest !== null
  const corridorsDone = progress.corridorCount !== null
  const scanDone = progress.probeCount !== null // probing starts once scoring settled
  const bestScore = progress.scored.reduce((best, c) => Math.max(best, c.curvy_km), 0)

  return [
    {
      label: 'Fastest route',
      state: routeDone ? 'done' : 'active',
      value: progress.fastest
        ? `${formatDuration(progress.fastest.duration_s)} · ${formatKm(progress.fastest.highway_distance_m)} highway`
        : '…',
    },
    {
      label: 'Highway corridors',
      state: corridorsDone ? 'done' : routeDone ? 'active' : 'pending',
      value: progress.corridorCount !== null ? `${progress.corridorCount} found` : '…',
    },
    {
      label: 'Curvy-road scan',
      state: scanDone ? 'done' : corridorsDone ? 'active' : 'pending',
      value:
        progress.scored.length > 0
          ? `${progress.scored.length}/${progress.corridorCount ?? '?'} · best ${bestScore.toFixed(1)}`
          : scanDone
            ? '—'
            : '…',
    },
    {
      // Probes launch as soon as good corridors score, so cuts can arrive while
      // the scan row is still running.
      label: 'Pricing detours',
      state: progress.probeCount !== null || progress.cuts.length > 0 ? 'active' : 'pending',
      value:
        progress.cuts.length > 0
          ? `${progress.cuts.length} cut${progress.cuts.length === 1 ? '' : 's'} found`
          : progress.probeCount !== null
            ? `probing ${progress.probeCount} corridors`
            : '…',
    },
  ]
}

const GLYPH = { pending: '·', active: '▸', done: '✓' } as const

/** Live build-pipeline checklist + road animation while the backend scouts. */
export default function ScoutLoader({ progress }: { progress: ScoutProgress }) {
  const rows = buildRows(progress)
  const latestCut = progress.cuts[progress.cuts.length - 1]

  return (
    <div className="scout-loader">
      <div className="scout-road" aria-hidden="true" />
      <ul className="scout-checklist" aria-hidden="true">
        {rows.map((row) => (
          <li key={row.label} className={`scout-row scout-${row.state}`}>
            <span className="scout-glyph">{GLYPH[row.state]}</span>
            <span className="scout-label">{row.label}</span>
            <span className="scout-value">{row.value}</span>
          </li>
        ))}
      </ul>
      {latestCut && (
        <p className="scout-caption" aria-hidden="true">
          Cut found on {latestCut.road ?? 'the highway'} · −{minutes(latestCut.avoided_highway_s)}{' '}
          min highway
        </p>
      )}
      <span className="visually-hidden">Scouting cuts, this can take a moment.</span>
    </div>
  )
}
