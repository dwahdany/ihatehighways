"""Pydantic models mirroring docs/api.md exactly (snake_case JSON)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class LatLng(BaseModel):
    lat: float
    lng: float


class PlacePoint(BaseModel):
    """Origin/destination: exactly one of place_id, address, lat_lng."""

    place_id: str | None = None
    address: str | None = None
    lat_lng: LatLng | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "PlacePoint":
        given = sum(v is not None for v in (self.place_id, self.address, self.lat_lng))
        if given != 1:
            raise ValueError("exactly one of place_id, address, lat_lng is required")
        return self


class PlanRequest(BaseModel):
    origin: PlacePoint
    destination: PlacePoint
    # strict=True: reject bools ({"max_extra_minutes": true} would otherwise
    # coerce to 1 minute) and numeric strings ("15"); docs/api.md says int 0-120.
    max_extra_minutes: int = Field(..., ge=0, le=120, strict=True)


class RouteSummary(BaseModel):
    """The `fastest` object in the response."""

    encoded_polyline: str
    duration_s: int
    static_duration_s: int
    distance_m: int
    highway_distance_m: int
    highway_duration_s: int


class Segment(BaseModel):
    kind: Literal["kept", "highway", "detour"]
    encoded_polyline: str
    duration_s: int
    distance_m: int


class Ride(BaseModel):
    duration_s: int
    extra_duration_s: int
    distance_m: int
    highway_distance_m: int
    highway_duration_s: int
    segments: list[Segment]
    # Deep link that reproduces this ride in the Google Maps app (detours pinned via
    # waypoints, 9 max — least valuable detours lose fidelity first).
    gmaps_url: str


class DetourOut(BaseModel):
    entry: LatLng
    exit: LatLng
    extra_duration_s: int
    avoided_highway_s: int
    avoided_highway_m: int
    detour_distance_m: int
    curviness: float


class PlanResponse(BaseModel):
    budget_s: int
    fastest: RouteSummary
    ride: Ride
    detours: list[DetourOut]


class ScoutRequest(BaseModel):
    origin: PlacePoint
    destination: PlacePoint


class CutOut(BaseModel):
    """One toggleable highway cut: a priced country-road replacement."""

    id: str
    road: str | None  # motorway name extracted from instructions, e.g. "A3"
    entry: LatLng
    exit: LatLng
    mid: LatLng  # midpoint of the detour path, for Google Maps waypoint pinning
    encoded_polyline: str
    detour_duration_s: int
    detour_distance_m: int
    extra_duration_s: int  # vs staying on the highway; negative = jammed, cut is free
    avoided_highway_s: int
    avoided_highway_m: int
    curviness: float


class SkeletonPart(BaseModel):
    """Fastest-route piece; parts with cut_id can be swapped for that cut client-side."""

    kind: Literal["kept", "highway"]
    encoded_polyline: str
    duration_s: int
    distance_m: int
    cut_id: str | None = None


class ScoutResponse(BaseModel):
    origin: LatLng  # resolved route endpoints, for client-side Google Maps handoff
    destination: LatLng
    fastest: RouteSummary
    skeleton: list[SkeletonPart]
    cuts: list[CutOut]


class CutPin(BaseModel):
    """A selected cut as pinned for the Navigation SDK handoff (route order)."""

    entry: LatLng
    mid: LatLng
    exit: LatLng
    encoded_polyline: str = Field(..., min_length=1)


class RideTokenRequest(BaseModel):
    origin: LatLng
    destination: LatLng
    cuts: list[CutPin] = Field(default_factory=list, max_length=12)


class RideTokenResponse(BaseModel):
    route_token: str
    encoded_polyline: str
    duration_s: int
    distance_m: int
    # The FINAL stopover list in route order; the iOS app must pass exactly these
    # (with the token) to the Navigation SDK.
    waypoints: list[LatLng]
    cuts_followed: list[bool]
