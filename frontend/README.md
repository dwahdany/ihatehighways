# ihatehighways — frontend

Vite + React + TypeScript client for `POST /api/plan`.

- Env: copy `.env.example` to `.env.local`, set `VITE_GOOGLE_MAPS_API_KEY` (browser key, Maps JavaScript API + Places API (New)). Optionally set `VITE_API_BASE` (e.g. `http://localhost:8000`) when serving the frontend without the `/api` proxy; pair it with the backend's `IHH_CORS_ORIGINS`.
- Dev: `nix-shell -p nodejs_22 --run 'npm install && npm run dev'` → http://localhost:5173
- Build: `nix-shell -p nodejs_22 --run 'npm run build'`
- Vite proxies `/api` → `http://localhost:8000` (see `vite.config.ts`); run the backend there.
