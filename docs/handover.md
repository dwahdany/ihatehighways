# Handover — state of ihatehighways (2026-07-07)

One page to pick this project back up cold. Product docs live elsewhere: what it is and
costs in [README](../README.md), the routing pipeline in [algorithm.md](algorithm.md),
the wire contract in [api.md](api.md), the iOS app in [ios/README.md](../ios/README.md).

## What exists and works

- **Web app** (`frontend/` + `backend/`) — live at <https://ihatehighways.wahdany.eu>.
  Scout & compose model: the backend streams priced highway "cuts" (`/api/scout/stream`),
  the rider toggles them client-side, hands off to Google Maps with pinned waypoints.
  `/api/plan` is the older knapsack single-shot variant.
- **Junction-aligned chunking** (`backend/app/junctions.py`) — cut boundaries snap to
  motorway exits (OSM `motorway_junction` nodes), killing the ride-past-the-exit-and-
  double-back bug. Live-verified: a Cologne→Frankfurt cut's entry landed 254 m upstream
  of A3 exit 29 Königsforst (`probe_entry_back_m` = 250). The action space is
  "leave at exit *i*, rejoin within the next ~10 exits" (`scout_max_span_chunks`).
- **iOS app** (`ios/`) — milestone 1: SwiftUI planner on the same scout contract plus
  Navigation SDK turn-by-turn via `/api/ride-token` route tokens. Simulator-verified
  only; never run on a real device.
- **Hosting** — everything on Cloudflare (paid Workers). The Worker in `worker/` serves
  the built frontend and runs the FastAPI backend as a Cloudflare Container
  (`backend/Dockerfile`, singleton via `getByName("api")`, `basic` instance, sleeps
  after 30 min, cold start ~2–4 s). Render is fully retired.

## Deploying

```sh
cd frontend && nix-shell -p nodejs_22 --run 'npm run build'
cd ../worker && npm install && npx wrangler deploy   # Docker must be running
```

- ⚠️ **Deploy from `worker/` only.** Running `wrangler deploy` from the repo root once
  picked up a stray auto-scaffolded root config and replaced the worker with an
  assets-only one — which also **deleted all Worker secrets**. Recovery: redeploy from
  `worker/`, re-run `npx wrangler secret put GOOGLE_MAPS_API_KEY`, then delete the
  container app (`npx wrangler containers list` → `delete <id>`) and redeploy so the
  instance restarts with the key (there is no instance-restart command; envVars are
  injected at container start).
- **After every deploy** check `https://ihatehighways.wahdany.eu/api/health` →
  `{"ok": true, "mock": false, "key_configured": true}`. `key_configured` exists
  precisely because the secret-wipe failure mode is silent otherwise.
- Backend env vars live in the `Backend` class in `worker/src/index.ts`; the only
  secret is `GOOGLE_MAPS_API_KEY` (also in `backend/.env` for local dev).
- Container disk is ephemeral: the 30-day OSM caches (`backend/.cache/`) reset on
  sleep. Warm-start loss only; move to KV if it ever matters (ODbL permits it).

## Constraints that shape everything (do not "optimize" these away)

- **Google ToS**: route responses must never be persisted (in-memory TTL cache ≤ 300 s
  only); routes must be displayed on a Google map; place IDs are the only thing you may
  store indefinitely — future saved-routes = place IDs + params, recompute on load.
- **Cost**: one scout ≈ ≤ 14 Routes calls at the Pro SKU ($10/1k, 5k free/month).
  Traffic-aware routing is what triggers Pro and is also the entire point. Per-IP and
  daily rate limits (`ratelimit.py`) guard the wallet; they assume a single backend
  instance — keep the container a singleton.
- **Overpass**: free, ~2 concurrent slots per IP, mirrors with 429 cooldowns, everything
  behind deadlines that fail open. OSM data only gates which Google calls to make; it is
  never mixed into displayed content.

## Known sharp edges

- Junction chunking: very narrow medians can let an opposite-direction-only exit
  through the 20 m snap gate (phantom boundary); Autobahnkreuz fork nodes count as
  exits and may waste a probe. Both documented in `junctions.py`'s docstring.
- Adjacent junction cuts overlap by design (probe-exit overshoot) and cannot coexist in
  one ride — the scout keeps one-chunk separators, `/api/plan`'s merge fallback keeps a
  non-overlapping subset. Don't remove those guards.
- `MockRoutesClient` (IHH_MOCK=1) cannot reproduce routing pathologies like the
  double-back — its detours are synthetic arcs. Real-behavior claims need a live key.
- Cloudflare Containers is beta: no SLA, rolling (not instant) deploys, and
  `wrangler containers delete` + redeploy is the only way to force-restart an instance.

## Where to go next (in rough priority order)

1. **iOS milestone 2** — run on a real device (needs `ios/Secrets.xcconfig` +
   Navigation SDK enabled on the key), current location as origin, TestFlight.
2. **Saved routes / rider profiles** — place-ID based per the ToS note above.
3. **Weather overlay** — plan around rain cells.
4. Nice-to-have: OSM caches to KV so container sleeps don't cost cache warmth.

Algorithm tunables all live in `backend/app/config.py` with calibration notes inline.
Tests: `cd backend && uv run pytest` (81 passing). The `docs/algorithm.md` "Why not X?"
section answers most "couldn't we just…" ideas — read it before re-architecting.
