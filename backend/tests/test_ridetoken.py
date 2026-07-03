import asyncio

from fastapi.testclient import TestClient

from app import polyline_util
from app.config import Settings
from app.google_routes import GRoute
from app.main import create_app
from app.models import CutPin, LatLng, RideTokenRequest
from app.ridetoken import build_ride_token

SETTINGS = Settings(_env_file=None, ihh_mock=True)


def straight_cut(lat0: float, lat1: float, lng: float = 7.0) -> dict:
    """A collinear cut; the mock's chord route through its mid pin follows it exactly."""
    mid_lat = (lat0 + lat1) / 2
    return {
        "entry": {"lat": lat0, "lng": lng},
        "mid": {"lat": mid_lat, "lng": lng},
        "exit": {"lat": lat1, "lng": lng},
        "encoded_polyline": polyline_util.encode([(lat0, lng), (mid_lat, lng), (lat1, lng)]),
    }


def test_ride_token_mock_contract():
    body = {
        "origin": {"lat": 50.0, "lng": 7.0},
        "destination": {"lat": 51.0, "lng": 7.0},
        "cuts": [straight_cut(50.2, 50.3), straight_cut(50.6, 50.7)],
    }
    with TestClient(create_app(SETTINGS)) as client:
        resp = client.post("/api/ride-token", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {
            "route_token",
            "encoded_polyline",
            "duration_s",
            "distance_m",
            "waypoints",
            "cuts_followed",
        }
        assert data["route_token"] == "mock-route-token"
        assert data["cuts_followed"] == [True, True]
        # The Navigation SDK destination list must END at the actual destination.
        assert data["waypoints"][-1] == {"lat": 51.0, "lng": 7.0}
        assert len(data["waypoints"]) == 3  # two mid pins + destination
        assert data["duration_s"] > 0 and data["distance_m"] > 0

        # Validation: too many cuts, and junk polylines -> envelope errors.
        too_many = dict(body, cuts=[straight_cut(50.2, 50.3)] * 13)
        assert client.post("/api/ride-token", json=too_many).status_code in (400, 422)
        junk = dict(body, cuts=[dict(straight_cut(50.2, 50.3), encoded_polyline="x")])
        resp_junk = client.post("/api/ride-token", json=junk)
        assert resp_junk.status_code == 400
        assert resp_junk.json()["detail"]["code"] == "INVALID_INPUT"


def test_fidelity_loop_pins_unfollowed_cuts():
    """First token route ignores the cut -> its entry/exit get pinned and we retry."""
    cut_pts = [(50.40 + 0.02 * i, 7.05) for i in range(6)]
    cut = CutPin(
        entry=LatLng(lat=50.40, lng=7.05),
        mid=LatLng(lat=50.45, lng=7.05),
        exit=LatLng(lat=50.50, lng=7.05),
        encoded_polyline=polyline_util.encode(cut_pts),
    )
    calls: list[list] = []

    class StubClient:
        async def compute_route_token(self, origin, destination, intermediates):
            calls.append(list(intermediates))
            if len(calls) == 1:
                pts = [(50.0, 7.0), (51.0, 7.0)]  # straight past the cut (~3.5 km off)
            else:
                pts = [(50.0, 7.0), *cut_pts, (51.0, 7.0)]
            return ("tok", GRoute(3600.0, 3600.0, 100_000.0, polyline_util.encode(pts), []))

    req = RideTokenRequest(
        origin=LatLng(lat=50.0, lng=7.0),
        destination=LatLng(lat=51.0, lng=7.0),
        cuts=[cut],
    )
    resp = asyncio.run(build_ride_token(req, StubClient(), SETTINGS))
    assert len(calls) == 2  # retried once
    assert len(calls[0]) == 1 and len(calls[1]) == 3  # mid -> entry+mid+exit
    assert resp.cuts_followed == [True]
    assert len(resp.waypoints) == 4  # three pins + destination
    assert resp.waypoints[-1] == LatLng(lat=51.0, lng=7.0)
