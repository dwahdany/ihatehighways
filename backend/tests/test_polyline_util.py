from app import polyline_util


def test_point_to_path_measures_segments_not_vertices():
    path = [(50.0, 7.0), (50.0, 7.5)]  # one straight ~35 km segment, no interior vertices
    assert polyline_util.point_to_path_m((50.0, 7.25), path) < 5
    off = (50.01, 7.25)  # ~1.1 km north of the segment
    assert 1000 < polyline_util.point_to_path_m(off, path) < 1300
    assert polyline_util.point_to_path_m((50.0, 7.25), []) == float("inf")


def test_encode_decode_roundtrip():
    points = [(50.94, 6.96), (50.7213, 7.20111), (50.11, 8.68)]
    encoded = polyline_util.encode(points)
    decoded = polyline_util.decode(encoded)
    assert len(decoded) == len(points)
    for (lat1, lng1), (lat2, lng2) in zip(points, decoded):
        assert abs(lat1 - lat2) < 1e-5 + 1e-9
        assert abs(lng1 - lng2) < 1e-5 + 1e-9


def test_decode_known_google_example():
    # Canonical example from Google's encoded polyline algorithm docs.
    decoded = polyline_util.decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    expected = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert len(decoded) == 3
    for got, want in zip(decoded, expected):
        assert abs(got[0] - want[0]) < 1e-5
        assert abs(got[1] - want[1]) < 1e-5


def test_bearing_cardinal_sanity():
    assert abs(polyline_util.initial_bearing_deg((50.0, 7.0), (51.0, 7.0)) - 0) <= 1
    east = polyline_util.initial_bearing_deg((50.0, 7.0), (50.0, 8.0))
    assert 88 <= east <= 92
    assert abs(polyline_util.initial_bearing_deg((51.0, 7.0), (50.0, 7.0)) - 180) <= 1
    west = polyline_util.initial_bearing_deg((50.0, 8.0), (50.0, 7.0))
    assert 268 <= west <= 272


def test_bearing_range():
    b = polyline_util.initial_bearing_deg((50.0, 7.0), (49.5, 6.5))
    assert 0 <= b <= 360


def test_haversine_known_distance():
    # One degree along a meridian on the mean sphere is ~111,195 m.
    d = polyline_util.haversine_m((0.0, 0.0), (1.0, 0.0))
    assert abs(d - 111_195) / 111_195 < 0.01
    d_eq = polyline_util.haversine_m((0.0, 0.0), (0.0, 1.0))
    assert abs(d_eq - 111_195) / 111_195 < 0.01


def test_point_and_bearing_at_distance():
    from app.polyline_util import bearing_at_distance_m, haversine_m, point_at_distance_m

    north = [(50.0, 7.0), (50.09, 7.0)]  # ~10 km due north
    mid = point_at_distance_m(north, 5_000)
    assert abs(haversine_m(north[0], mid) - 5_000) < 5
    assert bearing_at_distance_m(north, 5_000) in (0, 360)
    assert point_at_distance_m(north, 99_000) == north[-1]
    assert bearing_at_distance_m(north, 99_000) in (0, 360)


def test_project_arclen_m():
    from app.polyline_util import project_arclen_m

    north = [(50.0, 7.0), (50.045, 7.0), (50.09, 7.0)]  # ~10 km, one mid vertex
    east_20m = 20.0 / (111_320.0 * 0.6428)  # ~20 m in degrees longitude at lat 50
    arclen, dist = project_arclen_m((50.027, 7.0 + east_20m), north)
    assert abs(dist - 20.0) < 2.0
    assert abs(arclen - 0.027 * 111_195.0) < 20.0
    # A point before the path start projects to arclen 0.
    arclen, _ = project_arclen_m((49.99, 7.0), north)
    assert arclen == 0.0
