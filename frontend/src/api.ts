/**
 * Client for the ihatehighways backend. Mirrors docs/api.md exactly.
 * All durations are seconds, distances meters. Encoded polylines use the
 * standard Google encoded-polyline algorithm, precision 5.
 */

/**
 * Optional backend base URL (e.g. "http://localhost:8000") for deployments
 * without the dev proxy; requires the backend's IHH_CORS_ORIGINS to allow this
 * origin. Defaults to same-origin relative "/api" (Vite proxy in dev).
 */
const API_BASE: string = ((import.meta.env.VITE_API_BASE as string | undefined) ?? '')
  // Tolerate a trailing slash ("http://localhost:8000/"): "//api/plan" would 404.
  .replace(/\/+$/, '')

export interface LatLng {
  lat: number
  lng: number
}

/** Exactly one of `place_id`, `address`, or `lat_lng`. */
export interface Waypoint {
  place_id?: string
  address?: string
  lat_lng?: LatLng
}

export interface PlanRequest {
  origin: Waypoint
  destination: Waypoint
  /** 0–120. 0 still applies "free" detours. */
  max_extra_minutes: number
}

export interface FastestRoute {
  encoded_polyline: string
  duration_s: number
  static_duration_s: number
  distance_m: number
  highway_distance_m: number
  highway_duration_s: number
}

/**
 * `kept` — non-highway part of the fastest route, unchanged.
 * `highway` — highway part of the fastest route we could not afford to replace.
 * `detour` — replacement country-road segment.
 */
export type SegmentKind = 'kept' | 'highway' | 'detour'

export interface RideSegment {
  kind: SegmentKind
  encoded_polyline: string
  duration_s: number
  distance_m: number
}

export interface Ride {
  duration_s: number
  extra_duration_s: number
  distance_m: number
  highway_distance_m: number
  highway_duration_s: number
  /** Ordered origin → destination; polylines concatenate into the full ride. */
  segments: RideSegment[]
  /** Google Maps deep link reproducing this ride (detours pinned via waypoints). */
  gmaps_url: string
}

export interface Detour {
  entry: LatLng
  exit: LatLng
  extra_duration_s: number
  avoided_highway_s: number
  avoided_highway_m: number
  detour_distance_m: number
  curviness: number
}

export interface PlanResponse {
  budget_s: number
  fastest: FastestRoute
  ride: Ride
  detours: Detour[]
}

export interface ScoutRequest {
  origin: Waypoint
  destination: Waypoint
}

/** One toggleable highway cut: a priced country-road replacement. */
export interface Cut {
  id: string
  /** Motorway name from the instructions, e.g. "A3"; null when unknown. */
  road: string | null
  entry: LatLng
  exit: LatLng
  /** Midpoint of the detour path, for Google Maps waypoint pinning. */
  mid: LatLng
  encoded_polyline: string
  detour_duration_s: number
  detour_distance_m: number
  /** vs staying on the highway; negative = the highway is jammed, cut is free. */
  extra_duration_s: number
  avoided_highway_s: number
  avoided_highway_m: number
  curviness: number
}

/** Fastest-route piece; parts with cut_id can be swapped for that cut. */
export interface SkeletonPart {
  kind: 'kept' | 'highway'
  encoded_polyline: string
  duration_s: number
  distance_m: number
  cut_id: string | null
}

export interface ScoutResponse {
  origin: LatLng
  destination: LatLng
  fastest: FastestRoute
  /** Ordered origin → destination; polylines concatenate into the fastest route. */
  skeleton: SkeletonPart[]
  cuts: Cut[]
}

export type ApiErrorCode =
  | 'INVALID_INPUT'
  | 'GEOCODE_FAILED'
  | 'NO_ROUTE'
  | 'UPSTREAM'
  | 'RATE_LIMITED'
  | 'DAILY_CAP'

interface ErrorBody {
  detail?: {
    code?: string
    message?: string
  }
}

/** Thrown on non-2xx responses; carries the backend's `detail.code`/`message`. */
export class ApiError extends Error {
  readonly status: number
  readonly code: ApiErrorCode | string
  /** True when the documented `{detail: {code, message}}` envelope was present. */
  readonly enveloped: boolean
  /** Raw response body when unenveloped (proxy/server error page); '' otherwise. */
  readonly bodyText: string

  constructor(
    status: number,
    code: ApiErrorCode | string,
    message: string,
    enveloped = false,
    bodyText = '',
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.enveloped = enveloped
    this.bodyText = bodyText
  }
}

export async function planRoute(req: PlanRequest): Promise<PlanResponse> {
  return request<PlanResponse>('/api/plan', req)
}

export async function scoutRoute(req: ScoutRequest): Promise<ScoutResponse> {
  return request<ScoutResponse>('/api/scout', req)
}

async function request<T>(path: string, payload: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    let code: string = 'UPSTREAM'
    let message = `Plan request failed with status ${res.status}.`
    let enveloped = false
    const raw = await res.text().catch(() => '')
    try {
      const body = JSON.parse(raw) as ErrorBody
      if (body.detail && typeof body.detail === 'object' && body.detail.code) {
        enveloped = true
        code = body.detail.code
        if (body.detail.message) message = body.detail.message
      }
    } catch {
      // non-JSON error body; keep defaults
    }
    throw new ApiError(res.status, code, message, enveloped, enveloped ? '' : raw)
  }
  return (await res.json()) as T
}
