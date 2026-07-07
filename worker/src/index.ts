/**
 * Static frontend + same-origin /api served by a Cloudflare Container running the
 * FastAPI backend (backend/Dockerfile) as a singleton.
 *
 * Same-origin keeps CORS out of the picture entirely; the Worker forwards the real
 * client IP so the backend's per-IP rate limiting works behind the proxy chain
 * (browser → Cloudflare → container). While API_ORIGIN is set, a container failure
 * (not an app error) falls back to the legacy Render origin.
 */

import { Container } from '@cloudflare/containers'

interface Env {
  ASSETS: Fetcher
  BACKEND: any // DurableObjectNamespace<Backend>
  API_ORIGIN?: string
  GOOGLE_MAPS_API_KEY: string
}

export class Backend extends Container<Env> {
  defaultPort = 8000
  sleepAfter = '30m' // ephemeral disk: the OSM cache resets on sleep (warm-start loss only)
  enableInternet = true // Google Routes + Overpass
  envVars = {
    PORT: '8000',
    GOOGLE_MAPS_API_KEY: this.env.GOOGLE_MAPS_API_KEY,
    IHH_CORS_ORIGINS: 'https://ihatehighways.wahdany.eu',
    TRUST_FORWARDED_FOR: '1',
    RATE_PER_IP_HOUR: '10',
    RATE_DAILY_CAP: '100',
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)
    if (url.pathname.startsWith('/api/')) {
      const headers = new Headers(request.headers)
      const clientIp = request.headers.get('cf-connecting-ip')
      if (clientIp) headers.set('x-forwarded-for', clientIp)
      const upstream = new Request(request, { headers })
      try {
        // Singleton: the backend's in-memory rate limits and route TTL cache assume
        // one instance, exactly like the old single Render dyno.
        const backend = env.BACKEND.getByName('api')
        await backend.startAndWaitForPorts()
        return await backend.fetch(upstream)
      } catch (err) {
        // Container infra failure (start timeout, capacity) — NOT app 4xx/5xx.
        if (env.API_ORIGIN) {
          console.error('container unavailable, falling back to legacy origin', err)
          return fetch(new URL(url.pathname + url.search, env.API_ORIGIN), upstream)
        }
        throw err
      }
    }
    return env.ASSETS.fetch(request)
  },
}
