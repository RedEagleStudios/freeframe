/**
 * OAuth discovery for the MCP server, answered by the API.
 *
 * MCP clients look for the authorization server and the protected resource at
 * the origin root (/.well-known/oauth-authorization-server/api and
 * /.well-known/oauth-protected-resource/api/mcp when the API is served at
 * /api). In production the proxy sends everything outside /api to this app, so
 * these route handlers pass the requests on to the API, which owns the answer.
 */

// Server-to-server address, as in proxy.ts: NEXT_PUBLIC_API_URL is `/api` in
// the production image, which a server-side fetch cannot resolve.
const API_URL =
  process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export async function forwardOAuthMetadata(apiPath: string): Promise<Response> {
  try {
    const res = await fetch(`${API_URL}${apiPath}`, { cache: 'no-store', signal: AbortSignal.timeout(5000) })
    return new Response(await res.text(), {
      status: res.status,
      headers: {
        'Content-Type': res.headers.get('content-type') ?? 'application/json',
        // Public metadata; browser-based MCP clients read it cross-origin.
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'no-store',
      },
    })
  } catch {
    return Response.json({ error: 'temporarily_unavailable' }, { status: 502 })
  }
}
