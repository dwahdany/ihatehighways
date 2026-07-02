"""OSM corridor scoring: is there anything fun to ride near this highway chunk?

For each un-cached chunk, query Overpass (free, ODbL) for secondary/tertiary roads in
the chunk's padded bounding box and score the "excess curvature km":

    score = Σ over ways of  length_km × max(0, min(sinuosity, 2) − 1.05)

City grids score ≈ 0 (short, straight segments chopped at junctions); rural twisties
score high (long ways with sinuosity 1.1–1.5). Calibration on real data: the Westerwald
corridor along the A3 scores ~19; anything under ~2 has nothing worth riding. The score
gates which chunks get a paid Google detour probe.

Ops notes, learned the hard way: bbox filters are cheap but `around:` polyline filters
time out server-side; public instances allow ~2 concurrent slots per IP and get "too
busy" at peak — so queries run on 2 lanes with per-query mirror failover, an overall
deadline returns partial results (un-scored chunks fail OPEN), and scores are cached on
disk for 30 days (ODbL permits caching, unlike Google content — attribution:
© OpenStreetMap contributors).

ToS note: OSM data only decides which Google queries to make; it is never mixed into
displayed route content.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from pathlib import Path

import httpx

from . import polyline_util
from .config import Settings
from .polyline_util import Point

logger = logging.getLogger("ihatehighways.osm")

USER_AGENT = "ihatehighways/0.1 (+https://github.com/dwahdany/ihatehighways)"
CACHE_FILE = Path(__file__).resolve().parent.parent / ".cache" / "osm_corridors.json"
CACHE_TTL_S = 30 * 24 * 3600  # OSM roads barely change; refresh monthly
MAX_PARALLEL_QUERIES = 2  # public Overpass slot limit per IP
_MIN_WAY_M = 300.0
_MIN_CHORD_M = 100.0
_SINUOSITY_BASELINE = 1.05
_SINUOSITY_CAP = 2.0

_cache: dict[str, tuple[float, float]] | None = None  # key -> (score, ts)
_lock = asyncio.Lock()


def _cache_key(pair: tuple[Point, Point]) -> str:
    # ~1 km grid: corridors moving less than that share a score.
    entry, exit_ = pair
    return f"{entry[0]:.2f},{entry[1]:.2f}->{exit_[0]:.2f},{exit_[1]:.2f}"


def _load_cache() -> dict[str, tuple[float, float]]:
    global _cache
    if _cache is None:
        try:
            raw = json.loads(CACHE_FILE.read_text())
            _cache = {k: (float(v[0]), float(v[1])) for k, v in raw.items()}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save_cache() -> None:
    if _cache is None:
        return
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({k: list(v) for k, v in _cache.items()}))
    except OSError as exc:  # read-only filesystems are fine — cache stays in-memory
        logger.debug("could not persist OSM cache: %s", exc)


def _bbox(pair: tuple[Point, Point], pad_m: float) -> tuple[float, float, float, float]:
    (lat1, lng1), (lat2, lng2) = pair
    lat_pad = pad_m / 111_320.0
    lng_pad = pad_m / (111_320.0 * max(0.2, math.cos(math.radians((lat1 + lat2) / 2))))
    return (
        min(lat1, lat2) - lat_pad,
        min(lng1, lng2) - lng_pad,
        max(lat1, lat2) + lat_pad,
        max(lng1, lng2) + lng_pad,
    )


def _query(pair: tuple[Point, Point], settings: Settings) -> str:
    s, w, n, e = _bbox(pair, settings.osm_bbox_pad_m)
    # bbox filters are cheap server-side (around: polyline filters time out); the
    # length filter drops the thousands of sub-300 m urban fragments from the payload.
    return (
        f"[out:json][timeout:{int(settings.osm_timeout_s)}];"
        f'way[highway~"^(secondary|tertiary)$"][junction!~"."]'
        f"(if:length()>{int(_MIN_WAY_M)})"
        f"({s:.4f},{w:.4f},{n:.4f},{e:.4f});"
        "out geom;"
    )


def _way_excess_km(geom: list[dict]) -> tuple[float, Point] | None:
    """(excess curvy km, representative midpoint) for one way, or None if irrelevant."""
    if len(geom) < 3:
        return None
    pts = [(g["lat"], g["lon"]) for g in geom]
    if pts[0] == pts[-1]:  # closed way (loop) — junk for this metric
        return None
    length = polyline_util.path_length_m(pts)
    chord = polyline_util.haversine_m(pts[0], pts[-1])
    if length < _MIN_WAY_M or chord < _MIN_CHORD_M:
        return None
    sinuosity = min(length / chord, _SINUOSITY_CAP)
    excess = (length / 1000.0) * max(0.0, sinuosity - _SINUOSITY_BASELINE)
    return excess, pts[len(pts) // 2]


def score_ways(elements: list[dict], pairs: list[tuple[Point, Point]]) -> list[float]:
    """Assign each way to the nearest chunk (by chunk midpoint) and sum excess km."""
    mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in pairs]
    scores = [0.0] * len(pairs)
    for el in elements:
        scored = _way_excess_km(el.get("geometry") or [])
        if scored is None:
            continue
        excess, rep = scored
        nearest = min(range(len(mids)), key=lambda i: polyline_util.haversine_m(rep, mids[i]))
        scores[nearest] += excess
    return scores


async def _fetch_score(
    pair: tuple[Point, Point], settings: Settings, http: httpx.AsyncClient
) -> float | None:
    """Query one chunk corridor, trying each configured Overpass mirror once."""
    mirrors = [u.strip() for u in settings.osm_overpass_urls.split(",") if u.strip()]
    for url in mirrors:
        try:
            resp = await http.post(url, data={"data": _query(pair, settings)})
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.info("Overpass mirror %s failed: %s", url, exc)
            continue
        if body.get("remark"):  # server-side timeout returns 200 + empty elements
            logger.info("Overpass mirror %s remark: %s", url, body["remark"])
            continue
        return score_ways(body.get("elements") or [], [pair])[0]
    return None


async def score_chunks(
    pairs: list[tuple[Point, Point]], settings: Settings
) -> list[float | None]:
    """Score all chunk corridors; None entries mean "unknown" (callers must fail open).

    Runs misses on 2 lanes under an overall deadline; whatever finished in time is
    returned and cached, the rest stays None. The cache warms up across plans.
    """
    async with _lock:
        cache = _load_cache()
        now = time.time()
        results: list[float | None] = []
        misses: list[tuple[int, tuple[Point, Point]]] = []
        for i, pair in enumerate(pairs):
            hit = cache.get(_cache_key(pair))
            if hit and now - hit[1] < CACHE_TTL_S:
                results.append(hit[0])
            else:
                results.append(None)
                misses.append((i, pair))
    if not misses:
        return results

    semaphore = asyncio.Semaphore(MAX_PARALLEL_QUERIES)
    scored: dict[int, float] = {}

    async def one(index: int, pair: tuple[Point, Point], http: httpx.AsyncClient) -> None:
        async with semaphore:
            score = await _fetch_score(pair, settings, http)
            if score is not None:
                scored[index] = score

    async with httpx.AsyncClient(
        timeout=settings.osm_timeout_s + 5, headers={"User-Agent": USER_AGENT}
    ) as http:
        tasks = [asyncio.ensure_future(one(i, p, http)) for i, p in misses]
        _, pending = await asyncio.wait(tasks, timeout=settings.osm_deadline_s)
        for task in pending:
            task.cancel()
        if pending:
            logger.warning(
                "OSM deadline hit: %d/%d chunk corridors unscored (failing open)",
                len(pending),
                len(misses),
            )

    if scored:
        async with _lock:
            cache = _load_cache()
            for (i, pair) in misses:
                if i in scored:
                    results[i] = scored[i]
                    cache[_cache_key(pair)] = (scored[i], time.time())
            _save_cache()
    return results
