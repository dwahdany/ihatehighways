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
