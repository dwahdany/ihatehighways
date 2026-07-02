"""Google Maps app handoff: build a directions deep link that pins our detours.

The Maps URL API allows at most 9 waypoints. Detour entry/exit alone are NOT enough:
between two points near a motorway the Maps app would route straight back onto it, so
each detour also pins its midpoint. When over budget, midpoints of the least valuable
detours are dropped first, then whole detours (least valuable first).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from .polyline_util import Point

MAX_WAYPOINTS = 9


@dataclass(frozen=True)
class HandoffDetour:
    entry: Point
    mid: Point
    exit: Point
    value_s: float  # avoided highway seconds — the drop priority


def _fmt(p: Point) -> str:
    return f"{p[0]:.5f},{p[1]:.5f}"


def build_gmaps_url(origin: Point, destination: Point, detours: list[HandoffDetour]) -> str:
    kept = list(detours)  # in route order
    with_mid = [True] * len(kept)

    def total() -> int:
        return sum(3 if m else 2 for m in with_mid)

    by_value = sorted(range(len(kept)), key=lambda i: kept[i].value_s)
    for i in by_value:
        if total() <= MAX_WAYPOINTS:
            break
        with_mid[i] = False
    dropped: set[int] = set()
    for i in by_value:
        if total() - 2 * len(dropped) <= MAX_WAYPOINTS:
            break
        dropped.add(i)

    waypoints: list[str] = []
    for i, d in enumerate(kept):
        if i in dropped:
            continue
        waypoints.append(_fmt(d.entry))
        if with_mid[i]:
            waypoints.append(_fmt(d.mid))
        waypoints.append(_fmt(d.exit))

    params = {
        "api": "1",
        "origin": _fmt(origin),
        "destination": _fmt(destination),
        "travelmode": "driving",
    }
    if waypoints:
        params["waypoints"] = "|".join(waypoints)
    return f"https://www.google.com/maps/dir/?{urlencode(params)}"
