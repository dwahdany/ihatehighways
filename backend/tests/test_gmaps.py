from urllib.parse import parse_qs, urlparse

from app import polyline_util
from app.gmaps import HandoffDetour, build_gmaps_url

ORIGIN = (50.94, 6.96)
DEST = (50.11, 8.68)


def make_detour(i: float, value_s: float) -> HandoffDetour:
    return HandoffDetour(
        entry=(50.0 + i, 7.0),
        mid=(50.0 + i, 7.1),
        exit=(50.1 + i, 7.2),
        value_s=value_s,
    )


def waypoints_of(url: str) -> list[str]:
    query = parse_qs(urlparse(url).query)
    if "waypoints" not in query:
        return []
    return query["waypoints"][0].split("|")


def test_no_detours_plain_directions_link():
    url = build_gmaps_url(ORIGIN, DEST, [])
    q = parse_qs(urlparse(url).query)
    assert q["origin"] == ["50.94000,6.96000"]
    assert q["destination"] == ["50.11000,8.68000"]
    assert q["travelmode"] == ["driving"]
    assert "waypoints" not in q


def test_three_detours_all_pinned_with_midpoints():
    detours = [make_detour(i, value_s=100 * (i + 1)) for i in range(3)]
    wps = waypoints_of(build_gmaps_url(ORIGIN, DEST, detours))
    assert len(wps) == 9  # entry+mid+exit for each
    assert wps[0] == "50.00000,7.00000" and wps[1] == "50.00000,7.10000"


def test_four_detours_lowest_value_loses_midpoints_first():
    detours = [make_detour(i, value_s=100 * (i + 1)) for i in range(4)]
    wps = waypoints_of(build_gmaps_url(ORIGIN, DEST, detours))
    assert len(wps) <= 9
    # The lowest-value detour (i=0) lost its midpoint; the highest kept it.
    assert "50.00000,7.10000" not in wps
    assert "53.00000,7.10000" in wps


def test_six_detours_whole_low_value_detours_dropped():
    detours = [make_detour(i, value_s=100 * (i + 1)) for i in range(6)]
    wps = waypoints_of(build_gmaps_url(ORIGIN, DEST, detours))
    assert len(wps) <= 9
    # Highest-value detour survives with entry+exit at least.
    assert "55.00000,7.00000" in wps and "55.10000,7.20000" in wps
    # Lowest-value detour is gone entirely.
    assert not any(w.startswith("50.0") or w == "50.10000,7.20000" for w in wps)


def test_point_at_fraction_midpoint():
    pts = [(50.0, 7.0), (50.0, 7.1), (50.0, 7.2)]
    mid = polyline_util.point_at_fraction(pts, 0.5)
    assert abs(mid[0] - 50.0) < 1e-9
    assert abs(mid[1] - 7.1) < 1e-3
    assert polyline_util.point_at_fraction(pts, 0.0) == pts[0]
    assert polyline_util.point_at_fraction(pts, 1.0) == pts[-1]
