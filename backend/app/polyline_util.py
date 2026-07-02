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


def initial_bearing_deg(p1: Point, p2: Point) -> int:
    """Initial great-circle bearing from p1 to p2 as an int in [0, 360]. 0 = N, 90 = E."""
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(y, x))
    return int(round(bearing)) % 360
