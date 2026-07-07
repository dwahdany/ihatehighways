/**
 * Static frontend + same-origin /api served by a Cloudflare Container running the
 * FastAPI backend (backend/Dockerfile) as a singleton.
 *
 * Same-origin keeps CORS out of the picture entirely; the Worker forwards the real
 * client IP so the backend's per-IP rate limiting works behind the proxy chain
 * (browser → Cloudflare → container).
 */

import { Container } from '@cloudflare/containers'

interface Env {
  ASSETS: Fetcher
  BACKEND: any // DurableObjectNamespace<Backend>
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
      // Singleton: the backend's in-memory rate limits and route TTL cache assume
      // one instance.
      const backend = env.BACKEND.getByName('api')
      await backend.startAndWaitForPorts()
      return backend.fetch(new Request(request, { headers }))
    }
    return env.ASSETS.fetch(request)
  },
}
