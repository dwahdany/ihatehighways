import math

from app.osm import _bbox, _way_excess_km, score_ways


def way(points: list[tuple[float, float]]) -> dict:
    return {"geometry": [{"lat": lat, "lon": lon} for lat, lon in points]}


def zigzag(n: int = 20, amp: float = 0.004, step: float = 0.003) -> list[tuple[float, float]]:
    """A twisty road heading east: length substantially exceeds the chord."""
    return [(50.0 + amp * math.sin(i * 1.8), 7.0 + step * i) for i in range(n)]


def straight(n: int = 20, step: float = 0.003) -> list[tuple[float, float]]:
    return [(50.0, 7.0 + step * i) for i in range(n)]


def test_straight_way_scores_zero():
    assert _way_excess_km(way(straight())["geometry"]) is not None
    excess, _ = _way_excess_km(way(straight())["geometry"])
    assert excess == 0.0  # sinuosity 1.0 < baseline 1.05


def test_zigzag_way_scores_positive():
    excess, rep = _way_excess_km(way(zigzag())["geometry"])
    assert excess > 0.0
    assert 49.9 < rep[0] < 50.1  # representative point is on the way


def test_closed_and_short_ways_ignored():
    loop = straight(10) + list(reversed(straight(10)))
    assert _way_excess_km(way(loop)["geometry"]) is None  # closed
    assert _way_excess_km(way(straight(2))["geometry"]) is None  # too few points
    tiny = [(50.0, 7.0), (50.0005, 7.0), (50.001, 7.0)]
    assert _way_excess_km(way(tiny)["geometry"]) is None  # < 300 m


def test_score_ways_credits_chunks_by_bbox_overlap():
    pairs = [((50.0, 7.0), (50.0, 7.06)), ((51.0, 7.0), (51.0, 7.06))]
    near_first = way(zigzag())
    near_second = way([(51.0 + p[0] - 50.0, p[1]) for p in zigzag()])
    scores = score_ways([near_first, near_second, way(straight())], pairs, pad_m=4000)
    assert scores[0] > 0 and scores[1] > 0
    # Same shape credited once to each chunk (lengths differ ~1% with latitude).
    assert abs(scores[0] - scores[1]) / scores[0] < 0.02


def test_score_ways_boundary_way_counts_for_both_neighbors():
    """Solo-query semantics under batching: a twisty cluster in the overlap band of
    two adjacent corridors must score for BOTH, not be partitioned (which would drop
    both below the probe gate)."""
    a = ((50.0, 7.00), (50.0, 7.35))
    b = ((50.0, 7.35), (50.0, 7.70))
    boundary_way = way([(50.0 + 0.004 * math.sin(i * 1.8), 7.33 + 0.003 * i) for i in range(20)])
    scores = score_ways([boundary_way], [a, b], pad_m=4000)
    assert scores[0] > 0 and scores[1] > 0
    assert scores[0] == scores[1]  # full credit to each, exactly like solo queries


def test_bbox_pads_both_axes():
    south, west, north, east = _bbox(((50.0, 7.0), (50.1, 7.2)), pad_m=4000)
    assert south < 50.0 and north > 50.1
    assert west < 7.0 and east > 7.2
    assert 0.03 < 50.0 - south < 0.05  # ~4 km in degrees latitude
