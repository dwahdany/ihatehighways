"""POST /api/ride-token: fidelity-verified Google route tokens for the Navigation SDK.

The Navigation SDK follows a route token plus its stopover waypoints. We pin each
selected cut by its midpoint, ask Google for a token, and verify the returned route
actually follows every cut (sampling each cut polyline against the tokenized route).
Cuts the route skipped get their entry/exit added as extra stopovers and we retry —
up to 3 paid attempts, capped at Google's 25-intermediate limit.

Tokens are only valid for minutes: results must NEVER be cached.
"""

from __future__ import annotations

from typing import Protocol

from . import polyline_util
from .config import Settings
from .google_routes import GRoute, NoRouteError, UpstreamError, WaypointSpec
from .models import LatLng, RideTokenRequest, RideTokenResponse
from .planner import PlanError
from .polyline_util import Point

MAX_INTERMEDIATES = 25  # Routes API hard limit
MAX_ATTEMPTS = 3  # each attempt is one paid Google request
SAMPLES_PER_CUT = 9
MIN_SAMPLES_ON_ROUTE = 7

# Pin position within a cut, for global route ordering: entry < mid < exit.
_ENTRY, _MID, _EXIT = 0, 1, 2


class TokenClient(Protocol):
    async def compute_route_token(
        self,
        origin: WaypointSpec,
        destination: WaypointSpec,
        intermediates: list[Point],
    ) -> tuple[str, GRoute]: ...


def _cut_samples(encoded_polyline: str) -> list[Point]:
    """9 evenly spaced points along the cut's polyline."""
    try:
        pts = polyline_util.decode(encoded_polyline)
    except Exception:
        pts = []
    if len(pts) < 2:
        raise PlanError(
            "INVALID_INPUT", "Cut polylines must decode to at least two points.", 400
        )
    return [
        polyline_util.point_at_fraction(pts, i / (SAMPLES_PER_CUT - 1))
        for i in range(SAMPLES_PER_CUT)
    ]


def _cut_followed(samples: list[Point], route_pts: list[Point], tolerance_m: float) -> bool:
    """>= 7 of 9 samples within tolerance of the returned route. Distance is measured
    to the route's SEGMENTS — straight roads carry no interior vertices."""
    if len(route_pts) < 2:
        return False
    on_route = sum(
        1 for s in samples if polyline_util.point_to_path_m(s, route_pts) <= tolerance_m
    )
    return on_route >= MIN_SAMPLES_ON_ROUTE


async def build_ride_token(
    req: RideTokenRequest, client: TokenClient, settings: Settings
) -> RideTokenResponse:
    origin = WaypointSpec(lat_lng=(req.origin.lat, req.origin.lng))
    destination = WaypointSpec(lat_lng=(req.destination.lat, req.destination.lng))
    samples = [_cut_samples(c.encoded_polyline) for c in req.cuts]

    def pin_point(cut_index: int, position: int) -> Point:
        cut = req.cuts[cut_index]
        ll = (cut.entry, cut.mid, cut.exit)[position]
        return (ll.lat, ll.lng)

    # Pins as (cut_index, position) keys: sorting keeps global route order because cuts
    # arrive in route order and entry < mid < exit within a cut.
    pin_keys: set[tuple[int, int]] = {(i, _MID) for i in range(len(req.cuts))}
    pins = [pin_point(i, pos) for i, pos in sorted(pin_keys)]

    token = ""
    route: GRoute | None = None
    followed: list[bool] = [False] * len(req.cuts)
    for attempt in range(MAX_ATTEMPTS):
        try:
            token, route = await client.compute_route_token(origin, destination, pins)
        except NoRouteError:
            raise PlanError("NO_ROUTE", "No route found between origin and destination.", 400)
        except UpstreamError as exc:
            if exc.status == 400:
                raise PlanError(
                    "GEOCODE_FAILED",
                    f"Could not resolve origin or destination: {exc.message}",
                    400,
                )
            raise PlanError("UPSTREAM", exc.message, 502)
        route_pts = polyline_util.decode(route.encoded_polyline)
        followed = [
            _cut_followed(s, route_pts, settings.token_fidelity_m) for s in samples
        ]
        if all(followed) or attempt == MAX_ATTEMPTS - 1:
            break
        # Pin unfollowed cuts harder: entry and exit become stopovers too. Growth is
        # per-cut so hitting Google's 25-intermediate cap still pins what fits.
        grown = set(pin_keys)
        for i, ok in enumerate(followed):
            if ok:
                continue
            candidate = grown | {(i, _ENTRY), (i, _EXIT)}
            if len(candidate) > MAX_INTERMEDIATES:
                continue
            grown = candidate
        if grown == pin_keys:
            break  # nothing left to add within the cap
        pin_keys = grown
        pins = [pin_point(i, pos) for i, pos in sorted(pin_keys)]

    assert route is not None
    # The Navigation SDK's destination list must END at the actual destination —
    # otherwise guidance stops at the last cut exit.
    waypoints = [LatLng(lat=p[0], lng=p[1]) for p in pins]
    waypoints.append(req.destination)
    return RideTokenResponse(
        route_token=token,
        encoded_polyline=route.encoded_polyline,
        duration_s=round(route.duration_s),
        distance_m=round(route.distance_m),
        waypoints=waypoints,
        cuts_followed=followed,
    )
