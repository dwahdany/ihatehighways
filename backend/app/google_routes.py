"""Google Routes API v2 client (computeRoutes), internal dataclasses, and a deterministic mock.

The planner never touches raw Routes API JSON: everything is parsed into GRoute/GLeg/GStep
immediately. Verified against the 2026-07 Routes API v2 reference.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass

import httpx

from . import polyline_util
from .polyline_util import Point

logger = logging.getLogger("ihatehighways.google_routes")

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

# X-Goog-FieldMask is REQUIRED: comma-separated, no spaces.
FIELD_MASK = (
    "routes.duration,routes.staticDuration,routes.distanceMeters,"
    "routes.polyline.encodedPolyline,"
    "routes.legs.duration,routes.legs.staticDuration,routes.legs.distanceMeters,"
    "routes.legs.steps.distanceMeters,routes.legs.steps.staticDuration,"
    "routes.legs.steps.polyline.encodedPolyline,routes.legs.steps.navigationInstruction,"
    "routes.legs.steps.startLocation,routes.legs.steps.endLocation"
)


class UpstreamError(Exception):
    """Non-200 response or an {"error": ...} body from the Routes API."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Routes API error {status}: {message}")
        self.status = status
        self.message = message


class NoRouteError(Exception):
    """The Routes API answered successfully but returned no routes."""


def parse_duration_s(value: str | float | None) -> float:
    """Parse a Routes API duration string like "1234s" (or "1234.5s") into seconds."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = value.strip()
    if text.endswith("s"):
        text = text[:-1]
    return float(text)


@dataclass(frozen=True)
class WaypointSpec:
    """Union of place_id / address / lat_lng. Exactly one should be set."""

    place_id: str | None = None
    address: str | None = None
    lat_lng: Point | None = None

    def to_json(self, heading: int | None = None) -> dict:
        if self.place_id is not None:
            return {"placeId": self.place_id}
        if self.address is not None:
            return {"address": self.address}
        assert self.lat_lng is not None, "empty WaypointSpec"
        location: dict = {
            "latLng": {"latitude": self.lat_lng[0], "longitude": self.lat_lng[1]}
        }
        if heading is not None:
            # Heading lives INSIDE location (int 0-360, 0 = N, 90 = E).
            location["heading"] = int(heading) % 360
        return {"location": location}


@dataclass(frozen=True)
class GStep:
    distance_m: float
    static_duration_s: float  # steps only carry static durations
    encoded_polyline: str
    maneuver: str
    instructions: str
    start: Point
    end: Point


@dataclass(frozen=True)
class GLeg:
    duration_s: float  # traffic-aware
    static_duration_s: float
    distance_m: float
    steps: list[GStep]


@dataclass(frozen=True)
class GRoute:
    duration_s: float  # traffic-aware
    static_duration_s: float
    distance_m: float
    encoded_polyline: str
    legs: list[GLeg]


def _parse_latlng(obj: dict | None) -> Point | None:
    if not obj:
        return None
    ll = obj.get("latLng") or {}
    if "latitude" in ll and "longitude" in ll:
        return (float(ll["latitude"]), float(ll["longitude"]))
    return None


def _parse_step(raw: dict) -> GStep:
    encoded = (raw.get("polyline") or {}).get("encodedPolyline", "")
    nav = raw.get("navigationInstruction") or {}
    start = _parse_latlng(raw.get("startLocation"))
    end = _parse_latlng(raw.get("endLocation"))
    if start is None or end is None:
        pts = polyline_util.decode(encoded) if encoded else []
        if pts:
            start = start or pts[0]
            end = end or pts[-1]
        else:
            start = start or (0.0, 0.0)
            end = end or start
    return GStep(
        distance_m=float(raw.get("distanceMeters", 0)),
        static_duration_s=parse_duration_s(raw.get("staticDuration")),
        encoded_polyline=encoded,
        maneuver=nav.get("maneuver", ""),
        instructions=nav.get("instructions", ""),
        start=start,
        end=end,
    )


def _parse_leg(raw: dict) -> GLeg:
    steps = [_parse_step(s) for s in raw.get("steps") or []]
    return GLeg(
        duration_s=parse_duration_s(raw.get("duration")),
        static_duration_s=parse_duration_s(raw.get("staticDuration")),
        distance_m=float(raw.get("distanceMeters", 0)),
        steps=steps,
    )


def _parse_route(raw: dict) -> GRoute:
    return GRoute(
        duration_s=parse_duration_s(raw.get("duration")),
        static_duration_s=parse_duration_s(raw.get("staticDuration")),
        distance_m=float(raw.get("distanceMeters", 0)),
        encoded_polyline=(raw.get("polyline") or {}).get("encodedPolyline", ""),
        legs=[_parse_leg(l) for l in raw.get("legs") or []],
    )


class GoogleRoutesClient:
    """Async Routes API v2 client. 15 s timeout, 2 retries on timeout/5xx."""

    def __init__(self, api_key: str, *, timeout_s: float = 15.0, max_retries: int = 2):
        self._api_key = api_key
        self._max_retries = max_retries
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def compute_route(
        self,
        origin: WaypointSpec,
        destination: WaypointSpec,
        avoid_highways: bool = False,
        origin_heading: int | None = None,
    ) -> GRoute:
        routes = await self._request(origin, destination, avoid_highways, origin_heading, False)
        return routes[0]

    async def compute_route_alternatives(
        self,
        origin: WaypointSpec,
        destination: WaypointSpec,
        avoid_highways: bool = False,
        origin_heading: int | None = None,
    ) -> list[GRoute]:
        """Up to 3 route alternatives — same billable request, more candidates to score."""
        return await self._request(origin, destination, avoid_highways, origin_heading, True)

    async def _request(
        self,
        origin: WaypointSpec,
        destination: WaypointSpec,
        avoid_highways: bool,
        origin_heading: int | None,
        alternatives: bool,
    ) -> list[GRoute]:
        body = {
            "origin": origin.to_json(heading=origin_heading),
            "destination": destination.to_json(),
            # NEVER TWO_WHEELER: it is a pricier Enterprise SKU and adds nothing here.
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
            "polylineQuality": "HIGH_QUALITY",
            "routeModifiers": {"avoidHighways": bool(avoid_highways)},
            "languageCode": "en-US",
            "units": "METRIC",
            # NEVER set departureTime: omitted means "now"; a past timestamp errors.
        }
        if alternatives:
            # Not allowed with intermediates (we never send any); one billable request.
            body["computeAlternativeRoutes"] = True
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._http.post(ROUTES_URL, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise UpstreamError(504, f"Routes API timeout: {exc}") from exc
            if resp.status_code >= 500 and attempt < self._max_retries:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            return self._handle_response(resp)
        raise UpstreamError(502, "Routes API unavailable")  # pragma: no cover

    @staticmethod
    def _handle_response(resp: httpx.Response) -> list[GRoute]:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code != 200 or "error" in data:
            err = data.get("error") or {}
            message = err.get("message") or resp.text[:300] or "unknown upstream error"
            status = resp.status_code if resp.status_code != 200 else int(err.get("code") or 502)
            raise UpstreamError(status, message)
        routes = data.get("routes") or []
        if not routes:
            raise NoRouteError("Routes API returned no routes")
        return [_parse_route(r) for r in routes]


# ---------------------------------------------------------------------------
# Deterministic mock (IHH_MOCK=1)
# ---------------------------------------------------------------------------

_COLOGNE: Point = (50.94, 6.96)
_FRANKFURT: Point = (50.11, 8.68)
_COUNTRY_KMH = 60.0
_HIGHWAY_KMH = 115.0
_DETOUR_KMH = 62.0
_JAM_FACTOR = 1.25
_SAMPLE_M = 400.0  # geometry sample spacing
_BEND_DEG = 15.0  # gentle bearing oscillation amplitude
_BEND_PERIOD_M = 60_000.0
_DETOUR_OFFSET_FRACTION = 0.15  # midpoint perpendicular offset vs straight distance


@dataclass(frozen=True)
class _PlanStep:
    length_m: float
    speed_kmh: float
    instructions: str = ""
    maneuver: str = ""


def _dest_point(p: Point, bearing_deg: float, dist_m: float) -> Point:
    """Great-circle destination point (spherical earth)."""
    r = polyline_util.EARTH_RADIUS_M
    delta = dist_m / r
    theta = math.radians(bearing_deg)
    lat1, lon1 = math.radians(p[0]), math.radians(p[1])
    lat2 = math.asin(
        math.sin(lat1) * math.cos(delta) + math.cos(lat1) * math.sin(delta) * math.cos(theta)
    )
    lon2 = lon1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(lat1),
        math.cos(delta) - math.sin(lat1) * math.sin(lat2),
    )
    return (math.degrees(lat2), math.degrees(lon2))


def _mock_plan_legs() -> list[tuple[float, list[_PlanStep]]]:
    """(traffic_factor, steps) per leg for the synthetic base route (~172 km)."""
    country = [_PlanStep(2000, _COUNTRY_KMH, "Continue on K7", "TURN_SLIGHT_RIGHT")] * 5
    gap = [_PlanStep(4000, _COUNTRY_KMH, "Follow B49", "STRAIGHT")] * 2

    def stretch(n_steps: int) -> list[_PlanStep]:
        first = _PlanStep(8000, _HIGHWAY_KMH, "Merge onto A3", "MERGE")
        rest = [_PlanStep(8000, _HIGHWAY_KMH, "Continue on A3 toward Frankfurt", "STRAIGHT")]
        return [first] + rest * (n_steps - 1)

    return [
        (1.0, country + stretch(6) + gap),  # 10 km country + 48 km A3 + 8 km gap
        (_JAM_FACTOR, stretch(5)),  # 40 km A3, jammed: duration = 1.25 x static
        (1.0, gap + stretch(6) + country),  # 8 km gap + 48 km A3 + 10 km country
    ]


class MockRoutesClient:
    """Deterministic synthetic Routes client, selected when IHH_MOCK=1.

    It IGNORES the requested origin/destination for base queries: every
    avoid_highways=False call returns the same ~172 km synthetic route between fixed
    anchors, roughly Cologne (50.94, 6.96) -> Frankfurt (50.11, 8.68): ~10 km of country
    steps at 60 km/h on each end, three "A3" motorway stretches (8 km steps at 115 km/h)
    separated by 8 km country gaps, with the middle stretch jammed at the leg level
    (leg.duration = 1.25 x leg.staticDuration).

    avoid_highways=True calls return a single-step country-road route between the two
    requested points, following a slight arc (midpoint offset perpendicular by 15% of the
    straight distance) at 62 km/h - so detours are plausible and SOME (not all) fit a
    15-minute budget. Every generated number is a pure function of the inputs.
    """

    def __init__(self) -> None:
        self._base: GRoute | None = None

    async def aclose(self) -> None:  # symmetric with GoogleRoutesClient
        return None

    async def compute_route(
        self,
        origin: WaypointSpec,
        destination: WaypointSpec,
        avoid_highways: bool = False,
        origin_heading: int | None = None,
    ) -> GRoute:
        if avoid_highways:
            p1 = origin.lat_lng or _COLOGNE
            p2 = destination.lat_lng or _FRANKFURT
            return self._detour_route(p1, p2, _DETOUR_OFFSET_FRACTION, _DETOUR_KMH)
        if self._base is None:
            self._base = self._build_base()
        return self._base

    async def compute_route_alternatives(
        self,
        origin: WaypointSpec,
        destination: WaypointSpec,
        avoid_highways: bool = False,
        origin_heading: int | None = None,
    ) -> list[GRoute]:
        """Two deterministic alternatives: the standard arc and a curvier, slower one."""
        p1 = origin.lat_lng or _COLOGNE
        p2 = destination.lat_lng or _FRANKFURT
        if not avoid_highways:
            return [await self.compute_route(origin, destination, False, origin_heading)]
        return [
            self._detour_route(p1, p2, _DETOUR_OFFSET_FRACTION, _DETOUR_KMH),
            self._detour_route(p1, p2, _DETOUR_OFFSET_FRACTION * 2, _DETOUR_KMH - 7),
        ]

    @staticmethod
    def _build_base() -> GRoute:
        bearing0 = float(polyline_util.initial_bearing_deg(_COLOGNE, _FRANKFURT))
        cur = _COLOGNE
        d_done = 0.0
        legs: list[GLeg] = []
        all_pts: list[Point] = [cur]
        for factor, plan in _mock_plan_legs():
            gsteps: list[GStep] = []
            for ps in plan:
                pts: list[Point] = [cur]
                n = max(1, math.ceil(ps.length_m / _SAMPLE_M))
                seg = ps.length_m / n
                for k in range(n):
                    heading = bearing0 + _BEND_DEG * math.sin(
                        2 * math.pi * (d_done + seg * (k + 0.5)) / _BEND_PERIOD_M
                    )
                    cur = _dest_point(cur, heading, seg)
                    pts.append(cur)
                d_done += ps.length_m
                static = ps.length_m / (ps.speed_kmh / 3.6)
                gsteps.append(
                    GStep(
                        distance_m=float(ps.length_m),
                        static_duration_s=static,
                        encoded_polyline=polyline_util.encode(pts),
                        maneuver=ps.maneuver,
                        instructions=ps.instructions,
                        start=pts[0],
                        end=pts[-1],
                    )
                )
                all_pts.extend(pts[1:])
            leg_static = sum(s.static_duration_s for s in gsteps)
            leg_dist = sum(s.distance_m for s in gsteps)
            legs.append(GLeg(leg_static * factor, leg_static, leg_dist, gsteps))
        return GRoute(
            duration_s=sum(l.duration_s for l in legs),
            static_duration_s=sum(l.static_duration_s for l in legs),
            distance_m=sum(l.distance_m for l in legs),
            encoded_polyline=polyline_util.encode(all_pts),
            legs=legs,
        )

    @staticmethod
    def _detour_route(
        p1: Point, p2: Point, offset_fraction: float = _DETOUR_OFFSET_FRACTION, kmh: float = _DETOUR_KMH
    ) -> GRoute:
        dist = polyline_util.haversine_m(p1, p2)
        if dist < 1.0:
            pts = [p1, p2]
        else:
            mid_lat = math.radians((p1[0] + p2[0]) / 2)
            kx = 111_320.0 * math.cos(mid_lat)  # m per degree of longitude
            ky = 110_540.0  # m per degree of latitude
            x2 = (p2[1] - p1[1]) * kx
            y2 = (p2[0] - p1[0]) * ky
            norm = math.hypot(x2, y2) or 1.0
            nx, ny = -y2 / norm, x2 / norm  # unit perpendicular (left of travel)
            # Quadratic bezier control point so the curve midpoint sits at
            # chord midpoint + offset_fraction of distance, perpendicular.
            cx = x2 / 2 + 2 * offset_fraction * dist * nx
            cy = y2 / 2 + 2 * offset_fraction * dist * ny
            pts = []
            n = 24
            for i in range(n + 1):
                t = i / n
                bx = 2 * t * (1 - t) * cx + t * t * x2
                by = 2 * t * (1 - t) * cy + t * t * y2
                pts.append((p1[0] + by / ky, p1[1] + bx / kx))
        length = polyline_util.path_length_m(pts)
        static = length / (kmh / 3.6)
        encoded = polyline_util.encode(pts)
        step = GStep(
            distance_m=round(length),
            static_duration_s=static,
            encoded_polyline=encoded,
            maneuver="DEPART",
            instructions="Follow the country roads",
            start=pts[0],
            end=pts[-1],
        )
        leg = GLeg(static, static, round(length), [step])
        return GRoute(static, static, round(length), encoded, [leg])
