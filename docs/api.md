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

Errors (`400` input / geocoding, `502` upstream):

```json
{ "detail": { "code": "GEOCODE_FAILED", "message": "Could not resolve origin." } }
```

Codes: `INVALID_INPUT`, `GEOCODE_FAILED`, `NO_ROUTE`, `UPSTREAM`.

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
