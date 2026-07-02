import math

from app import polyline_util
from app.classify import atomize_steps, build_chunks, classify_steps, find_stretches, is_highway_step
from app.config import Settings
from app.google_routes import GStep

SETTINGS = Settings(_env_file=None)

M_PER_DEG_LAT = 111_195.0


def make_step(
    distance_m: float,
    speed_kmh: float,
    instructions: str = "",
    maneuver: str = "",
    start=(50.0, 7.0),
):
    end = (start[0] + distance_m / M_PER_DEG_LAT, start[1])
    return GStep(
        distance_m=float(distance_m),
        static_duration_s=distance_m / (speed_kmh / 3.6),
        encoded_polyline=polyline_util.encode([start, end]),
        maneuver=maneuver,
        instructions=instructions,
        start=start,
        end=end,
    )


def make_run(specs):
    """Chain steps northward so start/end points are contiguous."""
    steps = []
    cursor = (50.0, 7.0)
    for distance_m, speed_kmh, instructions, maneuver in specs:
        step = make_step(distance_m, speed_kmh, instructions, maneuver, start=cursor)
        steps.append(step)
        cursor = step.end
    return steps


def test_fast_unnamed_step_is_highway():
    assert is_highway_step(make_step(1000, 100), SETTINGS)


def test_fast_but_too_short_is_not_highway():
    assert not is_highway_step(make_step(400, 100), SETTINGS)


def test_named_motorway_at_80_is_highway():
    assert is_highway_step(make_step(900, 80, instructions="Merge onto A3"), SETTINGS)


def test_country_step_is_not_highway():
    assert not is_highway_step(make_step(2000, 55, instructions="Continue on K7"), SETTINGS)


def test_slow_unnamed_is_not_highway_even_if_long():
    assert not is_highway_step(make_step(10_000, 71), SETTINGS)


def test_gap_bridging_absorbs_short_interruption():
    steps = make_run(
        [
            (8000, 110, "Continue on A3", ""),
            (1500, 50, "Service area", ""),
            (8000, 110, "Continue on A3", ""),
        ]
    )
    flags = classify_steps(steps, SETTINGS)
    assert flags == [True, True, True]


def test_long_gap_is_not_bridged():
    steps = make_run(
        [
            (8000, 110, "Continue on A3", ""),
            (3000, 50, "Follow B49", ""),
            (8000, 110, "Continue on A3", ""),
        ]
    )
    flags = classify_steps(steps, SETTINGS)
    assert flags == [True, False, True]


def test_maneuver_hint_marks_ramp_before_highway():
    steps = make_run(
        [
            (2000, 55, "Head north", ""),
            (300, 40, "Take the ramp", "MERGE"),
            (8000, 110, "Continue on A3", ""),
        ]
    )
    flags = classify_steps(steps, SETTINGS)
    assert flags == [False, True, True]


def test_ramp_without_following_highway_stays_country():
    steps = make_run(
        [
            (300, 40, "Take the ramp", "RAMP_RIGHT"),
            (2000, 55, "Head north", ""),
        ]
    )
    flags = classify_steps(steps, SETTINGS)
    assert flags == [False, False]


def test_short_stretch_is_dropped():
    steps = make_run([(10_000, 110, "Continue on A3", "")])
    flags = classify_steps(steps, SETTINGS)
    assert flags == [True]
    assert find_stretches(steps, flags, SETTINGS) == []  # 10 km < min_stretch_km (12)


def test_chunk_split_respects_max_chunk_km():
    steps = make_run([(10_000, 110, "Continue on A3", "")] * 10)  # one 100 km stretch
    flags = classify_steps(steps, SETTINGS)
    chunks = build_chunks(steps, flags, [1.0] * len(steps), SETTINGS)
    assert len(chunks) == 3  # greedy: 40 + 40 + 20 km
    assert all(c.distance_m <= SETTINGS.max_chunk_km * 1000 for c in chunks)
    # Chunks are contiguous and cover the stretch.
    assert chunks[0].step_start == 0
    assert chunks[-1].step_end == len(steps)
    for a, b in zip(chunks, chunks[1:]):
        assert a.step_end == b.step_start
    assert sum(c.distance_m for c in chunks) == 100_000
    for c in chunks:
        assert 0 <= c.entry_heading <= 360


def test_chunk_count_respects_max_chunks():
    settings = Settings(_env_file=None, max_chunks=3)
    steps = make_run([(10_000, 110, "Continue on A3", "")] * 30)  # one 300 km stretch
    flags = classify_steps(steps, settings)
    chunks = build_chunks(steps, flags, [1.0] * len(steps), settings)
    assert 1 <= len(chunks) <= 3  # adaptively enlarged beyond max_chunk_km
    assert sum(c.distance_m for c in chunks) == 300_000


def test_chunk_size_adapts_to_budget():
    steps = make_run([(5_000, 110, "Continue on A3", "")] * 20)  # one 100 km stretch
    flags = classify_steps(steps, SETTINGS)
    factors = [1.0] * len(steps)
    # 15-min budget / 48 s per highway km -> ~18.75 km chunks instead of 45 km ones.
    small = build_chunks(steps, flags, factors, SETTINGS, budget_s=900)
    assert all(c.distance_m <= 900 / SETTINGS.detour_extra_per_hw_km_s * 1000 for c in small)
    assert len(small) > len(build_chunks(steps, flags, factors, SETTINGS))
    # A large budget clamps at max_chunk_km, matching the budget-less behavior.
    large = build_chunks(steps, flags, factors, SETTINGS, budget_s=7200)
    assert all(c.distance_m <= SETTINGS.max_chunk_km * 1000 for c in large)
    assert len(large) == len(build_chunks(steps, flags, factors, SETTINGS))
    # Zero budget clamps at min_chunk_km (small chunks maximize free jam escapes).
    tiny = build_chunks(steps, flags, factors, SETTINGS, budget_s=0)
    assert all(c.distance_m <= SETTINGS.min_chunk_km * 1000 for c in tiny)


def test_atomize_splits_long_single_step():
    long_step = make_step(30_000, 120, instructions="Continue on A3", maneuver="MERGE")
    short_step = make_step(2_000, 55, instructions="Follow K7", start=long_step.end)
    atoms, factors = atomize_steps([long_step, short_step], [1.3, 1.3], atom_m=3_000)
    # 30 km / 3 km -> 10 atoms, short step untouched.
    assert len(atoms) == 11
    assert all(f == 1.3 for f in factors)
    split = atoms[:10]
    assert math.isclose(sum(a.distance_m for a in split), 30_000)
    assert math.isclose(sum(a.static_duration_s for a in split), long_step.static_duration_s)
    assert split[0].maneuver == "MERGE"
    assert all(a.maneuver == "" for a in split[1:])
    assert all(a.instructions == "Continue on A3" for a in split)
    # Atoms are contiguous and reproduce the step geometry end to end.
    assert split[0].start == long_step.start
    assert polyline_util.haversine_m(split[-1].end, long_step.end) < 5
    for a, b in zip(split, split[1:]):
        assert polyline_util.haversine_m(a.end, b.start) < 1
    total_len = sum(
        polyline_util.path_length_m(polyline_util.decode(a.encoded_polyline)) for a in split
    )
    assert abs(total_len - 30_000) / 30_000 < 0.02
    assert atoms[10] == short_step


def test_chunk_baseline_uses_traffic_factor():
    steps = make_run([(10_000, 100, "Continue on A3", "")] * 2)  # 20 km stretch
    flags = classify_steps(steps, SETTINGS)
    chunks = build_chunks(steps, flags, [1.5, 1.5], SETTINGS)
    assert len(chunks) == 1
    assert math.isclose(chunks[0].baseline_s, chunks[0].static_duration_s * 1.5)
