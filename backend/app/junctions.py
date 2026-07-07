"""Junction-aligned chunk boundaries: motorway exits are the action space.

Distance-based chunking puts cut boundaries at arbitrary mid-carriageway points, so a
detour probe whose scenic road meets the motorway at the exit just *behind* its entry
can only ride to the NEXT exit and double back (the heading-locked origin plus soft
avoidHighways resolve to "take the next exit"). Aligning boundaries to interchanges
turns that mechanism into the feature: a chunk starts just upstream of the exit it
names, so "take the next exit" IS that exit.

Interchanges come from OSM highway=motorway_junction nodes (osm.fetch_junctions)
projected onto the base route. Nodes projecting farther than junction_snap_m are
(usually) the opposite carriageway of a divided motorway and get dropped; survivors
within junction_cluster_m collapse into one interchange, capped at 2x that span so
dense urban exit sequences can't chain into one multi-km "interchange". Known
limitations: a very narrow median can let an opposite-direction-only exit through, and
motorway forks (Autobahnkreuze) carry junction nodes too — a boundary there is still a
real decision point, but its probe may find nothing and waste one paid call.

Chunks tile at `cut_m` points (just upstream of an interchange's first node — probe
origin, skeleton split, and client entry pin all agree there). A chunk's probe
DESTINATION overshoots its far boundary to `exit_m`, just past the interchange's last
node, so Google can re-enter at that interchange's on-ramp and still reach it. The
overshoot (bounded by the cluster cap + probe_exit_fwd_m) is ridden inside the detour
and double counted against the neighboring skeleton part — seconds of honest noise,
versus the one-exit backtrack it replaces. Because of it, ADJACENT chunks' detours
overlap and cannot coexist in one ride; planner.py keeps a one-chunk separator between
launched spans and between fallback spans.

Stretches without junction data (Overpass down, no OSM coverage) fall back to the old
distance-based splitting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import classify, polyline_util
from .classify import Chunk
from .config import Settings
from .google_routes import GStep
from .polyline_util import Point

_MIN_CHUNK_M = 2_500.0  # boundaries closer than this to an edge or each other are noise


@dataclass(frozen=True)
class Boundary:
    """One kept interchange along a stretch polyline (arc lengths in meters)."""

    cut_m: float  # chunk tiling point / probe origin: upstream of the first node
    exit_m: float  # probe destination of the chunk ENDING here: past the last node


def stretch_polyline(steps: Sequence[GStep], start: int, end: int) -> list[Point]:
    """Concatenated step polylines of steps [start, end), consecutive dups dropped."""
    pts: list[Point] = []
    for i in range(start, end):
        step_pts = (
            polyline_util.decode(steps[i].encoded_polyline)
            if steps[i].encoded_polyline
            else [steps[i].start, steps[i].end]
        )
        for p in step_pts:
            if not pts or pts[-1] != p:
                pts.append(p)
    return pts


def _interchanges(
    poly: Sequence[Point], nodes: Sequence[Point], settings: Settings
) -> list[tuple[float, float]]:
    """(lo_m, hi_m) arc-length clusters of junction nodes on this carriageway.

    Cluster spans are capped at 2x junction_cluster_m: without the cap, urban
    motorways with exits every ~800 m chain transitively into one multi-km
    "interchange" whose exit overshoot swallows the whole next chunk.
    """
    hits: list[float] = []
    for node in nodes:
        arclen, dist = polyline_util.project_arclen_m(node, poly)
        if dist <= settings.junction_snap_m:
            hits.append(arclen)
    hits.sort()
    clusters: list[list[float]] = []
    for m in hits:
        if (
            clusters
            and m - clusters[-1][1] <= settings.junction_cluster_m
            and m - clusters[-1][0] <= 2 * settings.junction_cluster_m
        ):
            clusters[-1][1] = m
        else:
            clusters.append([m, m])
    return [(lo, hi) for lo, hi in clusters]


def pick_boundaries(
    total_m: float,
    interchanges: Sequence[tuple[float, float]],
    settings: Settings,
    target_m: float,
) -> list[Boundary]:
    """Keep interchanges so chunks stay NEAR (at most, exits permitting) target_m.

    Greedy: cut at the last viable exit before the chunk would exceed target_m, so the
    target is a ceiling, not a floor — chunks only exceed it where consecutive exits
    are farther apart than the target itself. The ceiling matters for /api/plan, where
    target_m is the budget-derived affordable detour size: a floor would make every
    cut cost more than the budget and the knapsack would select nothing.
    """
    viable: list[tuple[float, float]] = []
    for lo, hi in interchanges:
        cut = lo - settings.probe_entry_back_m
        if cut < _MIN_CHUNK_M or total_m - cut < _MIN_CHUNK_M:
            continue
        if viable and cut - viable[-1][0] < _MIN_CHUNK_M:
            continue
        viable.append((cut, hi))
    picked: list[tuple[float, float]] = []
    last = 0.0
    for k, (cut, hi) in enumerate(viable):
        reach = viable[k + 1][0] if k + 1 < len(viable) else total_m
        if reach - last > target_m:
            picked.append((cut, hi))
            last = cut
    out: list[Boundary] = []
    for i, (cut, hi) in enumerate(picked):
        next_cut = picked[i + 1][0] if i + 1 < len(picked) else total_m
        out.append(
            Boundary(cut_m=cut, exit_m=min(hi + settings.probe_exit_fwd_m, next_cut, total_m))
        )
    return out


def _make_chunk(
    steps: Sequence[GStep],
    factors: Sequence[float],
    sid: int,
    s: int,
    e: int,
    exit_: Point,
) -> Chunk:
    return Chunk(
        stretch_id=sid,
        step_start=s,
        step_end=e,
        distance_m=sum(steps[i].distance_m for i in range(s, e)),
        static_duration_s=sum(steps[i].static_duration_s for i in range(s, e)),
        baseline_s=sum(steps[i].static_duration_s * factors[i] for i in range(s, e)),
        entry=steps[s].start,
        exit=exit_,
        entry_heading=classify._entry_heading(steps[s]),
    )


def align(
    steps: Sequence[GStep],
    factors: Sequence[float],
    flags: Sequence[bool],
    stretches: Sequence[tuple[int, int]],
    nodes_per_stretch: Sequence[Sequence[Point] | None],
    settings: Settings,
    target_m: float,
    fallback_chunk_m: float,
    max_chunks: int,
) -> tuple[list[GStep], list[float], list[bool], list[Chunk]]:
    """Rebuild the step list with boundaries at interchanges and chunk the stretches.

    Returns (steps, factors, flags, chunks); stat sums and concatenated geometry are
    preserved (steps are only sliced). Stretches whose nodes entry is None or that
    yield no usable boundary fall back to distance-based splitting. Mirrors
    classify.build_chunks' cap semantics: the target enlarges x1.25 until the chunk
    count fits max_chunks (when even one part per stretch could fit), then the
    longest chunks are kept.
    """
    # Interchanges are target-independent; compute them once per stretch.
    ics_by_sid: dict[int, tuple[list[Point], list[tuple[float, float]], float]] = {}
    for sid, (a, b) in enumerate(stretches):
        nodes = nodes_per_stretch[sid] if sid < len(nodes_per_stretch) else None
        if nodes is None:
            continue
        poly = stretch_polyline(steps, a, b)
        ics = _interchanges(poly, nodes, settings)
        if ics:
            ics_by_sid[sid] = (poly, ics, polyline_util.path_length_m(poly))

    def build(cur_target_m: float) -> tuple[list[GStep], list[float], list[bool], list[Chunk]]:
        plans: dict[int, tuple[list[Point], list[Boundary]]] = {}
        for sid, (poly, ics, total) in ics_by_sid.items():
            bounds = pick_boundaries(total, ics, settings, cur_target_m)
            if bounds:
                plans[sid] = (poly, bounds)

        # Rebuild the step list, slicing steps where a cut_m falls inside them.
        new_steps: list[GStep] = []
        new_factors: list[float] = []
        new_flags: list[bool] = []
        stretch_new: dict[int, tuple[int, int]] = {}
        cut_index: dict[tuple[int, int], int] = {}  # (sid, boundary idx) -> new index
        stretch_at = {a: (sid, b) for sid, (a, b) in enumerate(stretches)}

        def copy(i: int) -> None:
            new_steps.append(steps[i])
            new_factors.append(factors[i])
            new_flags.append(flags[i])

        i = 0
        n = len(steps)
        while i < n:
            hit = stretch_at.get(i)
            if hit is None:
                copy(i)
                i += 1
                continue
            sid, b = hit
            a = i
            start_new = len(new_steps)
            plan = plans.get(sid)
            if plan is None:
                for j in range(a, b):
                    copy(j)
            else:
                _poly, bounds = plan
                cum = 0.0
                bi = 0
                for j in range(a, b):
                    pts = (
                        polyline_util.decode(steps[j].encoded_polyline)
                        if steps[j].encoded_polyline
                        else [steps[j].start, steps[j].end]
                    )
                    step_len = polyline_util.path_length_m(pts)
                    cuts: list[float] = []
                    cut_bis: list[int] = []
                    while bi < len(bounds) and bounds[bi].cut_m < cum + step_len - 1.0:
                        off = bounds[bi].cut_m - cum
                        if off <= 1.0:  # boundary lands at this step's start
                            cut_index[(sid, bi)] = len(new_steps)
                        else:
                            cuts.append(off)
                            cut_bis.append(bi)
                        bi += 1
                    pieces = classify.split_step_at(steps[j], cuts) if cuts else [steps[j]]
                    if len(pieces) != len(cuts) + 1:  # degenerate polyline: keep unsplit
                        pieces = [steps[j]]
                        for k in cut_bis:
                            cut_index[(sid, k)] = len(new_steps)
                        cut_bis = []
                    for k, piece in enumerate(pieces):
                        if k >= 1:
                            cut_index[(sid, cut_bis[k - 1])] = len(new_steps)
                        new_steps.append(piece)
                        new_factors.append(factors[j])
                        new_flags.append(flags[j])
                    cum += step_len
                # Defensive only: pick_boundaries keeps boundaries _MIN_CHUNK_M clear
                # of the stretch end, so nothing should remain here.
                while bi < len(bounds):
                    cut_index[(sid, bi)] = len(new_steps) - 1
                    bi += 1
            stretch_new[sid] = (start_new, len(new_steps))
            i = b

        # Chunk each stretch: junction bounds where planned, distance split otherwise.
        chunks: list[Chunk] = []
        for sid in range(len(stretches)):
            na, nb = stretch_new[sid]
            plan = plans.get(sid)
            if plan is None:
                for s, e in classify._split_stretch(new_steps, na, nb, fallback_chunk_m):
                    chunks.append(
                        _make_chunk(new_steps, new_factors, sid, s, e, new_steps[e - 1].end)
                    )
                continue
            poly, bounds = plan
            edges = [na] + [cut_index[(sid, k)] for k in range(len(bounds))] + [nb]
            for k in range(len(edges) - 1):
                s, e = edges[k], edges[k + 1]
                if s >= e:  # degenerate: boundary collapsed onto a stretch edge
                    continue
                if k < len(bounds):
                    exit_ = polyline_util.point_at_distance_m(poly, bounds[k].exit_m)
                else:
                    exit_ = new_steps[e - 1].end
                chunks.append(_make_chunk(new_steps, new_factors, sid, s, e, exit_))
        return new_steps, new_factors, new_flags, chunks

    result = build(target_m)
    if len(stretches) <= max_chunks:
        cur = target_m
        for _ in range(40):
            if len(result[3]) <= max_chunks:
                break
            cur *= 1.25
            result = build(cur)
    new_steps, new_factors, new_flags, chunks = result
    if len(chunks) > max_chunks:  # keep the longest, like classify.build_chunks
        chunks = sorted(chunks, key=lambda c: c.distance_m, reverse=True)[:max_chunks]
        chunks.sort(key=lambda c: (c.stretch_id, c.step_start))
    return new_steps, new_factors, new_flags, chunks
