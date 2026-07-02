# ihatehighways — backend

FastAPI service implementing `POST /api/plan` (see `../docs/api.md` and `../docs/algorithm.md`).

- Dev server: `uv run uvicorn app.main:app --reload --port 8000`
- Tests: `uv run pytest -q`

Copy `.env.example` to `.env` and set `GOOGLE_MAPS_API_KEY` (Routes API enabled) for real routing.

Mock mode: set `IHH_MOCK=1` to serve deterministic synthetic routes (fixed ~172 km
Cologne→Frankfurt geometry with one jammed motorway stretch) — no Google key or network needed.
