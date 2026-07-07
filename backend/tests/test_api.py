from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client() -> TestClient:
    # Settings override (equivalent to IHH_MOCK=1 in the environment).
    return TestClient(create_app(Settings(_env_file=None, ihh_mock=True)))


PLAN_BODY = {
    "origin": {"lat_lng": {"lat": 50.94, "lng": 6.96}},
    "destination": {"address": "Frankfurt am Main"},
    "max_extra_minutes": 15,
}


def test_health():
    with make_client() as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "mock": True, "key_configured": True}


def test_plan_happy_path_matches_contract():
    with make_client() as client:
        resp = client.post("/api/plan", json=PLAN_BODY)
    assert resp.status_code == 200
    data = resp.json()

    assert set(data) == {"budget_s", "fastest", "ride", "detours"}
    assert data["budget_s"] == 900

    assert set(data["fastest"]) == {
        "encoded_polyline",
        "duration_s",
        "static_duration_s",
        "distance_m",
        "highway_distance_m",
        "highway_duration_s",
    }

    assert set(data["ride"]) == {
        "duration_s",
        "extra_duration_s",
        "distance_m",
        "highway_distance_m",
        "highway_duration_s",
        "segments",
        "gmaps_url",
    }
    assert data["ride"]["gmaps_url"].startswith("https://www.google.com/maps/dir/?api=1")
    assert data["ride"]["segments"]
    for segment in data["ride"]["segments"]:
        assert set(segment) == {"kind", "encoded_polyline", "duration_s", "distance_m"}
        assert segment["kind"] in {"kept", "highway", "detour"}

    assert isinstance(data["detours"], list) and data["detours"]
    for detour in data["detours"]:
        assert set(detour) == {
            "entry",
            "exit",
            "extra_duration_s",
            "avoided_highway_s",
            "avoided_highway_m",
            "detour_distance_m",
            "curviness",
        }
        assert set(detour["entry"]) == {"lat", "lng"}
        assert set(detour["exit"]) == {"lat", "lng"}


def test_plan_budget_out_of_range_rejected():
    body = dict(PLAN_BODY, max_extra_minutes=200)
    with make_client() as client:
        resp = client.post("/api/plan", json=body)
    assert resp.status_code in (400, 422)
    if resp.status_code == 400:
        assert resp.json()["detail"]["code"] == "INVALID_INPUT"


def test_plan_budget_wrong_type_rejected():
    # Regression: pydantic lax coercion accepted true (-> 1 min) and "15" (-> 15 min);
    # docs/api.md defines max_extra_minutes as int 0-120, so non-ints must 400.
    with make_client() as client:
        for bad in (True, False, "15", 15.5):
            resp = client.post("/api/plan", json=dict(PLAN_BODY, max_extra_minutes=bad))
            assert resp.status_code == 400, f"max_extra_minutes={bad!r} was accepted"
            assert resp.json()["detail"]["code"] == "INVALID_INPUT"
        # Plain ints within range still work.
        resp = client.post("/api/plan", json=dict(PLAN_BODY, max_extra_minutes=0))
        assert resp.status_code == 200
        assert resp.json()["budget_s"] == 0


def test_plan_missing_origin_rejected():
    body = {k: v for k, v in PLAN_BODY.items() if k != "origin"}
    with make_client() as client:
        resp = client.post("/api/plan", json=body)
    assert resp.status_code in (400, 422)
    if resp.status_code == 400:
        assert resp.json()["detail"]["code"] == "INVALID_INPUT"


def test_plan_origin_with_two_fields_rejected():
    body = dict(
        PLAN_BODY,
        origin={"address": "Cologne", "lat_lng": {"lat": 50.94, "lng": 6.96}},
    )
    with make_client() as client:
        resp = client.post("/api/plan", json=body)
    assert resp.status_code in (400, 422)


def test_rate_limit_and_daily_cap():
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    body = {
        "origin": {"address": "Cologne"},
        "destination": {"address": "Frankfurt"},
        "max_extra_minutes": 15,
    }
    settings = Settings(_env_file=None, ihh_mock=True, rate_per_ip_hour=1, rate_daily_cap=100)
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/plan", json=body).status_code == 200
        # Identical request hits the response cache — never rate limited.
        assert client.post("/api/plan", json=body).status_code == 200
        other = dict(body, max_extra_minutes=30)
        resp = client.post("/api/plan", json=other)
        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "RATE_LIMITED"

    settings = Settings(_env_file=None, ihh_mock=True, rate_per_ip_hour=10, rate_daily_cap=1)
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/plan", json=body).status_code == 200
        resp = client.post("/api/plan", json=dict(body, max_extra_minutes=30))
        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "DAILY_CAP"
