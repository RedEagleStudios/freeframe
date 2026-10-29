import { api } from './api'
import type { ProjectRole } from '@/types'

/** A join link never makes someone an owner. */
export type JoinLinkRole = Exclude<ProjectRole, 'owner'>

export type JoinLinkInvalidReason = 'expired' | 'revoked' | 'used_up' | 'not_found'

/** A single-use project join link as the owner's list shows it. Never carries the token. */
export interface ProjectJoinLink {
  id: string
  project_id: string
  role: JoinLinkRole
  status: 'active' | 'used' | 'expired' | 'revoked'
  created_at: string | null
  created_by: string
  created_by_name: string | null
  expires_at: string | null
  used_at: string | null
  used_by: string | null
  used_by_name: string | null
  revoked_at: string | null
}

/** The create response: the only time the token, and the URL built from it, are sent. */
export interface CreatedProjectJoinLink extends ProjectJoinLink {
  token: string
  url: string
}

/** What anyone holding a link may learn about it. */
export interface JoinLinkPreview {
  valid: boolean
  project_name: string | null
  role: JoinLinkRole | null
  reason: JoinLinkInvalidReason | null
}

export interface JoinLinkAccepted {
  project_id: string
  role: ProjectRole
}

export const JOIN_LINK_ROLES: { value: JoinLinkRole; label: string; description: string }[] = [
  { value: 'viewer', label: 'Viewer', description: 'Can view the project' },
  { value: 'reviewer', label: 'Reviewer', description: 'Can view and comment' },
  { value: 'editor', label: 'Editor', description: 'Can upload, manage, download and share' },
]

/** Expiry choices, in days; null never expires. */
export const JOIN_LINK_EXPIRIES: { value: 1 | 7 | 30 | null; label: string }[] = [
  { value: 1, label: '1 day' },
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
  { value: null, label: 'Never' },
]

export function joinLinkRoleLabel(role: ProjectRole | null | undefined): string {
  return JOIN_LINK_ROLES.find((r) => r.value === role)?.label ?? (role === 'owner' ? 'Owner' : 'member')
}

/** The join page's message for a link that cannot be used. */
export const JOIN_LINK_INVALID_MESSAGES: Record<JoinLinkInvalidReason, string> = {
  used_up: 'This invite link has already been used. Ask the project owner for a new one.',
  expired: 'This invite link has expired. Ask the project owner for a new one.',
  revoked: 'This invite link was revoked. Ask the project owner for a new one.',
  not_found: 'This invite link is not valid. Check that you copied all of it, or ask the project owner for a new one.',
}

export function joinLinkInvalidMessage(reason: string | null | undefined): string {
  return Object.prototype.hasOwnProperty.call(JOIN_LINK_INVALID_MESSAGES, reason ?? '')
    ? JOIN_LINK_INVALID_MESSAGES[reason as JoinLinkInvalidReason]
    : JOIN_LINK_INVALID_MESSAGES.not_found
}

export function previewJoinLink(token: string): Promise<JoinLinkPreview> {
  return api.get<JoinLinkPreview>(`/project-join-links/${encodeURIComponent(token)}`)
}

export function acceptJoinLink(token: string): Promise<JoinLinkAccepted> {
  return api.post<JoinLinkAccepted>(`/project-join-links/${encodeURIComponent(token)}/accept`)
}

export function listJoinLinks(projectId: string): Promise<ProjectJoinLink[]> {
  return api.get<ProjectJoinLink[]>(`/projects/${projectId}/join-links`)
}

export function createJoinLink(
  projectId: string,
  body: { role: JoinLinkRole; expires_in_days: 1 | 7 | 30 | null },
): Promise<CreatedProjectJoinLink> {
  return api.post<CreatedProjectJoinLink>(`/projects/${projectId}/join-links`, body)
}

export function revokeJoinLink(projectId: string, linkId: string): Promise<void> {
  return api.delete<void>(`/projects/${projectId}/join-links/${linkId}`)
}
