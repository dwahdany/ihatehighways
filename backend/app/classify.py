"""Step classification, highway stretches, and chunking (docs/algorithm.md steps 2-3).

The Routes API exposes no road-class field, so highway detection is a speed + name
heuristic over the free-text navigation instructions, with a maneuver hint for ramps and
gap bridging for short interruptions (interchanges, service areas).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from . import polyline_util
from .config import Settings
from .google_routes import GStep
from .polyline_util import Point

MOTORWAY_RE = re.compile(
    r"\b(A ?\d{1,3}|E ?\d{1,3}|M\d{1,2}|I-\d{1,3}|Autobahn|motorway|freeway|interstate"
    r"|expressway|autoroute|autostrada|autopista)\b",
    re.IGNORECASE,
)

RAMP_MANEUVERS = {"MERGE", "RAMP_LEFT", "RAMP_RIGHT"}


def _lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def atomize_steps(
    steps: Sequence[GStep], factors: Sequence[float], atom_m: float
) -> tuple[list[GStep], list[float]]:
    """Split steps much longer than atom_m into equal sub-steps along their polylines.

    Google returns long motorway hauls as single steps (observed: one 167 km "Continue
    on A3" step Cologne->Frankfurt), and chunking only cuts at step boundaries — without
    this pass such a stretch is take-it-or-leave-it. Distance and static duration are
    distributed equally across atoms; geometry is sliced from the step polyline with
    interpolated cut points, so concatenating atom polylines reproduces the step.
    """
    out_steps: list[GStep] = []
    out_factors: list[float] = []
    for step, factor in zip(steps, factors):
        pts = polyline_util.decode(step.encoded_polyline) if step.encoded_polyline else []
        total = polyline_util.path_length_m(pts) if len(pts) >= 2 else 0.0
        if step.distance_m <= atom_m * 1.5 or total <= 0:
            out_steps.append(step)
            out_factors.append(factor)
            continue
        n = max(2, round(step.distance_m / atom_m))
        target = total / n
        slices: list[list[Point]] = []
        cur: list[Point] = [pts[0]]
        acc = 0.0
        a = pts[0]
        idx = 1
        while idx < len(pts):
            b = pts[idx]
            seg = polyline_util.haversine_m(a, b)
            if len(slices) < n - 1 and acc + seg >= target and target > 0:
                t = (target - acc) / seg if seg > 0 else 1.0
                cut = _lerp(a, b, t)
                cur.append(cut)
                slices.append(cur)
                cur = [cut]
                acc = 0.0
                a = cut
            else:
                cur.append(b)
                acc += seg
                a = b
                idx += 1
        slices.append(cur)
        frac = 1.0 / len(slices)
        for i, slice_pts in enumerate(slices):
            out_steps.append(
                GStep(
                    distance_m=step.distance_m * frac,
                    static_duration_s=step.static_duration_s * frac,
                    encoded_polyline=polyline_util.encode(slice_pts),
                    maneuver=step.maneuver if i == 0 else "",
                    instructions=step.instructions,
                    start=slice_pts[0],
                    end=slice_pts[-1],
                )
            )
            out_factors.append(factor)
    return out_steps, out_factors


def step_speed_kmh(step: GStep) -> float:
    """Average static speed of a step in km/h."""
    if step.static_duration_s <= 0:
        return float("inf") if step.distance_m > 0 else 0.0
    return (step.distance_m / step.static_duration_s) * 3.6


def is_highway_step(step: GStep, settings: Settings) -> bool:
    """Base classification: speed threshold, or lower threshold + motorway-name regex."""
    speed = step_speed_kmh(step)
    if speed >= settings.highway_fast_kmh and step.distance_m >= settings.min_step_m:
        return True
    if speed >= settings.highway_named_kmh and MOTORWAY_RE.search(step.instructions or ""):
        return True
    return False


def classify_steps(steps: Sequence[GStep], settings: Settings) -> list[bool]:
    """Classify each step as highway, with maneuver hint and gap bridging applied."""
    flags = [is_highway_step(s, settings) for s in steps]

    # Maneuver hint: MERGE/RAMP_* steps belong to the stretch when the following step is
    # highway. Backwards pass so ramp chains leading into a motorway are absorbed too.
    for i in range(len(steps) - 2, -1, -1):
        if not flags[i] and steps[i].maneuver in RAMP_MANEUVERS and flags[i + 1]:
            flags[i] = True

    # Gap bridging: non-highway runs shorter than gap_bridge_m sandwiched between highway
    # steps are absorbed into the stretch.
    prev_true: int | None = None
    for i, flag in enumerate(flags):
        if not flag:
            continue
        if prev_true is not None and i - prev_true > 1:
            gap_m = sum(steps[j].distance_m for j in range(prev_true + 1, i))
            if gap_m < settings.gap_bridge_m:
                for j in range(prev_true + 1, i):
                    flags[j] = True
        prev_true = i
    return flags


def find_stretches(
    steps: Sequence[GStep], flags: Sequence[bool], settings: Settings
) -> list[tuple[int, int]]:
    """Contiguous highway runs >= min_stretch_km, as [start, end) step index pairs."""
    stretches: list[tuple[int, int]] = []
    i = 0
    n = len(steps)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < n and flags[j]:
            j += 1
        if sum(steps[k].distance_m for k in range(i, j)) >= settings.min_stretch_km * 1000:
            stretches.append((i, j))
        i = j
    return stretches


@dataclass(frozen=True)
class Chunk:
    stretch_id: int
    step_start: int  # inclusive
    step_end: int  # exclusive
    distance_m: float
    static_duration_s: float
    baseline_s: float  # static durations scaled by the leg traffic factor
    entry: Point
    exit: Point
    entry_heading: int  # bearing of the base polyline at the entry point


def _split_stretch(
    steps: Sequence[GStep], start: int, end: int, max_m: float
) -> list[tuple[int, int]]:
    """Greedy split at step boundaries into pieces <= max_m (single steps may exceed)."""
    bounds: list[tuple[int, int]] = []
    cur_start = start
    cur_m = 0.0
    for i in range(start, end):
        d = steps[i].distance_m
        if i > cur_start and cur_m + d > max_m:
            bounds.append((cur_start, i))
            cur_start = i
            cur_m = 0.0
        cur_m += d
    bounds.append((cur_start, end))
    return bounds


def _entry_heading(step: GStep) -> int:
    pts = polyline_util.decode(step.encoded_polyline) if step.encoded_polyline else []
    if len(pts) >= 2:
        return polyline_util.initial_bearing_deg(pts[0], pts[1])
    return polyline_util.initial_bearing_deg(step.start, step.end)


def chunk_size_m(settings: Settings, budget_s: float | None) -> float:
    """Budget-adaptive chunk size.

    Replacing one highway km with country roads costs roughly detour_extra_per_hw_km_s
    extra seconds, so a chunk of budget/that many km is the largest single detour the
    rider can afford. Sizing chunks near that keeps long stretches escapable on small
    budgets (a 45 km chunk costs ~+25 min — nothing fits a 15-min budget), clamped to
    [min_chunk_km, max_chunk_km].
    """
    chunk_km = settings.max_chunk_km
    if budget_s is not None and settings.detour_extra_per_hw_km_s > 0:
        target_km = budget_s / settings.detour_extra_per_hw_km_s
        chunk_km = min(settings.max_chunk_km, max(settings.min_chunk_km, target_km))
    return chunk_km * 1000


def road_name(steps: Sequence[GStep], start: int, end: int) -> str | None:
    """Most common motorway name in the instruction texts of steps [start, end)."""
    counts: dict[str, int] = {}
    for i in range(start, end):
        m = MOTORWAY_RE.search(steps[i].instructions or "")
        if m:
            name = m.group(0).replace(" ", "").upper()
            counts[name] = counts.get(name, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


def build_chunks(
    steps: Sequence[GStep],
    flags: Sequence[bool],
    factors: Sequence[float],
    settings: Settings,
    budget_s: float | None = None,
    chunk_km: float | None = None,
) -> list[Chunk]:
    """Split highway stretches into detour-candidate chunks.

    Chunks are budget-adaptively sized (<= max_chunk_km) and adaptively enlarged so the
    total count stays <= max_chunks (they are the unit of optimization and each costs
    one API call).
    """
    stretches = find_stretches(steps, flags, settings)
    if not stretches:
        return []

    def split_all(max_m: float) -> list[tuple[int, int, int]]:
        parts: list[tuple[int, int, int]] = []
        for sid, (a, b) in enumerate(stretches):
            parts.extend((sid, s, e) for s, e in _split_stretch(steps, a, b, max_m))
        return parts

    chunk_m = chunk_km * 1000 if chunk_km is not None else chunk_size_m(settings, budget_s)
    parts = split_all(chunk_m)
    for _ in range(40):
        if len(parts) <= settings.max_chunks:
            break
        chunk_m *= 1.25
        parts = split_all(chunk_m)
    if len(parts) > settings.max_chunks:
        # More stretches than allowed chunks even at one chunk per stretch:
        # keep the longest ones.
        def part_dist(p: tuple[int, int, int]) -> float:
            return sum(steps[i].distance_m for i in range(p[1], p[2]))

        parts = sorted(parts, key=part_dist, reverse=True)[: settings.max_chunks]
        parts.sort(key=lambda p: p[1])

    chunks: list[Chunk] = []
    for sid, s, e in parts:
        dist = sum(steps[i].distance_m for i in range(s, e))
        static = sum(steps[i].static_duration_s for i in range(s, e))
        baseline = sum(steps[i].static_duration_s * factors[i] for i in range(s, e))
        chunks.append(
            Chunk(
                stretch_id=sid,
                step_start=s,
                step_end=e,
                distance_m=dist,
                static_duration_s=static,
                baseline_s=baseline,
                entry=steps[s].start,
                exit=steps[e - 1].end,
                entry_heading=_entry_heading(steps[s]),
            )
        )
    return chunks
