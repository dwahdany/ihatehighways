# ihatehighways — backend API contract

Frozen contract between backend (FastAPI) and clients (web now, iOS later).
All durations are seconds, distances meters. Encoded polylines use the standard
Google encoded-polyline algorithm, precision 5.

## `POST /api/plan`

Request:

```json
{
  "origin":      { "place_id": "ChIJ..." },
  "destination": { "address": "Frankfurt am Main" },
  "max_extra_minutes": 15
}
```

- `origin` / `destination`: exactly one of `place_id`, `address`, or
  `lat_lng: {"lat": 50.94, "lng": 6.96}`.
- `max_extra_minutes`: 0–120. 0 still applies "free" detours (highway jammed →
  country road no slower).

Response `200`:

```json
{
  "budget_s": 900,
  "fastest": {
    "encoded_polyline": "...",
    "duration_s": 7200,
    "static_duration_s": 6900,
    "distance_m": 190000,
    "highway_distance_m": 151000,
    "highway_duration_s": 5030
  },
  "ride": {
    "duration_s": 8050,
    "extra_duration_s": 850,
    "distance_m": 214000,
    "highway_distance_m": 42000,
    "highway_duration_s": 1500,
    "gmaps_url": "https://www.google.com/maps/dir/?api=1&origin=...&waypoints=...",
    "segments": [
      { "kind": "kept",    "encoded_polyline": "...", "duration_s": 640,  "distance_m": 9000 },
      { "kind": "detour",  "encoded_polyline": "...", "duration_s": 3100, "distance_m": 61000 },
      { "kind": "highway", "encoded_polyline": "...", "duration_s": 1500, "distance_m": 42000 }
    ]
  },
  "detours": [
    {
      "entry": { "lat": 50.81, "lng": 7.15 },
      "exit":  { "lat": 50.55, "lng": 7.61 },
      "extra_duration_s": 850,
      "avoided_highway_s": 2400,
      "avoided_highway_m": 68000,
      "detour_distance_m": 61000,
      "curviness": 1.42
    }
  ]
}
```

`ride.segments[].kind`:
- `kept` — non-highway part of the fastest route, unchanged
- `highway` — highway part of the fastest route we could not afford to replace
- `detour` — replacement country-road segment

Segments are ordered origin → destination and their polylines concatenate into the
full ride. The frontend colors them: `highway` = signage blue, `detour`/`kept` =
signage yellow; the fastest route is drawn underneath as a neutral line.

`ride.gmaps_url` deep-links the ride into the Google Maps app: each detour is pinned by
entry, midpoint, and exit waypoints (9 waypoints max — midpoints of the least valuable
detours are dropped first, then whole detours).

Errors (`400` input / geocoding, `429` rate limited, `502` upstream):

```json
{ "detail": { "code": "GEOCODE_FAILED", "message": "Could not resolve origin." } }
```

Codes: `INVALID_INPUT`, `GEOCODE_FAILED`, `NO_ROUTE`, `UPSTREAM`, `RATE_LIMITED`,
`DAILY_CAP`. The two 429 codes carry rider-ready messages (per-IP hourly window and a
global daily plan cap protecting the Google quota).

## `POST /api/scout`

The compose flow: evaluates ALL viable highway cuts (no budget, no knapsack) so the
client lets the rider toggle them. Request = `origin`/`destination` only.

Response `200`:

```json
{
  "origin": { "lat": 50.937, "lng": 6.960 },
  "destination": { "lat": 50.110, "lng": 8.682 },
  "fastest": { "...": "same shape as /api/plan fastest" },
  "skeleton": [
    { "kind": "kept",    "encoded_polyline": "...", "duration_s": 640, "distance_m": 9000, "cut_id": null },
    { "kind": "highway", "encoded_polyline": "...", "duration_s": 780, "distance_m": 23000, "cut_id": "c0" }
  ],
  "cuts": [
    {
      "id": "c0",
      "road": "A3",
      "entry": { "lat": 50.81, "lng": 7.15 },
      "mid":   { "lat": 50.70, "lng": 7.40 },
      "exit":  { "lat": 50.55, "lng": 7.61 },
      "encoded_polyline": "...",
      "detour_duration_s": 1860,
      "detour_distance_m": 30000,
      "extra_duration_s": 1080,
      "avoided_highway_s": 660,
      "avoided_highway_m": 17000,
      "curviness": 1.319
    }
  ]
}
```

Composition is client-side and additive: the ride = skeleton with each selected cut's
part swapped for the cut; `ride_duration = fastest.duration_s + Σ selected
extra_duration_s`. `extra_duration_s <= 0` means the highway is jammed — the cut is
free. Skeleton polylines concatenate into the fastest route; every `cut_id` matches
exactly one skeleton part (always `kind: "highway"`). Same error envelope and rate
limits as `/api/plan`.

## `POST /api/scout/stream`

Same request/semantics as `/api/scout`, but progress streams as NDJSON (one JSON per
line) so clients can build the map live:

```
{"type":"route", "origin":…, "destination":…, "fastest":…, "preview":[SkeletonPart…]}   // preview = skeleton, no cut_ids yet
{"type":"corridors", "count":39, "corridors":[{"entry":…, "exit":…}…]}
{"type":"scored", "corridors":[{"entry":…, "exit":…, "curvy_km":8.4}…]}                 // per OSM batch; absent when OSM is off
{"type":"probing", "count":12}
{"type":"cut", "cut":CutOut}                                                            // per gated probe, id empty, order nondeterministic
{"type":"done", "scout":ScoutResponse}                                                  // authoritative final payload
{"type":"error", "code":…, "message":…, "status":…}                                     // terminal instead of done
```

Cached scouts replay as a single `done` event. Rate limiting and validation errors are
returned as normal HTTP errors before the stream starts.

## `GET /api/health`

```json
{ "ok": true, "mock": false }
```

## Configuration (backend `.env`)

| var                   | default | meaning                                  |
| --------------------- | ------- | ---------------------------------------- |
| `GOOGLE_MAPS_API_KEY` | —       | server key, Routes API enabled           |
| `IHH_MOCK`            | `0`     | `1` = serve canned fixtures, no Google   |
| `IHH_CORS_ORIGINS`    | `http://localhost:5173` | comma-separated       |
