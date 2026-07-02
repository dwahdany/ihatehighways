import asyncio

from app import polyline_util
from app.config import Settings
from app.google_routes import MockRoutesClient
from app.models import LatLng, PlacePoint, PlanRequest
from app.planner import plan

SETTINGS = Settings(_env_file=None, ihh_mock=True)

VALID_KINDS = {"kept", "highway", "detour"}


def run_plan(max_extra_minutes: int):
    req = PlanRequest(
        origin=PlacePoint(lat_lng=LatLng(lat=50.94, lng=6.96)),
        destination=PlacePoint(address="Frankfurt am Main"),
        max_extra_minutes=max_extra_minutes,
    )
    return asyncio.run(plan(req, MockRoutesClient(), SETTINGS))


def test_plan_respects_budget_and_reduces_highway():
    resp = run_plan(15)
    budget = 15 * 60
    assert resp.budget_s == budget
    assert resp.ride.duration_s <= resp.fastest.duration_s + budget + 1
    assert resp.ride.highway_duration_s < resp.fastest.highway_duration_s
    assert resp.detours, "the mock must yield at least one affordable detour"


def test_segments_ordered_and_valid():
    resp = run_plan(15)
    segments = resp.ride.segments
    assert segments
    assert all(s.kind in VALID_KINDS for s in segments)
    # No two adjacent segments share a kind (they would have been merged).
    for a, b in zip(segments, segments[1:]):
        assert a.kind != b.kind
    # Every polyline decodes, and consecutive segments connect end-to-start.
    previous_end = None
    for seg in segments:
        pts = polyline_util.decode(seg.encoded_polyline)
        assert len(pts) >= 2
        assert seg.duration_s > 0
        assert seg.distance_m > 0
        if previous_end is not None:
            assert polyline_util.haversine_m(previous_end, pts[0]) < 100
        previous_end = pts[-1]


def test_detours_consistent_with_segments():
    resp = run_plan(15)
    detour_segments = [s for s in resp.ride.segments if s.kind == "detour"]
    assert len(detour_segments) == len(resp.detours)
    total_extra = sum(d.extra_duration_s for d in resp.detours)
    assert abs(resp.ride.extra_duration_s - total_extra) <= len(resp.detours) + 1
    for d in resp.detours:
        assert d.curviness >= 1.0
        assert d.avoided_highway_s > 0
        assert d.avoided_highway_m > 0
        assert d.detour_distance_m > 0


def test_zero_budget_only_free_detours():
    resp = run_plan(0)
    assert resp.budget_s == 0
    assert resp.ride.extra_duration_s <= 0
    assert all(d.extra_duration_s <= 0 for d in resp.detours)
    assert resp.ride.duration_s <= resp.fastest.duration_s + 1


def test_efficiency_gate_rejects_junk_detours():
    """A paid detour that sheds little highway time relative to its cost is skipped."""
    from app.classify import Chunk
    from app.google_routes import GLeg, GRoute, GStep
    from app.planner import _query_detours

    def _step(start, distance_m: float, speed_kmh: float, instructions: str) -> GStep:
        end = (start[0] + distance_m / 111_195.0, start[1])
        return GStep(
            distance_m=distance_m,
            static_duration_s=distance_m / (speed_kmh / 3.6),
            encoded_polyline=polyline_util.encode([start, end]),
            maneuver="",
            instructions=instructions,
            start=start,
            end=end,
        )

    class JunkDetourClient:
        async def compute_route(self, origin, destination, avoid_highways=False, origin_heading=None):
            # 18 km "detour" that still rides 4 km of A5 and crawls the rest through
            # town, taking 1200 s: sheds some highway time (value ~318 s, above the
            # escape gate's 250 s) but less than half its 700 s cost -> efficiency-gated.
            s1 = _step((50.0, 7.0), 4_000, 80.0, "Continue on A5")  # named: highway
            s2 = _step(s1.end, 14_000, 50.0, "Follow Hauptstraße")
            static = s1.static_duration_s + s2.static_duration_s
            leg = GLeg(1200.0, static, 18_000, [s1, s2])
            pts = polyline_util.decode(s1.encoded_polyline) + polyline_util.decode(s2.encoded_polyline)
            return GRoute(1200.0, static, 18_000, polyline_util.encode(pts), [leg])

    chunk = Chunk(
        stretch_id=0,
        step_start=0,
        step_end=2,
        distance_m=16_000,
        static_duration_s=500.0,
        baseline_s=500.0,
        entry=(50.0, 7.0),
        exit=(50.14, 7.0),
        entry_heading=0,
    )
    candidates = asyncio.run(_query_detours([chunk], JunkDetourClient(), SETTINGS))
    assert candidates == []  # value ~318 s vs cost 700 s -> gated


def test_escape_gate_rejects_detour_that_stays_on_the_motorway():
    """Soft avoidHighways can return the motorway itself between mid-motorway points;
    even when leg-average traffic scaling makes it look faster than baseline (negative
    cost), it must not count as a detour."""
    from app.classify import Chunk
    from app.google_routes import GLeg, GRoute, GStep
    from app.planner import _query_detours

    class StaysOnMotorwayClient:
        async def compute_route(self, origin, destination, avoid_highways=False, origin_heading=None):
            start, end = (50.0, 7.0), (50.144, 7.0)
            step = GStep(
                distance_m=16_000,
                static_duration_s=500.0,
                encoded_polyline=polyline_util.encode([start, end]),
                maneuver="",
                instructions="Continue on A3",
                start=start,
                end=end,
            )
            # Same 16 km of A3, measured slightly faster than the leg-scaled baseline.
            leg = GLeg(440.0, 500.0, 16_000, [step])
            return GRoute(440.0, 500.0, 16_000, step.encoded_polyline, [leg])

    chunk = Chunk(
        stretch_id=0,
        step_start=0,
        step_end=2,
        distance_m=16_000,
        static_duration_s=500.0,
        baseline_s=500.0,
        entry=(50.0, 7.0),
        exit=(50.144, 7.0),
        entry_heading=0,
    )
    candidates = asyncio.run(_query_detours([chunk], StaysOnMotorwayClient(), SETTINGS))
    assert candidates == []  # negative cost but avoids ~no highway -> escape-gated


def test_ride_stats_recomputed_from_stitched_route():
    resp = run_plan(15)
    highway_seg_m = sum(s.distance_m for s in resp.ride.segments if s.kind == "highway")
    # Ride highway distance comes from step classification of kept + detour parts; with
    # the mock's country detours it equals the unreplaced highway segments.
    assert abs(resp.ride.highway_distance_m - highway_seg_m) <= 5
    assert resp.ride.highway_distance_m < resp.fastest.highway_distance_m
    assert resp.ride.distance_m >= resp.fastest.distance_m  # detours are longer
