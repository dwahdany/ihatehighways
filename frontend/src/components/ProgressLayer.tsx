import { useEffect, useState } from 'react'
import { AdvancedMarker, Polyline, useMap, useMapsLibrary } from '@vis.gl/react-google-maps'
import type { ScoutProgress } from '../lib/progress'
import { corridorKey } from '../lib/progress'

const BLUE = '#5B8DD9'
const YELLOW = '#F7C948'
const YELLOW_DIM = '#C9A227'
const PROBE_FLASH = '#EDEAE3'

// Cosmetic mirror of the backend's osm_min_curvy_km gate.
const GOOD_SCORE = 2.0
// Rejected probes flash and dissolve over this window.
const PROBE_FADE_MS = 2200

/** Progressive map while scouting: route skeleton, corridor scan dots, live cuts. */
export default function ProgressLayer({ progress }: { progress: ScoutProgress }) {
  const map = useMap()
  const geometry = useMapsLibrary('geometry')
  const hasRoute = progress.fastest !== null

  // Ticker driving the fade of rejected probes; only runs while any are visible.
  const [now, setNow] = useState(() => Date.now())
  const hasFadingProbes = progress.probes.some(
    (probe) => !probe.kept && Date.now() - probe.at < PROBE_FADE_MS,
  )
  useEffect(() => {
    if (!hasFadingProbes) return
    const timer = setInterval(() => setNow(Date.now()), 200)
    return () => clearInterval(timer)
  }, [hasFadingProbes])

  useEffect(() => {
    if (!map || !geometry || !progress.fastest) return
    const bounds = new google.maps.LatLngBounds()
    for (const point of geometry.encoding.decodePath(progress.fastest.encoded_polyline)) {
      bounds.extend(point)
    }
    if (bounds.isEmpty()) return
    const narrow = window.matchMedia('(max-width: 840px)').matches
    map.fitBounds(
      bounds,
      narrow
        ? { left: 16, top: 40, right: 16, bottom: Math.round(window.innerHeight * 0.52) + 24 }
        : { left: 420, top: 40, right: 40, bottom: 40 },
    )
    // Fit exactly once per scout, the moment the route preview lands.
  }, [map, geometry, hasRoute]) // eslint-disable-line react-hooks/exhaustive-deps

  const scoreByCorridor = new Map(progress.scored.map((c) => [corridorKey(c), c.curvy_km]))

  return (
    <>
      {progress.preview.map((part, index) => (
        <Polyline
          key={`preview-${index}`}
          encodedPath={part.encoded_polyline}
          strokeColor={part.kind === 'highway' ? BLUE : YELLOW_DIM}
          strokeWeight={4}
          strokeOpacity={0.8}
          zIndex={1}
          clickable={false}
        />
      ))}

      {progress.corridors.map((corridor) => {
        const score = scoreByCorridor.get(corridorKey(corridor))
        const state =
          score === undefined ? 'scanning' : score >= GOOD_SCORE ? 'good' : 'bad'
        const mid = {
          lat: (corridor.entry.lat + corridor.exit.lat) / 2,
          lng: (corridor.entry.lng + corridor.exit.lng) / 2,
        }
        return (
          <AdvancedMarker key={corridorKey(corridor)} position={mid}>
            <div className={`marker-scan marker-scan-${state}`} />
          </AdvancedMarker>
        )
      })}

      {/* tested-and-rejected detours flash, then dissolve */}
      {progress.probes
        .filter((probe) => !probe.kept)
        .map((probe) => {
          const age = now - probe.at
          const opacity = Math.max(0, 0.55 * (1 - age / PROBE_FADE_MS))
          if (opacity <= 0.02) return null
          return (
            <Polyline
              key={`probe-${probe.id}`}
              encodedPath={probe.encoded_polyline}
              strokeColor={PROBE_FLASH}
              strokeWeight={3}
              strokeOpacity={opacity}
              zIndex={2}
              clickable={false}
            />
          )
        })}

      {progress.cuts.map((cut, index) => (
        <Polyline
          key={`live-cut-${index}`}
          encodedPath={cut.encoded_polyline}
          strokeColor={YELLOW}
          strokeWeight={5}
          strokeOpacity={0.75}
          zIndex={3}
          clickable={false}
        />
      ))}
    </>
  )
}
