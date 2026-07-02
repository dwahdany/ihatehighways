import { Fragment, useEffect, useMemo } from 'react'
import { AdvancedMarker, Polyline, useMap, useMapsLibrary } from '@vis.gl/react-google-maps'
import type { PlanResponse, SegmentKind } from '../api'

// Google Maps overlays need literal colors; keep in sync with theme.css tokens.
const ROUTE_GRAY = '#7A828C'
const SEGMENT_STYLE: Record<SegmentKind, { color: string; weight: number; opacity: number }> = {
  highway: { color: '#5B8DD9', weight: 5, opacity: 1 },
  kept: { color: '#C9A227', weight: 5, opacity: 1 },
  detour: { color: '#F7C948', weight: 6, opacity: 0.95 },
}

interface RouteLayerProps {
  plan: PlanResponse
}

export default function RouteLayer({ plan }: RouteLayerProps) {
  const map = useMap()
  const geometry = useMapsLibrary('geometry')

  const endpoints = useMemo(() => {
    if (!geometry || plan.ride.segments.length === 0) return null
    const segments = plan.ride.segments
    const first = geometry.encoding.decodePath(segments[0].encoded_polyline)
    const last = geometry.encoding.decodePath(segments[segments.length - 1].encoded_polyline)
    if (first.length === 0 || last.length === 0) return null
    return { start: first[0].toJSON(), end: last[last.length - 1].toJSON() }
  }, [geometry, plan])

  // Fit the ride into view, clearing the floating panel. Keep in sync with
  // theme.css .panel: docked left (372px + 16px inset) on desktop, bottom
  // sheet (max-height 52vh) at <=840px — a hardcoded left:420 there would
  // exceed the map width and wreck the fit.
  useEffect(() => {
    if (!map || !geometry) return
    const bounds = new google.maps.LatLngBounds()
    for (const segment of plan.ride.segments) {
      for (const point of geometry.encoding.decodePath(segment.encoded_polyline)) {
        bounds.extend(point)
      }
    }
    if (bounds.isEmpty()) return
    const narrow = window.matchMedia('(max-width: 840px)').matches
    map.fitBounds(
      bounds,
      narrow
        ? { left: 16, top: 40, right: 16, bottom: Math.round(window.innerHeight * 0.52) + 24 }
        : { left: 420, top: 40, right: 40, bottom: 40 },
    )
  }, [map, geometry, plan])

  return (
    <>
      {/* fastest route underlay */}
      <Polyline
        encodedPath={plan.fastest.encoded_polyline}
        strokeColor={ROUTE_GRAY}
        strokeWeight={4}
        strokeOpacity={0.55}
        zIndex={1}
      />
      {/* ride segments */}
      {plan.ride.segments.map((segment, index) => {
        const style = SEGMENT_STYLE[segment.kind]
        return (
          <Polyline
            key={index}
            encodedPath={segment.encoded_polyline}
            strokeColor={style.color}
            strokeWeight={style.weight}
            strokeOpacity={style.opacity}
            zIndex={2}
          />
        )
      })}
      {/* detour entry/exit dots */}
      {plan.detours.map((detour, index) => (
        <Fragment key={index}>
          <AdvancedMarker position={detour.entry} title="Detour entry">
            <div className="marker marker-node" />
          </AdvancedMarker>
          <AdvancedMarker position={detour.exit} title="Detour exit">
            <div className="marker marker-node" />
          </AdvancedMarker>
        </Fragment>
      ))}
      {/* start / end */}
      {endpoints && (
        <>
          <AdvancedMarker position={endpoints.start} title="Start">
            <div className="marker marker-start" />
          </AdvancedMarker>
          <AdvancedMarker position={endpoints.end} title="End">
            <div className="marker marker-end" />
          </AdvancedMarker>
        </>
      )}
    </>
  )
}
