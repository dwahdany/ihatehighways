/**
 * Static frontend + same-origin /api proxy to the backend.
 *
 * Same-origin keeps CORS out of the picture entirely; the Worker forwards the real
 * client IP so the backend's per-IP rate limiting works behind the proxy chain
 * (browser → Cloudflare → Render LB → app).
 */

interface Env {
  ASSETS: Fetcher
  API_ORIGIN: string
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)
    if (url.pathname.startsWith('/api/')) {
      const target = new URL(url.pathname + url.search, env.API_ORIGIN)
      const headers = new Headers(request.headers)
      const clientIp = request.headers.get('cf-connecting-ip')
      if (clientIp) headers.set('x-forwarded-for', clientIp)
      return fetch(target, {
        method: request.method,
        headers,
        body: request.body,
      })
    }
    return env.ASSETS.fetch(request)
  },
} satisfies ExportedHandler<Env>
