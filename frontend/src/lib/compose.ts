/**
 * Client-side ride composition: a scout result + a set of selected cut ids fully
 * determines the ride. Totals are additive (fastest + Σ selected extras), so toggling
 * cuts is instant — no backend round-trip.
 */

import type { Cut, ScoutResponse, SegmentKind } from '../api'

export interface ComposedSegment {
  kind: SegmentKind
  duration_s: number
}

export interface ComposedRide {
  duration_s: number
  extra_duration_s: number
  distance_m: number
  highway_duration_s: number
  highway_distance_m: number
  segments: ComposedSegment[]
}

export function composeRide(scout: ScoutResponse, selected: ReadonlySet<string>): ComposedRide {
  const cutsById = new Map(scout.cuts.map((cut) => [cut.id, cut]))
  const segments: ComposedSegment[] = []
  let duration = 0
  let distance = 0
  let highwayS = 0
  let highwayM = 0

  for (const part of scout.skeleton) {
    const cut = part.cut_id && selected.has(part.cut_id) ? cutsById.get(part.cut_id) : undefined
    if (cut) {
      segments.push({ kind: 'detour', duration_s: cut.detour_duration_s })
      duration += cut.detour_duration_s
      distance += cut.detour_distance_m
      // Residual highway inside the detour (soft avoidance): baseline − avoided.
      highwayS += Math.max(part.duration_s - cut.avoided_highway_s, 0)
      highwayM += Math.max(part.distance_m - cut.avoided_highway_m, 0)
    } else {
      segments.push({ kind: part.kind, duration_s: part.duration_s })
      duration += part.duration_s
      distance += part.distance_m
      if (part.kind === 'highway') {
        highwayS += part.duration_s
        highwayM += part.distance_m
      }
    }
  }

  // Merge adjacent same-kind segments so the ribbon reads clean.
  const merged: ComposedSegment[] = []
  for (const segment of segments) {
    const last = merged[merged.length - 1]
    if (last && last.kind === segment.kind) last.duration_s += segment.duration_s
    else merged.push({ ...segment })
  }

  const extra = scout.cuts
    .filter((cut) => selected.has(cut.id))
    .reduce((sum, cut) => sum + cut.extra_duration_s, 0)

  return {
    // Skeleton part durations are rounded ints; anchor totals on the exact fastest
    // duration + extras so numbers stay consistent with the cut list.
    duration_s: scout.fastest.duration_s + extra,
    extra_duration_s: extra,
    distance_m: distance,
    highway_duration_s: highwayS,
    highway_distance_m: highwayM,
    segments: merged,
  }
}

// Mirrors the backend's worth gate constants (config.py: curvy_boost, curviness_cap).
const CURVY_BOOST = 2.0
const CURVINESS_CAP = 1.7

/**
 * Fun-per-second-paid: highway time shed, boosted by curviness, per extra second.
 * A 1.5×-curvy sweep justifies more time loss than an arrow-straight B-road.
 */
export function cutWorth(cut: Cut): number {
  if (cut.extra_duration_s <= 0) return Number.POSITIVE_INFINITY
  const boost = 1 + CURVY_BOOST * (Math.min(cut.curviness, CURVINESS_CAP) - 1)
  return (cut.avoided_highway_s * boost) / cut.extra_duration_s
}

const GOOD_DEAL_MIN_WORTH = 1.0
// Default-selection sanity budget: individually-good cuts must not quietly turn a
// 7 h ride into a 13 h one. The rider can always toggle more on.
const GOOD_DEAL_BUDGET_FRACTION = 0.15
const GOOD_DEAL_MIN_BUDGET_S = 15 * 60

export function presetSelection(
  scout: ScoutResponse,
  preset: 'fastest' | 'value' | 'country',
): Set<string> {
  switch (preset) {
    case 'fastest':
      // Free cuts still belong in "fastest": the highway is jammed there.
      return new Set(scout.cuts.filter((c) => c.extra_duration_s <= 0).map((c) => c.id))
    case 'value': {
      // Best trades first, within ~15% of the fastest time in total.
      const budget = Math.max(
        scout.fastest.duration_s * GOOD_DEAL_BUDGET_FRACTION,
        GOOD_DEAL_MIN_BUDGET_S,
      )
      const picked = new Set<string>()
      let spent = 0
      const ranked = [...scout.cuts].sort((a, b) => cutWorth(b) - cutWorth(a))
      for (const cut of ranked) {
        if (cutWorth(cut) < GOOD_DEAL_MIN_WORTH) break
        const cost = Math.max(cut.extra_duration_s, 0)
        if (spent + cost > budget) continue // a cheaper good deal may still fit
        picked.add(cut.id)
        spent += cost
      }
      return picked
    }
    case 'country':
      return new Set(scout.cuts.map((c) => c.id))
  }
}

const GMAPS_MAX_WAYPOINTS = 9

export interface Handoff {
  url: string
  /** Cuts pinned entry+mid+exit — Google follows them closely. */
  full: number
  /** Cuts pinned entry+exit only (waypoint cap) — Google may straighten them. */
  loose: number
  /** Cuts that didn't fit Google's 9-waypoint budget at all. */
  dropped: number
}

/**
 * Google Maps deep link pinning each selected cut with entry/mid/exit waypoints,
 * plus a fidelity report. Google caps directions links at 9 waypoints and re-routes
 * freely between pins (avoid-highways does NOT carry over), so midpoints of the
 * least valuable cuts are dropped first, then whole cuts — and the UI must say so.
 */
export function buildHandoff(scout: ScoutResponse, selected: ReadonlySet<string>): Handoff {
  const cuts = scout.cuts.filter((cut) => selected.has(cut.id))
  const withMid = cuts.map(() => true)
  const byValue = cuts
    .map((cut, index) => ({ cut, index }))
    .sort((a, b) => a.cut.avoided_highway_s - b.cut.avoided_highway_s)

  const total = () => withMid.reduce((sum, m) => sum + (m ? 3 : 2), 0)
  for (const { index } of byValue) {
    if (total() <= GMAPS_MAX_WAYPOINTS) break
    withMid[index] = false
  }
  const dropped = new Set<number>()
  for (const { index } of byValue) {
    if (total() - 2 * dropped.size <= GMAPS_MAX_WAYPOINTS) break
    dropped.add(index)
  }

  const fmt = (p: { lat: number; lng: number }) => `${p.lat.toFixed(5)},${p.lng.toFixed(5)}`
  const waypoints: string[] = []
  cuts.forEach((cut, index) => {
    if (dropped.has(index)) return
    waypoints.push(fmt(cut.entry))
    if (withMid[index]) waypoints.push(fmt(cut.mid))
    waypoints.push(fmt(cut.exit))
  })

  const params = new URLSearchParams({
    api: '1',
    origin: fmt(scout.origin),
    destination: fmt(scout.destination),
    travelmode: 'driving',
  })
  if (waypoints.length > 0) params.set('waypoints', waypoints.join('|'))

  const kept = cuts.length - dropped.size
  const loose = cuts.filter((_, index) => !dropped.has(index) && !withMid[index]).length
  return {
    url: `https://www.google.com/maps/dir/?${params.toString()}`,
    full: kept - loose,
    loose,
    dropped: dropped.size,
  }
}
