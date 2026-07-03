"""Application settings (pydantic-settings), read from backend/.env and the environment."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Secrets / deployment
    google_maps_api_key: str = ""
    ihh_mock: bool = False
    ihh_cors_origins: str = "http://localhost:5173"
    # Wallet guard for public deployments (uncached plans only).
    rate_per_ip_hour: int = 10
    rate_daily_cap: int = 100
    # Enable behind a trusted reverse proxy so limits apply to the real client IP.
    trust_forwarded_for: bool = False

    # Algorithm tunables (see docs/algorithm.md)
    highway_fast_kmh: float = 90
    highway_named_kmh: float = 72
    min_step_m: float = 500
    gap_bridge_m: float = 2000
    min_stretch_km: float = 12.0
    min_chunk_km: float = 12.0
    max_chunk_km: float = 45.0
    max_chunks: int = 10
    # Long single steps are split into atoms of this size so chunk boundaries can fall
    # anywhere along a motorway haul (Google returns those as one huge step).
    step_atom_m: float = 3000.0
    # Expected extra seconds paid per highway km replaced by country roads; sizes chunks
    # so a single detour roughly fits the rider's budget (see docs/algorithm.md).
    # Calibrated on real Cologne->Frankfurt data (measured 43-72 s per highway km).
    detour_extra_per_hw_km_s: float = 48.0
    # Fixed chunk size for /api/scout, where the rider composes cuts manually: fine
    # enough that single cuts stay in the +10-20 min range, coarse enough to keep the
    # probe count (= paid Google calls) near the /api/plan level. Chunks do NOT grow
    # with route length — on long hauls OSM scoring picks which corridors get probed.
    scout_chunk_km: float = 25.0
    # Paid Google detour probes per scout (the OSM ranking chooses which chunks).
    scout_max_probes: int = 12
    # Consecutive well-scoring corridors merge into one big cut candidate (a
    # continuously curvy region like the Pfälzerwald becomes a single ~75 km sweep,
    # for a single probe) — without this, long rides only ever get 25 km nibbles.
    scout_max_span_chunks: int = 3
    # Raw candidate corridors before OSM ranking; chunk size only grows beyond this
    # (a 2000 km highway haul still yields 48 x ~42 km corridors, not 10 x 200 km).
    scout_max_raw_chunks: int = 48
    # Reject paid detours that shed less highway time than this fraction of their cost.
    min_detour_efficiency: float = 0.5
    # A detour must shed at least this fraction of its chunk's highway time, or it never
    # really left the motorway (soft avoidHighways stays on it when leaving is costly).
    min_avoided_fraction: float = 0.5

    # OSM corridor pre-filter: skip Google detour probes for chunks with no fun roads
    # nearby (saves ~$ per plan and kills junk urban detours at the source).
    osm_enabled: bool = True
    # Comma-separated mirror list, tried in order per query.
    osm_overpass_urls: str = (
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.private.coffee/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter"
    )
    osm_bbox_pad_m: float = 4000
    # Chunk bboxes unioned into one Overpass query (bbox filters are cheap; batching
    # keeps long routes at ~2 lanes x few queries instead of dozens). Measured: 3-bbox
    # unions answer in ~6 s on a healthy instance; 6-bbox ones exceed a 10 s server
    # timeout and fail wholesale.
    osm_bbox_batch: int = 3
    osm_min_curvy_km: float = 2.0  # calibrated on real corridors, see docs/algorithm.md
    osm_timeout_s: float = 15.0  # per query (server-side and client-side)
    # How long OSM scoring may delay a scout: base + per-cold-batch, capped. Long cold
    # routes wait longer (probe targeting matters most there; the UI shows a loader),
    # warm ones barely wait. Unscored chunks fail open either way.
    osm_deadline_s: float = 8.0
    osm_deadline_per_batch_s: float = 4.0
    # Hard UX cap: partially-scored scouts probe with what they have (unknowns fill
    # probe slots evenly) rather than stalling for straggler batches; the cache warms
    # a bit more with every scout anyway. Observed: waiting out a 28 s deadline for
    # the last 3 batches bought nothing the even-spread fallback wouldn't.
    osm_deadline_max_s: float = 18.0
    knapsack_bucket_s: int = 15
    cache_ttl_s: int = 240
