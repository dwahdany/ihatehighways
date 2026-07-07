"""Thin wrappers over the `polyline` package (precision 5) plus geo helpers.

Points are (lat, lng) tuples in degrees.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import polyline as _polyline

Point = tuple[float, float]

PRECISION = 5
EARTH_RADIUS_M = 6_371_008.8


def decode(encoded: str) -> list[Point]:
    """Decode a Google encoded polyline (precision 5) into (lat, lng) tuples."""
    return [(float(lat), float(lng)) for lat, lng in _polyline.decode(encoded, PRECISION)]


def encode(points: Iterable[Point]) -> str:
    """Encode (lat, lng) tuples into a Google encoded polyline (precision 5)."""
    return _polyline.encode(list(points), PRECISION)


def haversine_m(p1: Point, p2: Point) -> float:
    """Great-circle distance between two (lat, lng) points, in meters."""
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def path_length_m(points: Sequence[Point]) -> float:
    """Length of a polyline (sum of consecutive haversine distances), in meters."""
    return sum(haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))


def point_at_fraction(points: Sequence[Point], fraction: float) -> Point:
    """The point at `fraction` (0..1) of the path length, interpolated on the polyline."""
    if not points:
        raise ValueError("empty path")
    if len(points) == 1 or fraction <= 0:
        return points[0]
    if fraction >= 1:
        return points[-1]
    target = path_length_m(points) * fraction
    acc = 0.0
    for a, b in zip(points, points[1:]):
        seg = haversine_m(a, b)
        if acc + seg >= target and seg > 0:
            t = (target - acc) / seg
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        acc += seg
    return points[-1]


def point_at_distance_m(points: Sequence[Point], distance_m: float) -> Point:
    """The point at `distance_m` along the path, interpolated on the polyline."""
    if not points:
        raise ValueError("empty path")
    if len(points) == 1 or distance_m <= 0:
        return points[0]
    acc = 0.0
    for a, b in zip(points, points[1:]):
        seg = haversine_m(a, b)
        if acc + seg >= distance_m and seg > 0:
            t = (distance_m - acc) / seg
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        acc += seg
    return points[-1]


def bearing_at_distance_m(points: Sequence[Point], distance_m: float) -> int:
    """Bearing of the polyline segment containing the point at `distance_m`."""
    if len(points) < 2:
        return 0
    acc = 0.0
    for a, b in zip(points, points[1:]):
        seg = haversine_m(a, b)
        if seg > 0 and acc + seg >= distance_m:
            return initial_bearing_deg(a, b)
        acc += seg
    for a, b in zip(reversed(points[:-1]), reversed(points[1:])):
        if haversine_m(a, b) > 0:
            return initial_bearing_deg(a, b)
    return 0


def project_arclen_m(p: Point, pts: Sequence[Point]) -> tuple[float, float]:
    """(arc length at the closest point on the path, distance to it), in meters.

    Same segment-wise equirectangular projection as point_to_path_m; arc lengths
    accumulate haversine segment lengths so they are comparable to path_length_m.
    """
    if not pts:
        return 0.0, float("inf")
    if len(pts) == 1:
        return 0.0, haversine_m(p, pts[0])
    kx = 111_320.0 * math.cos(math.radians(p[0]))
    ky = 110_540.0
    best_d2 = float("inf")
    best_m = 0.0
    acc = 0.0
    ax = (pts[0][1] - p[1]) * kx
    ay = (pts[0][0] - p[0]) * ky
    prev = pts[0]
    for q in pts[1:]:
        bx = (q[1] - p[1]) * kx
        by = (q[0] - p[0]) * ky
        dx, dy = bx - ax, by - ay
        seg_len2 = dx * dx + dy * dy
        seg_m = haversine_m(prev, q)
        if seg_len2 <= 1e-9:
            t = 0.0
            d2 = ax * ax + ay * ay
        else:
            t = max(0.0, min(1.0, -(ax * dx + ay * dy) / seg_len2))
            cx, cy = ax + t * dx, ay + t * dy
            d2 = cx * cx + cy * cy
        if d2 < best_d2:
            best_d2 = d2
            best_m = acc + t * seg_m
        acc += seg_m
        ax, ay = bx, by
        prev = q
    return best_m, math.sqrt(best_d2)


def point_to_path_m(p: Point, pts: Sequence[Point]) -> float:
    """Min distance from p to a polyline's SEGMENTS (not just vertices).

    Vertex-only distance breaks on straight roads: encoded polylines put no interior
    vertices on straight geometry, so a point mid-segment can sit "far" from every
    vertex while lying exactly on the path. Equirectangular projection around p is
    accurate at the sub-km scales this is used for.
    """
    if not pts:
        return float("inf")
    if len(pts) == 1:
        return haversine_m(p, pts[0])
    kx = 111_320.0 * math.cos(math.radians(p[0]))
    ky = 110_540.0
    best = float("inf")
    ax = (pts[0][1] - p[1]) * kx
    ay = (pts[0][0] - p[0]) * ky
    for q in pts[1:]:
        bx = (q[1] - p[1]) * kx
        by = (q[0] - p[0]) * ky
        dx, dy = bx - ax, by - ay
        seg_len2 = dx * dx + dy * dy
        if seg_len2 <= 1e-9:
            d2 = ax * ax + ay * ay
        else:
            t = max(0.0, min(1.0, -(ax * dx + ay * dy) / seg_len2))
            cx, cy = ax + t * dx, ay + t * dy
            d2 = cx * cx + cy * cy
        if d2 < best:
            best = d2
        ax, ay = bx, by
    return math.sqrt(best)


def initial_bearing_deg(p1: Point, p2: Point) -> int:
    """Initial great-circle bearing from p1 to p2 as an int in [0, 360]. 0 = N, 90 = E."""
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(y, x))
    return int(round(bearing)) % 360
