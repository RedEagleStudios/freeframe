import { forwardOAuthMetadata } from '@/lib/oauth-metadata'

export const dynamic = 'force-dynamic'

// The API has one issuer, so every path-inserted form (RFC 8414 §3.1, e.g.
// /.well-known/oauth-authorization-server/api) gets the same document.
export async function GET() {
  return forwardOAuthMetadata('/.well-known/oauth-authorization-server')
}
