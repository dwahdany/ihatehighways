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
