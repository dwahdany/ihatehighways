# ihatehighways — minimal-highway routing algorithm

## Goal

Given origin **A**, destination **B**, and a rider time budget **Δ** ("I'll trade up to Δ extra
minutes"), return a route that **minimizes time spent on highways** subject to
`total_duration ≤ fastest_duration + Δ`, using **live traffic** (Google Routes API v2,
`TRAFFIC_AWARE`). The key product insight: full highway avoidance (what every navigator offers)
is useless on long trips; the win is *surgically* replacing the highway stretches that cost the
least time to replace — and traffic jams make some replacements *free*.

## Pipeline

1. **Base route** — `computeRoutes(A→B)` with `routingPreference: TRAFFIC_AWARE`, field mask
   covering `routes.duration,staticDuration,distanceMeters,polyline` and
   `routes.legs.{duration,staticDuration,steps.{distanceMeters,staticDuration,polyline,navigationInstruction}}`.
   Single leg (no intermediates).

2. **Atomization** — Google returns long motorway hauls as *single steps* (observed: one 167 km
   "Continue on A3" step for Cologne→Frankfurt). Steps much longer than `STEP_ATOM_M` (3 km)
   are split into equal sub-steps along their polylines (interpolated cut points, distance and
   static duration distributed equally) so chunk boundaries can fall anywhere on a haul.

   **Step classification** — Routes API exposes no road-class field (verified against the 2026
   `RouteLegStep` reference: road names only appear in free-text
   `navigationInstruction.instructions`), so classify each step as highway iff:
   - avg static speed `distance/staticDuration ≥ 90 km/h` and `distance ≥ 500 m`, **or**
   - avg speed ≥ 72 km/h **and** the instruction text matches the motorway regex
     (`\b(A ?\d{1,3}|E ?\d{1,3}|M\d{1,2}|I-\d{1,3}|Autobahn|motorway|freeway|interstate|expressway|autoroute|autostrada|autopista)\b`, case-insensitive).
   - **Maneuver hint:** steps with maneuver `MERGE`/`RAMP_LEFT`/`RAMP_RIGHT` count as highway when
     the following step classifies as highway (ramps are slow but belong to the stretch).
   - **Gap bridging:** non-highway runs shorter than 2 km sandwiched between highway steps are
     absorbed into the highway stretch (interchanges, service areas).

3. **Stretches → chunks** — contiguous highway runs ≥ `MIN_STRETCH_KM` (12) become detour
   candidates. Each stretch is split at step boundaries into chunks, adaptively enlarged so
   total chunks ≤ `MAX_CHUNKS` (10). Chunks are the unit of optimization: on a 300 km Autobahn
   haul you rarely want to replace all of it — you want the best 40 km.
   **Chunk size adapts to the budget:** replacing one highway km costs roughly
   `DETOUR_EXTRA_PER_HW_KM_S` (30 s) extra, so chunks are sized near
   `budget / 30 s-per-km`, clamped to [`MIN_CHUNK_KM` (15), `MAX_CHUNK_KM` (45)]. Without
   this, a 15-minute budget can't afford any 45 km chunk on a long haul (~+25 min each) and
   the optimizer degenerates to junk micro-detours near the endpoints (observed on real
   Cologne→Frankfurt data).

3b. **OSM corridor pre-filter** — before spending paid Google calls, each chunk's corridor
   is scored with free OSM data (Overpass, per-chunk bbox query): "excess curvature km"
   `Σ way_length × (sinuosity − 1.05)⁺` over secondary/tertiary roads. City grids score
   ≈ 0, real twisty country (e.g. the Westerwald along the A3: ~19) scores high; chunks
   under `OSM_MIN_CURVY_KM` (2.0) are skipped — no fun roads nearby, no probe. Scores are
   disk-cached 30 days (ODbL allows it; attribution: © OpenStreetMap contributors) and
   failures fail OPEN under a hard latency deadline, so Overpass being down costs
   filtering, never the plan. Ops constraints that shaped this: `around:` polyline
   filters time out server-side (bbox is cheap), and public instances allow ~2
   concurrent slots per IP (2 lanes + mirror failover).

4. **Detour query per chunk** (parallel) — `computeRoutes(entry→exit, avoidHighways: true,
   TRAFFIC_AWARE)` where entry/exit are chunk boundary lat/lngs. Set origin `heading` to the base
   polyline's bearing at entry so routing can't start backwards. `avoidHighways` is *soft*
   avoidance, so mid-motorway endpoints resolve naturally to "take the next exit".
   Per chunk compute:
   - `baseline = Σ step.staticDuration × traffic_factor`, with
     `traffic_factor = leg.duration / leg.staticDuration` from the base route
     (steps only carry static durations; this scales them to live traffic).
   - `extra_cost = detour.duration − baseline` (**can be ≤ 0** when the highway is jammed).
   - `value = baseline_highway_time − highway_time_within_detour` (detour steps are classified
     too; soft avoidance can leave residual highway).
   - `curviness = detour_polyline_length / straight_line(entry, exit)` (sinuosity, for the UI).
   - **Escape gate:** a candidate must shed ≥ `MIN_AVOIDED_FRACTION` (0.5) of its chunk's
     highway time. Soft `avoidHighways` between two mid-motorway points often returns the
     motorway itself (observed on real A3 data) — and leg-average traffic scaling can even
     make such non-escapes look *free*. This gate also applies to merged-span requeries.
   - **Worth gate:** each probe requests up to 3 route alternatives (same billable call) and
     keeps the alternative maximizing `worth = value × (1 + CURVY_BOOST (2.0) ×
     (min(curviness, CURVINESS_CAP 1.7) − 1)) / extra_cost` — curviness is valued against time
     loss, so a 1.5×-curvy sweep justifies roughly 3× the time cost of an arrow-straight swap.
     Candidates below `MIN_DETOUR_WORTH` (0.6) are discarded; free (jam) detours have infinite
     worth but are never exempt from the escape gate. The same worth formula drives the
     frontend's "Good deals" preset (≥ 1.0) and the cut-list ranking.

   **Known estimation limit:** chunk baselines scale step static durations by the *leg-wide*
   traffic factor, so per-chunk costs carry ±jam-distribution error. Fixing this properly
   means one extra fastest-route query per chunk (2× API cost) — deferred until it matters.

5. **Selection — 0/1 knapsack** — chunks with `extra_cost ≤ 0` are auto-selected (free wins).
   Remaining chunks: maximize `Σ value` s.t. `Σ extra_cost ≤ Δ`, DP over 15-second buckets
   (≤ 10 items, trivial).

6. **Merge & requery** — adjacent selected chunks in the same stretch merge into one span,
   re-queried once with `avoidHighways: true` so the final route doesn't bounce back onto the
   motorway at internal chunk boundaries. If the merged detour would blow the remaining budget,
   fall back to the unmerged chunk detours.

7. **Stitch** — final ride = base steps outside selected spans + detour routes inside.
   Polylines: decode → concatenate → re-encode (Google encoded polyline, precision 5).
   `ride.duration = base.duration − Σ baselines + Σ detour.durations`. Highway stats are
   recomputed from the stitched step sequence, never assumed.

8. **Degenerate cases** — no highway on base route → ride = fastest. Budget too small for any
   paid detour → fastest + free detours only. Detour query failure → skip that chunk, log, continue.

## Cost envelope

Per plan: 1 base call + ≤ 10 chunk calls + ≤ ~3 merge requeries ≈ **≤ 14 Routes API calls**.
`TRAFFIC_AWARE` (and the `heading` location modifier) bill as **Routes: Compute Routes Pro** —
$10/1000 calls, 5,000 free calls/month (≈ 350 free plans/month). We deliberately use
`travelMode: DRIVE`, not `TWO_WHEELER`: two-wheeler routing is an Enterprise SKU ($15/1000,
1,000 free/month) and adds nothing for this use case.

**ToS constraint:** Google forbids caching Routes API responses beyond transient use (lat/lng
≤ 30 days; durations/ETAs not cacheable at all). We keep only an in-process TTL cache
(≤ 5 min) and never persist computed routes. Future "saved routes" must store place IDs
(cacheable indefinitely) + request parameters and recompute on load.

## Why not X?

- **`avoidHighways` alone** — binary; adds hours on long trips. Our budget model is the product.
- **Custom OSM engine (Valhalla/GraphHopper custom models)** — real weighted road-class costs,
  but no Google live traffic/closures, and mixing Google traffic into non-Google routing violates
  Google ToS. Live traffic *is* the differentiator, so we build on Routes API.
- **Roads/step metadata for classification** — no Google API exposes road class today; the
  speed + name heuristic is measurable and tunable (thresholds in config).

## Roadmap hooks

- Curviness-aware detour choice: request `computeAlternativeRoutes` on detour queries, prefer the
  most sinuous alternative within ~10% of the best detour time.
- Rain/wind layers, surface-quality crowd data, multi-day touring, GPX export, iOS app on the
  same `/api/plan` contract.
