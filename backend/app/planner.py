"""The minimal-highway planning pipeline (docs/algorithm.md).

base route -> classify -> stretches/chunks -> parallel detour queries -> knapsack
selection -> merge & requery -> stitch.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Protocol, Sequence

Emit = Callable[[dict], None]

from . import classify, gmaps, knapsack, osm, polyline_util
from .classify import Chunk
from .config import Settings
from .google_routes import GRoute, GStep, NoRouteError, UpstreamError, WaypointSpec
from .models import (
    CutOut,
    DetourOut,
    LatLng,
    PlacePoint,
    PlanRequest,
    PlanResponse,
    Ride,
    RouteSummary,
    ScoutRequest,
    ScoutResponse,
    Segment,
    SkeletonPart,
)
from .polyline_util import Point

logger = logging.getLogger("ihatehighways.planner")

MAX_PARALLEL_DETOUR_QUERIES = 6
MAX_DETOUR_DISTANCE_FACTOR = 3.0  # skip detours longer than 3x the chunk distance
MAX_DETOUR_DURATION_FACTOR = 4.0  # skip detours slower than 4x the chunk baseline


class RoutesClient(Protocol):
    async def compute_route(
        self,
        origin: WaypointSpec,
        destination: WaypointSpec,
        avoid_highways: bool = False,
        origin_heading: int | None = None,
    ) -> GRoute: ...


class PlanError(Exception):
    """Maps to the documented error envelope: {"detail": {"code", "message"}}."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class TTLCache:
    """In-process TTL cache keyed by canonical request JSON.

    NOTE: Google's ToS forbids persisting Routes API responses (durations/ETAs are not
    cacheable at all; lat/lng at most 30 days). This cache must stay in-memory and its
    TTL must stay <= 300 seconds. Never write computed routes to disk or a database.
    """

    def __init__(self, ttl_s: float, max_entries: int = 256):
        self._ttl_s = min(float(ttl_s), 300.0)
        self._max_entries = max_entries
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> object | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl_s:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: object) -> None:
        now = time.monotonic()
        if len(self._store) >= self._max_entries:
            expired = [k for k, (ts, _) in self._store.items() if now - ts > self._ttl_s]
            for k in expired:
                del self._store[k]
            while len(self._store) >= self._max_entries:
                self._store.pop(next(iter(self._store)))
        self._store[key] = (now, value)


@dataclass(frozen=True)
class _Candidate:
    chunk: Chunk
    route: GRoute
    extra_cost_s: float  # detour duration - baseline (can be <= 0 when jammed)
    value_s: float  # baseline highway time - highway time within the detour
    hw_in_detour_s: float
    hw_in_detour_m: float


@dataclass(frozen=True)
class _Span:
    """A final (possibly merged) detour replacing base steps [step_start, step_end)."""

    step_start: int
    step_end: int
    entry: Point
    exit: Point
    baseline_s: float
    baseline_hw_m: float
    route: GRoute
    hw_in_detour_s: float
    hw_in_detour_m: float


def _spec(p: PlacePoint) -> WaypointSpec:
    if p.place_id is not None:
        return WaypointSpec(place_id=p.place_id)
    if p.address is not None:
        return WaypointSpec(address=p.address)
    assert p.lat_lng is not None
    return WaypointSpec(lat_lng=(p.lat_lng.lat, p.lat_lng.lng))


def _flatten(route: GRoute) -> tuple[list[GStep], list[float]]:
    """All steps in order, each paired with its leg's traffic factor."""
    steps: list[GStep] = []
    factors: list[float] = []
    for leg in route.legs:
        factor = (
            leg.duration_s / leg.static_duration_s if leg.static_duration_s > 0 else 1.0
        )
        if factor <= 0:
            factor = 1.0
        for step in leg.steps:
            steps.append(step)
            factors.append(factor)
    return steps, factors


def _highway_stats(
    steps: Sequence[GStep], factors: Sequence[float], flags: Sequence[bool]
) -> tuple[float, float]:
    """(traffic-scaled highway seconds, highway meters) for a classified step sequence."""
    hw_s = sum(s.static_duration_s * f for s, f, fl in zip(steps, factors, flags) if fl)
    hw_m = sum(s.distance_m for s, fl in zip(steps, flags) if fl)
    return hw_s, hw_m


def _route_highway_stats(route: GRoute, settings: Settings) -> tuple[float, float]:
    steps, factors = _flatten(route)
    flags = classify.classify_steps(steps, settings)
    return _highway_stats(steps, factors, flags)


def _curviness(route: GRoute, entry: Point, exit: Point) -> float:
    pts = polyline_util.decode(route.encoded_polyline) if route.encoded_polyline else []
    straight = polyline_util.haversine_m(entry, exit)
    if straight <= 0 or len(pts) < 2:
        return 1.0
    return max(1.0, polyline_util.path_length_m(pts) / straight)


async def _query_detours(
    chunks: Sequence[Chunk],
    client: RoutesClient,
    settings: Settings,
    on_candidate: Callable[[_Candidate], None] | None = None,
    on_probe: Callable[[GRoute, bool], None] | None = None,
) -> list[_Candidate]:
    semaphore = asyncio.Semaphore(MAX_PARALLEL_DETOUR_QUERIES)

    def evaluate(chunk: Chunk, route: GRoute) -> _Candidate | None:
        if route.distance_m > MAX_DETOUR_DISTANCE_FACTOR * chunk.distance_m:
            logger.info("skipping detour: distance %.0f m > 3x chunk", route.distance_m)
            return None
        if route.duration_s > MAX_DETOUR_DURATION_FACTOR * chunk.baseline_s:
            logger.info("skipping detour: duration %.0f s > 4x baseline", route.duration_s)
            return None
        hw_s, hw_m = _route_highway_stats(route, settings)
        extra_cost_s = route.duration_s - chunk.baseline_s
        # Chunk steps are all highway (bridged gaps included), so the chunk's baseline
        # highway time is its full baseline.
        value_s = chunk.baseline_s - hw_s
        # Escape gate: avoidHighways is soft — between two mid-motorway points it often
        # just stays on the motorway (observed on real A3 data, where leg-average
        # traffic scaling even made such non-escapes look "free"). A real detour sheds
        # most of the chunk's highway time.
        if value_s < settings.min_avoided_fraction * chunk.baseline_s:
            logger.info(
                "skipping detour: only avoids %.0f s of the chunk's %.0f s highway time",
                value_s,
                chunk.baseline_s,
            )
            return None
        # Efficiency gate: a paid detour must shed highway time worth a reasonable
        # fraction of its cost, or it is a junk trade (e.g. crawling through city
        # streets to dodge an urban motorway).
        if extra_cost_s > 0 and value_s < settings.min_detour_efficiency * extra_cost_s:
            logger.info(
                "skipping detour: pays %.0f s to avoid only %.0f s of highway",
                extra_cost_s,
                value_s,
            )
            return None
        return _Candidate(
            chunk=chunk,
            route=route,
            extra_cost_s=extra_cost_s,
            value_s=value_s,
            hw_in_detour_s=hw_s,
            hw_in_detour_m=hw_m,
        )

    async def one(chunk: Chunk) -> _Candidate | None:
        async with semaphore:
            try:
                route = await client.compute_route(
                    WaypointSpec(lat_lng=chunk.entry),
                    WaypointSpec(lat_lng=chunk.exit),
                    avoid_highways=True,
                    origin_heading=chunk.entry_heading,
                )
            except Exception as exc:  # query failure -> skip chunk, log, continue
                logger.warning(
                    "detour query failed for steps [%d,%d): %s",
                    chunk.step_start,
                    chunk.step_end,
                    exc,
                )
                return None
        candidate = evaluate(chunk, route)
        if on_probe is not None:
            on_probe(route, candidate is not None)
        if candidate is not None and on_candidate is not None:
            on_candidate(candidate)
        return candidate

    results = await asyncio.gather(*(one(c) for c in chunks))
    return [c for c in results if c is not None]


def _span_from_candidates(run: Sequence[_Candidate], route: GRoute, settings: Settings) -> _Span:
    hw_s, hw_m = _route_highway_stats(route, settings)
    return _Span(
        step_start=run[0].chunk.step_start,
        step_end=run[-1].chunk.step_end,
        entry=run[0].chunk.entry,
        exit=run[-1].chunk.exit,
        baseline_s=sum(c.chunk.baseline_s for c in run),
        baseline_hw_m=sum(c.chunk.distance_m for c in run),
        route=route,
        hw_in_detour_s=hw_s,
        hw_in_detour_m=hw_m,
    )


async def _merge_spans(
    selected: Sequence[_Candidate],
    budget_s: float,
    client: RoutesClient,
    settings: Settings,
) -> list[_Span]:
    """Merge adjacent selected chunks per stretch and requery each merged span once.

    Falls back to the unmerged chunk detours when the merged requery fails, looks
    unreasonable, or would blow the remaining budget.
    """
    total_extra = sum(c.extra_cost_s for c in selected)
    runs: list[list[_Candidate]] = []
    for cand in sorted(selected, key=lambda c: c.chunk.step_start):
        if (
            runs
            and runs[-1][-1].chunk.stretch_id == cand.chunk.stretch_id
            and runs[-1][-1].chunk.step_end == cand.chunk.step_start
        ):
            runs[-1].append(cand)
        else:
            runs.append([cand])

    spans: list[_Span] = []
    for run in runs:
        if len(run) == 1:
            cand = run[0]
            spans.append(_span_from_candidates(run, cand.route, settings))
            continue
        entry = run[0].chunk.entry
        exit_ = run[-1].chunk.exit
        baseline = sum(c.chunk.baseline_s for c in run)
        distance = sum(c.chunk.distance_m for c in run)
        run_extra = sum(c.extra_cost_s for c in run)
        merged: GRoute | None = None
        try:
            merged = await client.compute_route(
                WaypointSpec(lat_lng=entry),
                WaypointSpec(lat_lng=exit_),
                avoid_highways=True,
                origin_heading=run[0].chunk.entry_heading,
            )
        except Exception as exc:
            logger.warning("merged-span requery failed, keeping unmerged detours: %s", exc)
        accepted = False
        if (
            merged is not None
            and merged.distance_m <= MAX_DETOUR_DISTANCE_FACTOR * distance
            and merged.duration_s <= MAX_DETOUR_DURATION_FACTOR * baseline
        ):
            merged_hw_s, _ = _route_highway_stats(merged, settings)
            merged_extra = merged.duration_s - baseline
            new_total = total_extra - run_extra + merged_extra
            # Same escape gate as for single chunks: the merged requery must actually
            # leave the motorway, or we keep the individually-vetted detours.
            escapes = baseline - merged_hw_s >= settings.min_avoided_fraction * baseline
            if escapes and new_total <= budget_s:
                spans.append(_span_from_candidates(run, merged, settings))
                total_extra = new_total
                accepted = True
        if not accepted:
            spans.extend(_span_from_candidates([c], c.route, settings) for c in run)
    spans.sort(key=lambda s: s.step_start)
    return spans


def _dedupe(points: list[Point]) -> list[Point]:
    out: list[Point] = []
    for p in points:
        if not out or out[-1] != p:
            out.append(p)
    return out


def _build_segments(
    steps: Sequence[GStep],
    factors: Sequence[float],
    flags: Sequence[bool],
    spans: Sequence[_Span],
) -> list[Segment]:
    span_by_start = {s.step_start: s for s in spans}
    # (kind, points, duration_s, distance_m) per part, then merge contiguous same-kind.
    parts: list[list] = []
    i = 0
    while i < len(steps):
        span = span_by_start.get(i)
        if span is not None:
            pts = polyline_util.decode(span.route.encoded_polyline)
            parts.append(["detour", pts, span.route.duration_s, float(span.route.distance_m)])
            i = span.step_end
            continue
        kind = "highway" if flags[i] else "kept"
        pts = polyline_util.decode(steps[i].encoded_polyline) if steps[i].encoded_polyline else [
            steps[i].start,
            steps[i].end,
        ]
        parts.append([kind, pts, steps[i].static_duration_s * factors[i], float(steps[i].distance_m)])
        i += 1

    merged: list[list] = []
    for part in parts:
        if merged and merged[-1][0] == part[0]:
            merged[-1][1].extend(part[1])
            merged[-1][2] += part[2]
            merged[-1][3] += part[3]
        else:
            merged.append([part[0], list(part[1]), part[2], part[3]])

    return [
        Segment(
            kind=kind,
            encoded_polyline=polyline_util.encode(_dedupe(pts)),
            duration_s=round(duration),
            distance_m=round(distance),
        )
        for kind, pts, duration, distance in merged
    ]


async def _fetch_base(
    origin: PlacePoint, destination: PlacePoint, client: RoutesClient
) -> GRoute:
    try:
        return await client.compute_route(_spec(origin), _spec(destination), avoid_highways=False)
    except NoRouteError:
        raise PlanError("NO_ROUTE", "No route found between origin and destination.", 400)
    except UpstreamError as exc:
        if exc.status == 400:
            raise PlanError(
                "GEOCODE_FAILED",
                f"Could not resolve origin or destination: {exc.message}",
                400,
            )
        raise PlanError("UPSTREAM", exc.message, 502)


def _fastest_summary(
    base: GRoute, steps: Sequence[GStep], factors: Sequence[float], flags: Sequence[bool]
) -> RouteSummary:
    base_hw_s, base_hw_m = _highway_stats(steps, factors, flags)
    return RouteSummary(
        encoded_polyline=base.encoded_polyline,
        duration_s=round(base.duration_s),
        static_duration_s=round(base.static_duration_s),
        distance_m=round(base.distance_m),
        highway_distance_m=round(base_hw_m),
        highway_duration_s=round(base_hw_s),
    )


async def _osm_filter(chunks: list[Chunk], settings: Settings) -> list[Chunk]:
    """Drop chunks whose corridors have no fun roads (free OSM data, fails open)."""
    if not chunks or not settings.osm_enabled or settings.ihh_mock:
        return chunks
    # /api/plan is interactive without a loader UI: flat ~12 s cap, unlike scout's
    # batch-scaled deadline.
    flat_deadline = settings.osm_deadline_s + settings.osm_deadline_per_batch_s
    scores = await osm.score_chunks(
        [(c.entry, c.exit) for c in chunks], settings, deadline_s=flat_deadline
    )
    kept: list[Chunk] = []
    for chunk, score in zip(chunks, scores):
        if score is not None and score < settings.osm_min_curvy_km:
            logger.info(
                "skipping chunk [%d,%d): corridor curvy score %.1f km < %.1f km",
                chunk.step_start,
                chunk.step_end,
                score,
                settings.osm_min_curvy_km,
            )
            continue
        kept.append(chunk)
    return kept


async def plan(req: PlanRequest, client: RoutesClient, settings: Settings) -> PlanResponse:
    budget_s = req.max_extra_minutes * 60

    # 1. Base route (traffic-aware fastest).
    base = await _fetch_base(req.origin, req.destination, client)

    # 2. Atomize long steps, then classification and fastest-route stats.
    steps, factors = _flatten(base)
    steps, factors = classify.atomize_steps(steps, factors, settings.step_atom_m)
    flags = classify.classify_steps(steps, settings)
    fastest = _fastest_summary(base, steps, factors, flags)

    # 3-4. Stretches -> chunks (budget-adaptively sized) -> OSM pre-filter -> parallel
    # detour queries (each probe is a paid Google call; OSM gating is free).
    chunks = classify.build_chunks(steps, flags, factors, settings, budget_s=budget_s)
    chunks = await _osm_filter(chunks, settings)
    candidates = await _query_detours(chunks, client, settings)

    # 5. Selection: free detours always, then 0/1 knapsack within the budget.
    items = [
        knapsack.Item(key=idx, cost_s=c.extra_cost_s, value_s=c.value_s)
        for idx, c in enumerate(candidates)
        if c.value_s > 0  # a detour that avoids no highway time is pointless
    ]
    selected_keys = knapsack.select(items, budget_s, settings.knapsack_bucket_s)
    selected = [candidates[k] for k in sorted(selected_keys)]

    # 6. Merge adjacent selected chunks and requery each merged span once.
    spans = await _merge_spans(selected, budget_s, client, settings)

    # 7. Stitch.
    segments = _build_segments(steps, factors, flags, spans)
    ride_duration = (
        base.duration_s
        - sum(s.baseline_s for s in spans)
        + sum(s.route.duration_s for s in spans)
    )
    span_steps = {i for s in spans for i in range(s.step_start, s.step_end)}
    ride_hw_s = sum(
        steps[i].static_duration_s * factors[i]
        for i in range(len(steps))
        if flags[i] and i not in span_steps
    ) + sum(s.hw_in_detour_s for s in spans)
    ride_hw_m = sum(
        steps[i].distance_m for i in range(len(steps)) if flags[i] and i not in span_steps
    ) + sum(s.hw_in_detour_m for s in spans)
    ride_distance = sum(seg.distance_m for seg in segments)

    handoff = [
        gmaps.HandoffDetour(
            entry=s.entry,
            mid=polyline_util.point_at_fraction(
                polyline_util.decode(s.route.encoded_polyline), 0.5
            ),
            exit=s.exit,
            value_s=s.baseline_s - s.hw_in_detour_s,
        )
        for s in spans
        if s.route.encoded_polyline
    ]
    ride = Ride(
        duration_s=round(ride_duration),
        extra_duration_s=round(ride_duration - base.duration_s),
        distance_m=round(ride_distance),
        highway_distance_m=round(ride_hw_m),
        highway_duration_s=round(ride_hw_s),
        segments=segments,
        gmaps_url=gmaps.build_gmaps_url(steps[0].start, steps[-1].end, handoff),
    )
    detours = [
        DetourOut(
            entry=LatLng(lat=s.entry[0], lng=s.entry[1]),
            exit=LatLng(lat=s.exit[0], lng=s.exit[1]),
            extra_duration_s=round(s.route.duration_s - s.baseline_s),
            avoided_highway_s=round(s.baseline_s - s.hw_in_detour_s),
            avoided_highway_m=round(s.baseline_hw_m - s.hw_in_detour_m),
            detour_distance_m=round(s.route.distance_m),
            curviness=round(_curviness(s.route, s.entry, s.exit), 3),
        )
        for s in spans
    ]
    return PlanResponse(budget_s=budget_s, fastest=fastest, ride=ride, detours=detours)


def _build_skeleton(
    steps: Sequence[GStep],
    factors: Sequence[float],
    flags: Sequence[bool],
    candidates: Sequence[_Candidate],
) -> list[SkeletonPart]:
    """Fastest route split so every cut candidate owns exactly one highway part."""
    by_start = {c.chunk.step_start: (f"c{i}", c) for i, c in enumerate(candidates)}
    parts: list[list] = []  # [kind, points, duration, distance, cut_id]
    i = 0
    while i < len(steps):
        hit = by_start.get(i)
        if hit is not None:
            cut_id, cand = hit
            pts: list[Point] = []
            duration = 0.0
            distance = 0.0
            for j in range(i, cand.chunk.step_end):
                pts.extend(
                    polyline_util.decode(steps[j].encoded_polyline)
                    if steps[j].encoded_polyline
                    else [steps[j].start, steps[j].end]
                )
                duration += steps[j].static_duration_s * factors[j]
                distance += steps[j].distance_m
            parts.append(["highway", pts, duration, distance, cut_id])
            i = cand.chunk.step_end
            continue
        kind = "highway" if flags[i] else "kept"
        pts = (
            polyline_util.decode(steps[i].encoded_polyline)
            if steps[i].encoded_polyline
            else [steps[i].start, steps[i].end]
        )
        if parts and parts[-1][0] == kind and parts[-1][4] is None:
            parts[-1][1].extend(pts)
            parts[-1][2] += steps[i].static_duration_s * factors[i]
            parts[-1][3] += steps[i].distance_m
        else:
            parts.append([kind, list(pts), steps[i].static_duration_s * factors[i], steps[i].distance_m, None])
        i += 1

    return [
        SkeletonPart(
            kind=kind,
            encoded_polyline=polyline_util.encode(_dedupe(pts)),
            duration_s=round(duration),
            distance_m=round(distance),
            cut_id=cut_id,
        )
        for kind, pts, duration, distance, cut_id in parts
    ]


def _plan_probe_spans(
    chunks: Sequence[Chunk],
    scores: Sequence[float | None],
    max_probes: int,
    min_curvy_km: float,
    max_span: int,
) -> list[Chunk]:
    """Turn scored corridors into probe candidates, merging good neighbors.

    Runs of consecutive well-scoring corridors merge into spans of up to max_span
    chunks — a continuously curvy region becomes one big sweep for one probe. Unknown
    scores (Overpass gaps) fail open as blind pairs, so bigger options exist even when
    scoring is down. Known-bad corridors are dropped. Known spans are ranked by total
    curvy km; unknown spans fill leftover probe slots evenly spread along the route.
    """
    known_spans: list[tuple[list[Chunk], float]] = []
    unknown_spans: list[list[Chunk]] = []
    i = 0
    n = len(chunks)
    while i < n:
        score = scores[i]
        if score is not None and score < min_curvy_km:
            i += 1
            continue
        known = score is not None
        run: list[tuple[Chunk, float]] = [(chunks[i], score or 0.0)]
        j = i + 1
        while j < n:
            next_score = scores[j]
            same_kind = (
                (next_score is not None and next_score >= min_curvy_km)
                if known
                else next_score is None
            )
            contiguous = (
                chunks[j].stretch_id == run[-1][0].stretch_id
                and chunks[j].step_start == run[-1][0].step_end
            )
            if not same_kind or not contiguous:
                break
            run.append((chunks[j], next_score or 0.0))
            j += 1
        size = max_span if known else 2
        for k in range(0, len(run), size):
            span = run[k : k + size]
            if known:
                known_spans.append(([c for c, _ in span], sum(s for _, s in span)))
            else:
                unknown_spans.append([c for c, _ in span])
        i = j

    known_spans.sort(key=lambda item: -item[1])
    picked: list[list[Chunk]] = [span for span, _ in known_spans[:max_probes]]
    slots = max_probes - len(picked)
    if slots > 0 and unknown_spans:
        stride = max(1, len(unknown_spans) // slots)
        picked.extend(unknown_spans[::stride][:slots])
    merged = [classify.merge_chunks(span) for span in picked]
    merged.sort(key=lambda c: c.step_start)
    return merged


async def _select_scout_chunks(
    chunks: list[Chunk], settings: Settings, emit: Emit | None = None
) -> list[Chunk]:
    if not chunks:
        return chunks
    if settings.osm_enabled and not settings.ihh_mock:
        on_batch = None
        if emit is not None:

            def on_batch(pairs: list, batch_scores: list) -> None:
                emit(
                    {
                        "type": "scored",
                        "corridors": [
                            {
                                "entry": {"lat": p[0][0], "lng": p[0][1]},
                                "exit": {"lat": p[1][0], "lng": p[1][1]},
                                "curvy_km": round(s, 1),
                            }
                            for p, s in zip(pairs, batch_scores)
                        ],
                    }
                )

        scores = await osm.score_chunks(
            [(c.entry, c.exit) for c in chunks], settings, on_batch=on_batch
        )
    else:
        scores = [None] * len(chunks)
    spans = _plan_probe_spans(
        chunks,
        scores,
        settings.scout_max_probes,
        settings.osm_min_curvy_km,
        settings.scout_max_span_chunks,
    )
    if len(spans) < len(chunks):
        logger.info(
            "scout: probing %d spans over %d corridors (OSM-ranked)", len(spans), len(chunks)
        )
    return spans


def _cut_fields(c: _Candidate, steps: Sequence[GStep]) -> dict:
    """CutOut fields (minus id) for one gated candidate — shared by the final response
    and the live stream."""
    mid = c.chunk.entry
    if c.route.encoded_polyline:
        mid = polyline_util.point_at_fraction(
            polyline_util.decode(c.route.encoded_polyline), 0.5
        )
    return {
        "road": classify.road_name(steps, c.chunk.step_start, c.chunk.step_end),
        "entry": LatLng(lat=c.chunk.entry[0], lng=c.chunk.entry[1]),
        "exit": LatLng(lat=c.chunk.exit[0], lng=c.chunk.exit[1]),
        "mid": LatLng(lat=mid[0], lng=mid[1]),
        "encoded_polyline": c.route.encoded_polyline,
        "detour_duration_s": round(c.route.duration_s),
        "detour_distance_m": round(c.route.distance_m),
        "extra_duration_s": round(c.extra_cost_s),
        "avoided_highway_s": round(c.value_s),
        "avoided_highway_m": round(c.chunk.distance_m - c.hw_in_detour_m),
        "curviness": round(_curviness(c.route, c.chunk.entry, c.chunk.exit), 3),
    }


async def scout(
    req: ScoutRequest,
    client: RoutesClient,
    settings: Settings,
    emit: Emit | None = None,
) -> ScoutResponse:
    """Evaluate ALL viable highway cuts so the rider composes the ride themselves.

    Same pipeline as plan() but without a knapsack: every gated candidate is returned
    as a priced, toggleable cut; selection totals are additive client-side
    (fastest.duration_s + Σ selected extra_duration_s). Chunks stay human-sized on any
    route length — OSM corridor scores decide which of them earn a paid probe.

    When `emit` is given, progress events stream out at every pipeline stage
    (docs/api.md, /api/scout/stream): route -> corridors -> scored* -> probing ->
    cut* -> (caller emits done).
    """
    base = await _fetch_base(req.origin, req.destination, client)
    steps, factors = _flatten(base)
    steps, factors = classify.atomize_steps(steps, factors, settings.step_atom_m)
    flags = classify.classify_steps(steps, settings)
    fastest = _fastest_summary(base, steps, factors, flags)
    if emit is not None:
        preview = _build_skeleton(steps, factors, flags, [])
        emit(
            {
                "type": "route",
                "origin": {"lat": steps[0].start[0], "lng": steps[0].start[1]},
                "destination": {"lat": steps[-1].end[0], "lng": steps[-1].end[1]},
                "fastest": fastest.model_dump(mode="json"),
                "preview": [p.model_dump(mode="json") for p in preview],
            }
        )

    stretches = classify.find_stretches(steps, flags, settings)
    total_hw_km = (
        sum(sum(steps[i].distance_m for i in range(a, b)) for a, b in stretches) / 1000.0
    )
    # Chunk size only grows once even scout_max_raw_chunks corridors can't cover the
    # highway — never balloons to +70 min cuts just because the route is long.
    eff_chunk_km = max(settings.scout_chunk_km, total_hw_km / settings.scout_max_raw_chunks)
    chunks = classify.build_chunks(
        steps,
        flags,
        factors,
        settings,
        chunk_km=eff_chunk_km,
        max_chunks=settings.scout_max_raw_chunks,
    )
    if emit is not None:
        emit(
            {
                "type": "corridors",
                "count": len(chunks),
                "corridors": [
                    {
                        "entry": {"lat": c.entry[0], "lng": c.entry[1]},
                        "exit": {"lat": c.exit[0], "lng": c.exit[1]},
                    }
                    for c in chunks
                ],
            }
        )
    chunks = await _select_scout_chunks(chunks, settings, emit)
    if emit is not None:
        emit({"type": "probing", "count": len(chunks)})

    on_candidate = None
    on_probe = None
    if emit is not None:

        def on_candidate(c: _Candidate) -> None:
            fields = _cut_fields(c, steps)
            emit({"type": "cut", "cut": CutOut(id="", **fields).model_dump(mode="json")})

        def on_probe(route: GRoute, kept: bool) -> None:
            # Every tested detour flashes on the client map; rejects fade away.
            emit(
                {
                    "type": "probe",
                    "encoded_polyline": route.encoded_polyline,
                    "kept": kept,
                }
            )

    candidates = await _query_detours(
        chunks, client, settings, on_candidate=on_candidate, on_probe=on_probe
    )
    candidates = sorted(candidates, key=lambda c: c.chunk.step_start)

    skeleton = _build_skeleton(steps, factors, flags, candidates)
    cuts = [
        CutOut(id=f"c{i}", **_cut_fields(c, steps)) for i, c in enumerate(candidates)
    ]
    return ScoutResponse(
        origin=LatLng(lat=steps[0].start[0], lng=steps[0].start[1]),
        destination=LatLng(lat=steps[-1].end[0], lng=steps[-1].end[1]),
        fastest=fastest,
        skeleton=skeleton,
        cuts=cuts,
    )


async def scout_events(
    req: ScoutRequest, client: RoutesClient, settings: Settings
) -> AsyncIterator[dict]:
    """Run scout() and yield its progress events, ending with done or error."""
    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def run() -> None:
        try:
            resp = await scout(req, client, settings, emit=queue.put_nowait)
            queue.put_nowait({"type": "done", "scout": resp.model_dump(mode="json")})
        except PlanError as exc:
            queue.put_nowait(
                {"type": "error", "code": exc.code, "message": exc.message, "status": exc.status}
            )
        except Exception:
            logger.exception("scout stream failed")
            queue.put_nowait(
                {
                    "type": "error",
                    "code": "UPSTREAM",
                    "message": "Scouting failed unexpectedly. Try again in a moment.",
                    "status": 502,
                }
            )

    task = asyncio.create_task(run())
    try:
        while True:
            event = await queue.get()
            yield event
            if event["type"] in ("done", "error"):
                break
    finally:
        task.cancel()
