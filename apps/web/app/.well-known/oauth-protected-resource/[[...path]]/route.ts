import { forwardOAuthMetadata } from '@/lib/oauth-metadata'

export const dynamic = 'force-dynamic'

// RFC 9728 §3.1: the resource's path follows the well-known prefix
// (/.well-known/oauth-protected-resource/api/mcp), and the API registers that
// same path, so it is passed on unchanged.
export async function GET(_request: Request, { params }: { params: Promise<{ path?: string[] }> }) {
  const { path = [] } = await params
  return forwardOAuthMetadata(['/.well-known/oauth-protected-resource', ...path.map(encodeURIComponent)].join('/'))
}
