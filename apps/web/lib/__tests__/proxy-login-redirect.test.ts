/**
 * The proxy's sign-in gate. An AI app's connection request is a URL with a
 * query (/oauth/consent?request=…), so the way back after sign-in has to keep
 * it; and MCP clients read OAuth discovery under /.well-known/ before anyone
 * is signed in.
 */
import { describe, it, expect } from 'vitest'
import { NextRequest } from 'next/server'
import { proxy } from '../../proxy'

function signedOutRequest(url: string) {
  const req = new NextRequest(url)
  req.cookies.set('ff_setup_done', '1') // skip the setup check
  return req
}

describe('proxy sign-in gate', () => {
  it('sends a signed-out visitor to sign in with the full way back, query included', async () => {
    const res = await proxy(signedOutRequest('http://localhost:3000/oauth/consent?request=abc'))

    expect(res.status).toBe(307)
    const location = new URL(res.headers.get('location')!)
    expect(location.pathname).toBe('/login')
    expect(location.searchParams.get('from')).toBe('/oauth/consent?request=abc')
  })

  it('lets OAuth discovery through without a session', async () => {
    const res = await proxy(signedOutRequest('http://localhost:3000/.well-known/oauth-authorization-server/api'))

    expect(res.headers.get('location')).toBeNull()
  })
})
