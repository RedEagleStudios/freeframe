import { api } from './api'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

/** Which external sign-in providers the instance offers (`GET /auth/providers`). */
export interface AuthProviders {
  discord: boolean
}

/** What `POST /auth/discord/exchange` answers for a valid one-time ticket. */
export interface DiscordExchangeResponse {
  access_token: string
  refresh_token: string
  needs_password: boolean
  redirect_to: string
}

/** The admin view of the Discord OAuth app. The secret itself is never sent back. */
export interface DiscordAuthConfig {
  enabled: boolean
  client_id: string | null
  client_secret_set: boolean
  server_id: string | null
  allowed_role_ids: string[]
  redirect_uri: string
  configured_via: 'database' | 'environment' | 'none'
}

/** Partial update: an omitted key stays as it is; `client_secret: ''` removes the secret. */
export interface DiscordAuthConfigUpdate {
  enabled?: boolean
  client_id?: string | null
  client_secret?: string | null
  server_id?: string | null
  allowed_role_ids?: string[]
}

export function fetchAuthProviders(): Promise<AuthProviders> {
  return api.get<AuthProviders>('/auth/providers')
}

export function exchangeDiscordTicket(ticket: string): Promise<DiscordExchangeResponse> {
  return api.post<DiscordExchangeResponse>('/auth/discord/exchange', { ticket })
}

export function fetchDiscordAuthConfig(): Promise<DiscordAuthConfig> {
  return api.get<DiscordAuthConfig>('/admin/auth/discord')
}

export function updateDiscordAuthConfig(update: DiscordAuthConfigUpdate): Promise<DiscordAuthConfig> {
  return api.put<DiscordAuthConfig>('/admin/auth/discord', update)
}

/**
 * The address the "Continue with Discord" button navigates to. It is a full
 * page load on the API (which answers with a redirect to Discord), so it is the
 * API's own URL: the API stays at the origin root and carries no basePath.
 * `from` must already be a sanitized in-app path; the API checks it again.
 */
export function discordStartUrl(from: string): string {
  return `${API_URL}/auth/discord/start?from=${encodeURIComponent(from)}`
}

/** Login-page messages for the `?error=` codes the Discord flow redirects with. */
export const DISCORD_LOGIN_ERRORS: Record<string, string> = {
  discord_no_account: 'No FreeFrame account uses this Discord email. Ask an admin to invite you.',
  discord_not_in_server: "You need to be a member of the team's Discord server.",
  discord_missing_role: "Your Discord role doesn't have access to FreeFrame.",
  discord_account_disabled: 'This account has been deactivated. Contact an admin.',
  discord_email_unverified: 'Verify your email on Discord first.',
  discord_invite_expired: 'Your invitation has expired. Ask an admin to send a new one.',
  discord_not_configured: 'Sign in with Discord is not set up on this instance.',
  discord_failed: 'Signing in with Discord failed. Please try again.',
}

/** The message for a login `?error=` value, or null when it is not a Discord error code. */
export function discordLoginError(code: string | null | undefined): string | null {
  if (!code) return null
  return Object.prototype.hasOwnProperty.call(DISCORD_LOGIN_ERRORS, code) ? DISCORD_LOGIN_ERRORS[code] : null
}

/** Role IDs typed one per line or comma-separated, trimmed, blanks and repeats dropped. */
export function parseRoleIds(text: string): string[] {
  return Array.from(new Set(text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)))
}
