import math

from app import junctions, polyline_util
from app.classify import build_chunks, classify_steps, find_stretches, split_step_at
from app.config import Settings
from tests.test_classify import make_run, make_step

SETTINGS = Settings(_env_file=None)

M_PER_DEG_LAT = 111_195.0
# Degrees longitude per meter at lat 50 (matches the equirectangular projection).
M_PER_DEG_LNG_50 = 111_320.0 * math.cos(math.radians(50.0))


def motorway_run(n_steps: int = 10, step_m: float = 5_000):
    """One northward motorway stretch starting at (50, 7)."""
    return make_run([(step_m, 115, "Continue on A3", "")] * n_steps)


def node_at(arclen_m: float, east_m: float = 8.0):
    """A junction node `arclen_m` up the stretch, offset east off the carriageway."""
    return (50.0 + arclen_m / M_PER_DEG_LAT, 7.0 + east_m / M_PER_DEG_LNG_50)


def run_align(steps, nodes, target_m=6_000, settings=SETTINGS, max_chunks=48):
    flags = classify_steps(steps, settings)
    stretches = find_stretches(steps, flags, settings)
    return junctions.align(
        steps,
        [1.0] * len(steps),
        flags,
        stretches,
        nodes,
        settings,
        target_m=target_m,
        fallback_chunk_m=25_000,
        max_chunks=max_chunks,
    )


def test_split_step_at_preserves_sums_and_geometry():
    step = make_step(9_000, 115, instructions="Continue on A3", maneuver="MERGE")
    pieces = split_step_at(step, [2_500, 6_000])
    assert len(pieces) == 3
    assert math.isclose(sum(p.distance_m for p in pieces), step.distance_m, rel_tol=1e-6)
    assert math.isclose(
        sum(p.static_duration_s for p in pieces), step.static_duration_s, rel_tol=1e-6
    )
    assert pieces[0].maneuver == "MERGE" and pieces[1].maneuver == ""
    assert pieces[0].start == step.start
    assert polyline_util.haversine_m(pieces[-1].end, step.end) < 2
    for a, b in zip(pieces, pieces[1:]):
        assert polyline_util.haversine_m(a.end, b.start) < 1
    assert abs(pieces[0].distance_m - 2_500) < 30


def test_split_step_at_ignores_degenerate_offsets():
    step = make_step(5_000, 115)
    assert split_step_at(step, [0.0, 5_000.0]) == [step]
    assert split_step_at(step, []) == [step]


def test_align_tiles_chunks_at_interchanges():
    steps = motorway_run()  # 50 km
    nodes = [[node_at(12_000), node_at(30_000)]]
    new_steps, factors, flags, chunks = run_align(steps, nodes)

    assert len(chunks) == 3
    # Tiling: contiguous step ranges covering the stretch, stats preserved.
    assert chunks[0].step_start == 0
    assert chunks[-1].step_end == len(new_steps)
    for a, b in zip(chunks, chunks[1:]):
        assert a.step_end == b.step_start
    assert math.isclose(sum(c.distance_m for c in chunks), 50_000, rel_tol=1e-4)
    assert math.isclose(sum(s.distance_m for s in new_steps), 50_000, rel_tol=1e-6)
    assert all(flags)
    assert all(f == 1.0 for f in factors)

    # Boundaries sit probe_entry_back_m upstream of each junction node.
    entry1_m = 12_000 - SETTINGS.probe_entry_back_m
    assert abs((chunks[1].entry[0] - 50.0) * M_PER_DEG_LAT - entry1_m) < 30
    entry2_m = 30_000 - SETTINGS.probe_entry_back_m
    assert abs((chunks[2].entry[0] - 50.0) * M_PER_DEG_LAT - entry2_m) < 30
    # Chunk entries are exactly their first step's start (skeleton and probe agree).
    assert chunks[1].entry == new_steps[chunks[1].step_start].start
    for c in chunks:
        assert abs(c.entry_heading - 0) <= 1 or abs(c.entry_heading - 360) <= 1

    # Probe destinations overshoot past the interchange so its on-ramp can reach them.
    exit0_m = 12_000 + SETTINGS.probe_exit_fwd_m
    assert abs((chunks[0].exit[0] - 50.0) * M_PER_DEG_LAT - exit0_m) < 30
    # The last chunk ends at the stretch end, no overshoot.
    assert polyline_util.haversine_m(chunks[2].exit, new_steps[-1].end) < 2


def test_align_filters_far_nodes_and_clusters_near_ones():
    steps = motorway_run()
    # 30 m east: the opposite carriageway on a typical median — not our exit. No
    # usable boundary means the stretch falls back to distance chunking, with no
    # boundary anywhere near the rejected node.
    far = [[node_at(12_000, east_m=30.0)]]
    _, _, _, chunks = run_align(steps, far)
    assert [c.distance_m for c in chunks] == [25_000, 25_000]

    # Two nodes 400 m apart cluster into ONE interchange: one boundary, and the
    # exit overshoot clears the LAST node of the cluster.
    clustered = [[node_at(12_000), node_at(12_400)]]
    _, _, _, chunks = run_align(steps, clustered)
    assert len(chunks) == 2
    exit0_m = 12_400 + SETTINGS.probe_exit_fwd_m
    assert abs((chunks[0].exit[0] - 50.0) * M_PER_DEG_LAT - exit0_m) < 30


def test_align_target_is_a_ceiling_not_a_floor():
    steps = motorway_run()  # 50 km
    dense = [[node_at(m) for m in range(3_000, 50_000, 3_000)]]  # exits every 3 km
    _, _, _, chunks = run_align(steps, dense, target_m=6_000)
    assert len(chunks) >= 7
    for c in chunks:
        # Exits permitting, chunks never exceed the target (a floor would price every
        # /api/plan cut above the budget) and never shrink below the noise floor.
        assert 2_500 <= c.distance_m <= 6_100


def test_align_caps_chained_interchange_clusters():
    # Exits every 800 m from km 10 to km 30: transitive clustering would chain them
    # into one 20 km "interchange" whose exit overshoot swallows the next chunk.
    steps = motorway_run()  # 50 km
    dense = [[node_at(m) for m in range(10_000, 30_001, 800)]]
    new_steps, _, _, chunks = run_align(steps, dense, target_m=6_000)
    assert len(chunks) >= 4
    for prev, nxt in zip(chunks, chunks[1:]):
        # Probe destination overshoot beyond the tiling point stays bounded by
        # entry_back + 2x cluster span cap + exit_fwd, never km-scale.
        tile_m = (nxt.entry[0] - 50.0) * M_PER_DEG_LAT
        exit_m = (prev.exit[0] - 50.0) * M_PER_DEG_LAT
        assert exit_m - tile_m <= (
            SETTINGS.probe_entry_back_m
            + 2 * SETTINGS.junction_cluster_m
            + SETTINGS.probe_exit_fwd_m
        )
        assert exit_m > tile_m  # still overshoots past the interchange


def test_align_falls_back_per_stretch_without_nodes():
    steps = motorway_run()
    new_steps, _, _, chunks = run_align(steps, [None])
    flags = classify_steps(steps, SETTINGS)
    expected = build_chunks(
        steps, flags, [1.0] * len(steps), SETTINGS, chunk_km=25.0, max_chunks=48
    )
    assert [(c.step_start, c.step_end) for c in chunks] == [
        (c.step_start, c.step_end) for c in expected
    ]
    assert new_steps == steps  # nothing sliced


def test_align_mixed_stretches_and_boundary_inside_long_step():
    # Stretch A (junctions known) - country gap - stretch B (Overpass gap: None).
    steps = make_run(
        [(30_000, 115, "Continue on A3", "")]
        + [(4_000, 55, "Follow B49", "")]
        + [(15_000, 115, "Continue on A61", "")] * 2
    )
    nodes = [[node_at(14_000)], None]
    new_steps, _, new_flags, chunks = run_align(steps, nodes)

    # The 30 km single step was sliced at the boundary (14 km - entry_back).
    a_chunks = [c for c in chunks if c.stretch_id == 0]
    assert len(a_chunks) == 2
    cut_m = 14_000 - SETTINGS.probe_entry_back_m
    assert abs(a_chunks[0].distance_m - cut_m) < 50
    assert abs((a_chunks[1].entry[0] - 50.0) * M_PER_DEG_LAT - cut_m) < 30
    # Stretch B fell back to distance chunking with untouched steps.
    b_chunks = [c for c in chunks if c.stretch_id == 1]
    assert len(b_chunks) == 2
    assert math.isclose(sum(c.distance_m for c in b_chunks), 30_000, rel_tol=1e-4)
    # Flags survive the rebuild: the country step is still not highway.
    assert not all(new_flags)
    assert math.isclose(
        sum(s.distance_m for s in new_steps), sum(s.distance_m for s in steps), rel_tol=1e-6
    )


def test_pick_boundaries_drops_noise_and_cuts_before_target():
    ics = [(2_000.0, 2_000.0), (12_000.0, 12_300.0), (13_500.0, 13_500.0), (47_000.0, 47_000.0)]
    bounds = junctions.pick_boundaries(50_000.0, ics, SETTINGS, target_m=6_000)
    # 2 km: too close to the start. 13.5 km: too close to 12 km. 12 km and 47 km are
    # kept — each is the last exit before the chunk would exceed the 6 km target.
    assert len(bounds) == 2
    assert math.isclose(bounds[0].cut_m, 12_000 - SETTINGS.probe_entry_back_m)
    assert math.isclose(bounds[0].exit_m, 12_300 + SETTINGS.probe_exit_fwd_m)
    assert math.isclose(bounds[1].cut_m, 47_000 - SETTINGS.probe_entry_back_m)
    assert math.isclose(bounds[1].exit_m, 47_000 + SETTINGS.probe_exit_fwd_m)


def test_entry_heading_skips_duplicate_leading_points():
    from app.classify import _entry_heading
    from app.google_routes import GStep

    # A split point that quantizes onto the next vertex leaves two identical leading
    # points; the heading must come from the first DISTINCT point (east = 90), not
    # bearing(p, p) = 0 — a wrong heading sends the paid probe backwards.
    pts = [(50.0, 7.0), (50.0, 7.0), (50.0, 7.01)]
    step = GStep(
        distance_m=700,
        static_duration_s=25,
        encoded_polyline=polyline_util.encode(pts),
        maneuver="",
        instructions="Continue on A3",
        start=pts[0],
        end=pts[-1],
    )
    assert abs(_entry_heading(step) - 90) <= 1


def test_scout_spans_leave_a_separator_between_adjacent_junction_spans(monkeypatch):
    """Junction chunks' probe exits overshoot the next chunk's entry, so two adjacent
    launched spans would double back at the shared interchange — the second span must
    give up its edge chunk as a separator."""
    import asyncio

    from app.classify import Chunk
    from app.google_routes import GLeg, GRoute, GStep
    from app.planner import _scout_probes

    settings = Settings(_env_file=None, ihh_mock=False, osm_enabled=True, scout_max_probes=4)

    def make_junction_chunk(i: int) -> Chunk:
        return Chunk(
            stretch_id=0,
            step_start=i * 2,
            step_end=i * 2 + 2,
            distance_m=6_000,
            static_duration_s=200.0,
            baseline_s=200.0,
            entry=(50.0 + i * 0.06, 7.0),
            # Overshoots ~1.1 km past the NEXT chunk's entry (junction semantics).
            exit=(50.0 + (i + 1) * 0.06 + 0.01, 7.0),
            entry_heading=0,
        )

    chunks = [make_junction_chunk(i) for i in range(12)]

    async def fake_score_chunks(pairs, s, deadline_s=None, on_batch=None, bbox_batch=None):
        scores = [50.0] * len(pairs)  # one warm-cache batch: all clearly good
        if on_batch is not None:
            on_batch(pairs, scores)
        return scores

    from app import planner

    monkeypatch.setattr(planner.osm, "score_chunks", fake_score_chunks)

    class CountryClient:
        async def compute_route(self, origin, destination, avoid_highways=False, origin_heading=None):
            start, end = origin.lat_lng, destination.lat_lng
            distance = polyline_util.haversine_m(start, end) * 1.15
            static = distance / (70 / 3.6)
            step = GStep(
                distance_m=distance,
                static_duration_s=static,
                encoded_polyline=polyline_util.encode([start, end]),
                maneuver="",
                instructions="Follow the country roads",
                start=start,
                end=end,
            )
            return GRoute(static, static, distance, step.encoded_polyline, [GLeg(static, static, distance, [step])])

    candidates = asyncio.run(_scout_probes(chunks, CountryClient(), settings, None, None, None))
    ranges = sorted((c.chunk.step_start, c.chunk.step_end) for c in candidates)
    # One 10-chunk sweep, then chunk 10 sacrificed as the separator, then chunk 11.
    assert ranges == [(0, 20), (22, 24)]
