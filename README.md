# ihatehighways

Route planning for motorcyclists who'd rather ride than transit. Every navigator offers a
binary "avoid highways" toggle that turns a 3-hour trip into a 6-hour one. ihatehighways
answers a different question:

> **"I'll trade up to 15 extra minutes — get me off the highway as much as that buys."**

It computes the fastest route with **live Google traffic**, finds the highway stretches that
are cheapest to replace with country roads (a jammed Autobahn stretch is often *free* to
replace), and spends your time budget where it buys the most riding. No competitor does this:
calimoto, Kurviger, Scenic, and Rever have no live-traffic routing at all; MyRoute-app has
HERE traffic but only binary avoidance. Details in [docs/algorithm.md](docs/algorithm.md).

## Architecture

```
backend/    FastAPI + Google Routes API v2 — the planner lives here (POST /api/plan)
frontend/   Vite + React + @vis.gl/react-google-maps — map UI
docs/       algorithm.md (the optimizer), api.md (frozen API contract, shared with the future iOS app)
```

The backend API is deliberately client-agnostic: the planned iOS app consumes the same
`/api/plan` contract.

## Setup

You need two Google Maps Platform API keys (one project, both billable within generous free
tiers):

1. **Server key** (backend): enable **Routes API**. Restrict by IP in production.
   → `backend/.env`: `GOOGLE_MAPS_API_KEY=...`
2. **Browser key** (frontend): enable **Maps JavaScript API** + **Places API (New)**.
   Restrict by HTTP referrer. → `frontend/.env.local`: `VITE_GOOGLE_MAPS_API_KEY=...`

Copy `backend/.env.example` and `frontend/.env.example` to get started.

## Run

```sh
# backend (uv)
cd backend && uv run uvicorn app.main:app --reload --port 8000

# frontend (node via nix; dev server proxies /api → :8000)
cd frontend && nix-shell -p nodejs_22 --run 'npm run dev'
```

No Google key yet? Run the backend with `IHH_MOCK=1` to serve a deterministic synthetic
route (Cologne → Frankfurt shaped) and exercise the whole stack offline:

```sh
cd backend && IHH_MOCK=1 uv run uvicorn app.main:app --port 8000
```

Tests: `cd backend && uv run pytest`.

## Deploy

- **Backend** — Render free tier via [`render.yaml`](render.yaml) (Docker, spins down when
  idle; set `GOOGLE_MAPS_API_KEY` in the dashboard). Per-IP and daily rate limits guard
  the Google quota.
- **Frontend** — Cloudflare Worker in [`worker/`](worker/) serves the built frontend and
  proxies `/api` same-origin to the backend:
  `cd frontend && nix-shell -p nodejs_22 --run 'npm run build'`, then
  `cd worker && npx wrangler deploy`. The custom domain is configured in
  `worker/wrangler.jsonc`.

Corridor pre-filtering uses OpenStreetMap data via Overpass
(© OpenStreetMap contributors, ODbL) to decide which Google queries are worth making.

## Costs & terms (read before scaling)

- One plan ≈ ≤ 14 Routes API calls at the **Pro** SKU ($10/1k, 5k free/month ⇒ ~350 free
  plans/month). Traffic-aware routing is what triggers Pro — it is also the entire point.
- Google ToS forbid persisting route responses (polylines/durations). Only place IDs may be
  stored indefinitely — future saved-routes must store waypoints + params and recompute.
- Routes must be displayed on a Google map (non-EEA terms). GPX export of Google-computed
  routes is ToS-gray at best — treat as out of scope.
- No `TWO_WHEELER` travel mode: Enterprise SKU, no benefit here. We route as `DRIVE`.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) — use, modify, and share freely for any
noncommercial purpose. Commercial use requires a separate license from the author.

## Roadmap

- Curviness preference: request alternatives on detour queries, prefer sinuous ones within
  a small time tolerance.
- Saved routes (place-ID based), rider profiles (budget presets).
- iOS app (SwiftUI + Maps SDK) on the same backend.
- Weather overlay for planning around rain cells.
