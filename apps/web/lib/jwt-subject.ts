/**
 * Reading the claims of a JWT without checking its signature: these only name
 * the account the server will act as and when it stops, they do not vouch for
 * it.
 *
 * Kept free of imports so `auth.ts` can use it: `upload-owner.ts`, which
 * re-exports it, imports `auth.ts`.
 */
function claimsOf(token: string): Record<string, unknown> | null {
  const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
  const payload = JSON.parse(atob(part)) as unknown
  return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : null
}

/**
 * The `sub` claim of a JWT, or null for no token or anything that does not
 * decode to a payload with a non-empty string `sub`.
 */
export function subjectOf(token: string | null): string | null {
  if (!token) return null
  try {
    const sub = claimsOf(token)?.sub
    return typeof sub === 'string' && sub !== '' ? sub : null
  } catch {
    return null
  }
}

/**
 * Whether a JWT's `exp` falls within `withinMs` from now, or has passed. A token
 * that does not decode, or has no numeric `exp`, is not ours to judge: false,
 * and the server answers for it.
 */
export function expiresWithin(token: string, withinMs: number): boolean {
  try {
    const exp = claimsOf(token)?.exp
    return typeof exp === 'number' && exp * 1000 <= Date.now() + withinMs
  } catch {
    return false
  }
}
