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


def test_pick_by_scores_ranks_and_fails_open():
    from app.planner import _pick_by_scores

    # Known-bad corridors dropped; best known scores win the probe budget.
    scores = [0.5, 10.0, 5.0, None, 8.0, 1.0]
    assert _pick_by_scores(6, scores, max_probes=2, min_curvy_km=2.0) == [1, 4]
    # All-unknown (Overpass down) degrades to even sampling along the route.
    picked = _pick_by_scores(10, [None] * 10, max_probes=3, min_curvy_km=2.0)
    assert len(picked) == 3
    assert picked == sorted(picked)
    assert picked[-1] - picked[0] >= 5  # spread, not the first three
    # Under budget: everything eligible is probed, known-bad still dropped.
    assert _pick_by_scores(3, [None, 5.0, 1.0], max_probes=12, min_curvy_km=2.0) == [0, 1]


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
        # Chunk sizes must stay near scout_chunk_km on any route length.
        assert parts_by_cut[cut.id].distance_m <= SETTINGS.scout_chunk_km * 1000 * 1.1
    # Probes spread along the haul, not clustered at the start.
    firsts = [p.cut_id is not None for p in resp.skeleton]
    assert any(firsts[len(firsts) // 2 :])


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
