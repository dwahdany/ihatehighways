/**
 * Live scouting progress: a pure reducer over /api/scout/stream events, feeding the
 * loader checklist and the progressive map layer.
 */

import type {
  CorridorRef,
  Cut,
  FastestRoute,
  LatLng,
  ScoredCorridor,
  ScoutEvent,
  SkeletonPart,
} from '../api'

export interface TestedProbe {
  id: number
  encoded_polyline: string
  kept: boolean
  /** Client receive time (ms) — drives the fade-out of rejected probes. */
  at: number
}

export interface ScoutProgress {
  fastest: FastestRoute | null
  preview: SkeletonPart[]
  origin: LatLng | null
  destination: LatLng | null
  corridorCount: number | null
  corridors: CorridorRef[]
  scored: ScoredCorridor[]
  probeCount: number | null
  probes: TestedProbe[]
  cuts: Cut[]
}

export const EMPTY_PROGRESS: ScoutProgress = {
  fastest: null,
  preview: [],
  origin: null,
  destination: null,
  corridorCount: null,
  corridors: [],
  scored: [],
  probeCount: null,
  probes: [],
  cuts: [],
}

const MAX_PROBES_SHOWN = 24
let probeSequence = 0

/** Fold one stream event into the progress state (done/error handled by the caller). */
export function applyEvent(progress: ScoutProgress, event: ScoutEvent): ScoutProgress {
  switch (event.type) {
    case 'route':
      return {
        ...progress,
        fastest: event.fastest,
        preview: event.preview,
        origin: event.origin,
        destination: event.destination,
      }
    case 'corridors':
      return { ...progress, corridorCount: event.count, corridors: event.corridors }
    case 'scored':
      return { ...progress, scored: [...progress.scored, ...event.corridors] }
    case 'probing':
      return { ...progress, probeCount: event.count }
    case 'probe':
      return {
        ...progress,
        probes: [
          ...progress.probes.slice(-MAX_PROBES_SHOWN),
          {
            id: probeSequence++,
            encoded_polyline: event.encoded_polyline,
            kept: event.kept,
            at: Date.now(),
          },
        ],
      }
    case 'cut':
      return { ...progress, cuts: [...progress.cuts, event.cut] }
    default:
      return progress
  }
}

export function corridorKey(ref: CorridorRef): string {
  // Entry AND exit: entry-only (~110 m cells) collides when a route leaves and
  // re-enters a motorway at the same interchange.
  return `${ref.entry.lat.toFixed(3)},${ref.entry.lng.toFixed(3)}->${ref.exit.lat.toFixed(3)},${ref.exit.lng.toFixed(3)}`
}
