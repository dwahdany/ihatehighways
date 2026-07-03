import asyncio

from app import polyline_util
from app.config import Settings
from app.google_routes import MockRoutesClient
from app.models import LatLng, PlacePoint, ScoutRequest
from app.planner import scout

SETTINGS = Settings(_env_file=None, ihh_mock=True)


def run_scout():
    req = ScoutRequest(
        origin=PlacePoint(lat_lng=LatLng(lat=50.94, lng=6.96)),
        destination=PlacePoint(address="Frankfurt am Main"),
    )
    return asyncio.run(scout(req, MockRoutesClient(), SETTINGS))


def test_scout_offers_cuts_with_skeleton():
    resp = run_scout()
    assert resp.cuts, "the mock route must yield cut candidates"
    cut_ids = {c.id for c in resp.cuts}
    skeleton_cut_ids = {p.cut_id for p in resp.skeleton if p.cut_id}
    assert skeleton_cut_ids == cut_ids  # every cut owns exactly one skeleton part
    assert all(p.kind == "highway" for p in resp.skeleton if p.cut_id)

    # Skeleton is contiguous origin -> destination and covers the fastest route.
    previous_end = None
    total_m = 0
    for part in resp.skeleton:
        pts = polyline_util.decode(part.encoded_polyline)
        assert len(pts) >= 2
        if previous_end is not None:
            assert polyline_util.haversine_m(previous_end, pts[0]) < 100
        previous_end = pts[-1]
        total_m += part.distance_m
    assert abs(total_m - resp.fastest.distance_m) / resp.fastest.distance_m < 0.02


def test_scout_cut_pricing_is_composable():
    resp = run_scout()
    parts_by_cut = {p.cut_id: p for p in resp.skeleton if p.cut_id}
    for cut in resp.cuts:
        part = parts_by_cut[cut.id]
        # Replacing the part with the cut costs extra_duration_s by definition:
        # detour_duration - baseline(part duration).
        assert abs(part.duration_s - (cut.detour_duration_s - cut.extra_duration_s)) <= 2
        assert cut.avoided_highway_s > 0
        assert cut.curviness >= 1.0
        assert cut.encoded_polyline
        # Mid sits on the detour path (mock arcs bulge away from the chord).
        pts = polyline_util.decode(cut.encoded_polyline)
        assert min(polyline_util.haversine_m((cut.mid.lat, cut.mid.lng), p) for p in pts) < 500
        # The mock's A3 instructions surface as the road label.
        assert cut.road == "A3"


def make_chunk(i: int, stretch: int = 0):
    from app.classify import Chunk

    return Chunk(
        stretch_id=stretch,
        step_start=i * 2,
        step_end=i * 2 + 2,
        distance_m=25_000,
        static_duration_s=800.0,
        baseline_s=800.0,
        entry=(50.0 + i * 0.2, 7.0),
        exit=(50.2 + i * 0.2, 7.0),
        entry_heading=0,
    )


def test_plan_probe_spans_merges_good_neighbors():
    from app.planner import _plan_probe_spans

    chunks = [make_chunk(i) for i in range(6)]
    scores = [5.0, 6.0, 7.0, 8.0, 0.5, None]
    spans = _plan_probe_spans(chunks, scores, max_probes=12, min_curvy_km=2.0, max_span=3)
    ranges = [(s.step_start, s.step_end) for s in spans]
    assert (0, 6) in ranges  # chunks 0-2 merged into one 75 km sweep
    assert (6, 8) in ranges  # chunk 3, the run remainder
    assert all(not (r[0] <= 8 < r[1]) for r in ranges)  # known-bad chunk 4 dropped
    assert (10, 12) in ranges  # unknown chunk 5 probed blind
    merged = next(s for s in spans if (s.step_start, s.step_end) == (0, 6))
    assert merged.distance_m == 75_000
    assert merged.baseline_s == 2400.0
    assert merged.entry == chunks[0].entry and merged.exit == chunks[2].exit


def test_plan_probe_spans_ranks_by_total_curvy_and_caps():
    from app.planner import _plan_probe_spans

    chunks = [make_chunk(i) for i in range(8)]
    scores = [10.0, 0.1, 3.0, 0.1, 7.0, 0.1, None, None]
    spans = _plan_probe_spans(chunks, scores, max_probes=2, min_curvy_km=2.0, max_span=3)
    # Top-2 isolated known spans by score (10.0 and 7.0), returned in route order.
    assert [(s.step_start, s.step_end) for s in spans] == [(0, 2), (8, 10)]


def test_plan_probe_spans_blind_pairs_when_unscored():
    from app.planner import _plan_probe_spans

    chunks = [make_chunk(i) for i in range(6)]
    spans = _plan_probe_spans(chunks, [None] * 6, max_probes=12, min_curvy_km=2.0, max_span=3)
    # Overpass down: pairs, so bigger options still exist.
    assert [(s.step_start, s.step_end) for s in spans] == [(0, 4), (4, 8), (8, 12)]


def test_scout_chunks_stay_small_on_long_routes():
    from app.classify import build_chunks, classify_steps
    from tests.test_classify import make_run

    settings = Settings(_env_file=None)
    # ~1000 km of motorway: with the old max_chunks=10 cap this ballooned to ~100 km
    # chunks; the scout path must keep them at scout_chunk_km.
    steps = make_run([(5_000, 115, "Continue on A24", "")] * 200)
    flags = classify_steps(steps, settings)
    chunks = build_chunks(
        steps,
        flags,
        [1.0] * len(steps),
        settings,
        chunk_km=settings.scout_chunk_km,
        max_chunks=settings.scout_max_raw_chunks,
    )
    assert len(chunks) == 40  # 1000 km / 25 km
    assert all(c.distance_m <= settings.scout_chunk_km * 1000 for c in chunks)


def test_scout_long_haul_caps_probes_and_chunk_sizes():
    """End-to-end wiring: a ~500 km motorway must yield human-sized cuts capped at
    scout_max_probes — not ten +70 min monsters (the Berlin->Calais regression)."""
    from app.google_routes import GLeg, GRoute
    from tests.test_classify import make_run

    class LongHaulClient(MockRoutesClient):
        async def compute_route(self, origin, destination, avoid_highways=False, origin_heading=None):
            if avoid_highways:
                return await super().compute_route(origin, destination, True, origin_heading)
            steps = make_run(
                [(5_000, 60, "Follow K7", "")]
                + [(5_000, 115, "Continue on A7", "")] * 100
                + [(5_000, 60, "Follow K7", "")]
            )
            static = sum(s.static_duration_s for s in steps)
            dist = sum(s.distance_m for s in steps)
            leg = GLeg(static, static, dist, steps)
            pts: list = []
            for s in steps:
                pts.extend(polyline_util.decode(s.encoded_polyline))
            return GRoute(static, static, dist, polyline_util.encode(pts), [leg])

    req = ScoutRequest(
        origin=PlacePoint(lat_lng=LatLng(lat=50.0, lng=7.0)),
        destination=PlacePoint(address="far away"),
    )
    resp = asyncio.run(scout(req, LongHaulClient(), SETTINGS))
    assert 0 < len(resp.cuts) <= SETTINGS.scout_max_probes
    parts_by_cut = {p.cut_id: p for p in resp.skeleton if p.cut_id}
    for cut in resp.cuts:
        # Unscored corridors probe as blind pairs at most: <= 2 chunks per cut.
        assert parts_by_cut[cut.id].distance_m <= SETTINGS.scout_chunk_km * 2 * 1000 * 1.1
    # Probes spread along the haul, not clustered at the start.
    firsts = [p.cut_id is not None for p in resp.skeleton]
    assert any(firsts[len(firsts) // 2 :])


def test_scout_probes_launch_while_scoring_still_runs(monkeypatch):
    """Clearly-good corridors probe the moment their OSM batch lands — probing
    overlaps the scoring wait instead of bursting after it."""
    from app import planner
    from app.google_routes import GLeg, GRoute, GStep
    from app.planner import _scout_probes

    settings = Settings(
        _env_file=None, ihh_mock=False, osm_enabled=True, scout_max_probes=4
    )
    chunks = [make_chunk(i) for i in range(8)]
    order: list[str] = []

    async def fake_score_chunks(pairs, s, deadline_s=None, on_batch=None):
        assert on_batch is not None
        on_batch(pairs[:3], [10.0, 9.0, 8.0])  # adjacent, clearly good
        await asyncio.sleep(0.05)  # the rest of "scoring" takes a while
        order.append("scoring-done")
        return [10.0, 9.0, 8.0] + [None] * 5

    monkeypatch.setattr(planner.osm, "score_chunks", fake_score_chunks)

    class CountryClient:
        async def compute_route(self, origin, destination, avoid_highways=False, origin_heading=None):
            order.append("probe")
            start, end = origin.lat_lng, destination.lat_lng
            distance = polyline_util.haversine_m(start, end) * 1.3
            static = distance / (60 / 3.6)  # honest country detour, no highway
            step = GStep(
                distance_m=distance,
                static_duration_s=static,
                encoded_polyline=polyline_util.encode([start, end]),
                maneuver="",
                instructions="Follow the country roads",
                start=start,
                end=end,
            )
            leg = GLeg(static, static, distance, [step])
            return GRoute(static, static, distance, step.encoded_polyline, [leg])

    candidates = asyncio.run(
        _scout_probes(chunks, CountryClient(), settings, None, None, None)
    )
    # The good trio's probe started before scoring finished.
    assert "probe" in order[: order.index("scoring-done")]
    ranges = [(c.chunk.step_start, c.chunk.step_end) for c in candidates]
    assert (0, 6) in ranges  # merged 3-chunk span, launched immediately
    assert len(candidates) <= settings.scout_max_probes


def test_scout_stream_events():
    import json

    from fastapi.testclient import TestClient

    from app.main import create_app

    body = {"origin": {"address": "Cologne"}, "destination": {"address": "Frankfurt"}}
    with TestClient(create_app(SETTINGS)) as client:
        with client.stream("POST", "/api/scout/stream", json=body) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("application/x-ndjson")
            events = [json.loads(line) for line in resp.iter_lines() if line]

        types = [e["type"] for e in events]
        assert types[0] == "route"
        assert "corridors" in types
        assert "probing" in types
        assert types[-1] == "done"
        assert types.count("done") == 1

        route_ev = events[0]
        assert all(p["cut_id"] is None for p in route_ev["preview"])
        assert route_ev["fastest"]["duration_s"] > 0

        done = events[-1]["scout"]
        assert set(done) == {"origin", "destination", "fastest", "skeleton", "cuts"}
        cut_events = [e for e in events if e["type"] == "cut"]
        assert len(cut_events) == len(done["cuts"]) > 0
        for e in cut_events:
            assert e["cut"]["avoided_highway_s"] > 0
        # Every tested detour streams a probe event; kept ones match the cuts.
        probe_events = [e for e in events if e["type"] == "probe"]
        assert len(probe_events) >= len(cut_events)
        assert sum(1 for e in probe_events if e["kept"]) == len(cut_events)
        assert all(e["encoded_polyline"] for e in probe_events)

        # Second call replays the cached result as a single done event.
        with client.stream("POST", "/api/scout/stream", json=body) as resp2:
            events2 = [json.loads(line) for line in resp2.iter_lines() if line]
        assert [e["type"] for e in events2] == ["done"]
        assert events2[0]["scout"] == done


def test_scout_api_contract():
    import os

    os.environ["IHH_MOCK"] = "1"
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(SETTINGS)) as client:
        resp = client.post(
            "/api/scout",
            json={"origin": {"address": "Cologne"}, "destination": {"address": "Frankfurt"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"origin", "destination", "fastest", "skeleton", "cuts"}
        assert set(data["origin"]) == {"lat", "lng"}
        for cut in data["cuts"]:
            assert set(cut) == {
                "id",
                "road",
                "entry",
                "exit",
                "mid",
                "encoded_polyline",
                "detour_duration_s",
                "detour_distance_m",
                "extra_duration_s",
                "avoided_highway_s",
                "avoided_highway_m",
                "curviness",
            }
        for part in data["skeleton"]:
            assert set(part) == {"kind", "encoded_polyline", "duration_s", "distance_m", "cut_id"}
            assert part["kind"] in {"kept", "highway"}
