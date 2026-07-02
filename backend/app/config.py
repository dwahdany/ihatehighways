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
    # Reject paid detours that shed less highway time than this fraction of their cost.
    min_detour_efficiency: float = 0.5
    # A detour must shed at least this fraction of its chunk's highway time, or it never
    # really left the motorway (soft avoidHighways stays on it when leaving is costly).
    min_avoided_fraction: float = 0.5
    knapsack_bucket_s: int = 15
    cache_ttl_s: int = 240
