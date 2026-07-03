import { Fragment, useEffect } from 'react'
import { AdvancedMarker, Polyline, useMap, useMapsLibrary } from '@vis.gl/react-google-maps'
import type { ScoutResponse } from '../api'

// Google Maps overlays need literal colors; keep in sync with theme.css tokens.
const ROUTE_GRAY = '#7A828C'
const BLUE = '#5B8DD9'
const YELLOW = '#F7C948'
const YELLOW_DIM = '#C9A227'

interface RouteLayerProps {
  scout: ScoutResponse
  selected: ReadonlySet<string>
  onToggle: (id: string) => void
}

export default function RouteLayer({ scout, selected, onToggle }: RouteLayerProps) {
  const map = useMap()
  const geometry = useMapsLibrary('geometry')

  // Fit once per scout (NOT per selection — the map must not jump on toggles),
  // covering the fastest route plus every offered cut. Padding clears the floating
  // panel; keep in sync with theme.css .panel (372px + 16px inset desktop, bottom
  // sheet max-height 52vh at <=840px).
  useEffect(() => {
    if (!map || !geometry) return
    const bounds = new google.maps.LatLngBounds()
    for (const encoded of [
      scout.fastest.encoded_polyline,
      ...scout.cuts.map((cut) => cut.encoded_polyline),
    ]) {
      if (!encoded) continue
      for (const point of geometry.encoding.decodePath(encoded)) bounds.extend(point)
    }
    if (bounds.isEmpty()) return
    const narrow = window.matchMedia('(max-width: 840px)').matches
    map.fitBounds(
      bounds,
      narrow
        ? { left: 16, top: 40, right: 16, bottom: Math.round(window.innerHeight * 0.52) + 24 }
        : { left: 420, top: 40, right: 40, bottom: 40 },
    )
  }, [map, geometry, scout])

  return (
    <>
      {/* fastest route underlay */}
      <Polyline
        encodedPath={scout.fastest.encoded_polyline}
        strokeColor={ROUTE_GRAY}
        strokeWeight={4}
        strokeOpacity={0.55}
        zIndex={1}
      />

      {/* skeleton: the fastest route by kind; replaceable highway parts are clickable */}
      {scout.skeleton.map((part, index) => {
        const isReplaced = part.cut_id !== null && selected.has(part.cut_id)
        if (isReplaced) return null
        const cutId = part.cut_id
        return (
          <Polyline
            key={`part-${index}`}
            encodedPath={part.encoded_polyline}
            strokeColor={part.kind === 'highway' ? BLUE : YELLOW_DIM}
            strokeWeight={5}
            strokeOpacity={1}
            zIndex={2}
            {...(cutId !== null
              ? { onClick: () => onToggle(cutId), clickable: true }
              : { clickable: false })}
          />
        )
      })}

      {/* cuts: selected ride solid, unselected offers ghosted — both toggle on click */}
      {scout.cuts.map((cut) => {
        const on = selected.has(cut.id)
        return (
          <Fragment key={cut.id}>
            <Polyline
              encodedPath={cut.encoded_polyline}
              strokeColor={YELLOW}
              strokeWeight={on ? 6 : 4}
              strokeOpacity={on ? 0.95 : 0.35}
              zIndex={on ? 4 : 3}
              onClick={() => onToggle(cut.id)}
            />
            {on && (
              <>
                <AdvancedMarker position={cut.entry} title="Cut entry">
                  <div className="marker marker-node" />
                </AdvancedMarker>
                <AdvancedMarker position={cut.exit} title="Cut exit">
                  <div className="marker marker-node" />
                </AdvancedMarker>
              </>
            )}
          </Fragment>
        )
      })}

      {/* start / end */}
      <AdvancedMarker position={scout.origin} title="Start">
        <div className="marker marker-start" />
      </AdvancedMarker>
      <AdvancedMarker position={scout.destination} title="End">
        <div className="marker marker-end" />
      </AdvancedMarker>
    </>
  )
}
