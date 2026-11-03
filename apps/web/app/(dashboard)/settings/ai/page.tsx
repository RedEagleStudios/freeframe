'use client'

import * as React from 'react'
import { Bot, Check, Copy } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { copyToClipboard, formatRelativeTime } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { ConnectedApp, McpServerInfo } from '@/types'

const SCOPE_WRITE = 'freeframe:write'

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = React.useState(false)
  return (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      aria-label={label}
      onClick={async () => {
        if (await copyToClipboard(text)) {
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }
      }}
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </Button>
  )
}

function Snippet({ title, hint, code }: { title: string; hint?: string; code: string }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-medium text-text-secondary">{title}</h3>
        {hint && <span className="text-2xs text-text-tertiary font-mono">{hint}</span>}
      </div>
      <div className="flex items-start gap-2">
        <pre className="flex-1 overflow-x-auto rounded-md border border-border bg-bg-tertiary px-3 py-2 text-2xs text-text-primary font-mono">
          {code}
        </pre>
        <CopyButton text={code} label={`Copy ${title} setup`} />
      </div>
    </div>
  )
}

export default function AiAccessPage() {
  const [server, setServer] = React.useState<McpServerInfo | null>(null)
  const [apps, setApps] = React.useState<ConnectedApp[] | null>(null)
  const [error, setError] = React.useState('')
  const [revoking, setRevoking] = React.useState<string | null>(null)

  const loadApps = React.useCallback(() => {
    api.get<ConnectedApp[]>('/oauth/grants').then(setApps).catch(() => setApps([]))
  }, [])

  React.useEffect(() => {
    api.get<McpServerInfo>('/oauth/server-info').then(setServer).catch(() => setError('Could not load the MCP server address.'))
    loadApps()
  }, [loadApps])

  async function disconnect(app: ConnectedApp) {
    setRevoking(app.id)
    setError('')
    try {
      await api.delete(`/oauth/grants/${app.id}`)
      loadApps()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not disconnect the app.')
    } finally {
      setRevoking(null)
    }
  }

  const url = server?.mcp_url ?? ''

  return (
    <div className="p-6 max-w-xl space-y-8">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-muted">
          <Bot className="h-5 w-5 text-text-primary" />
        </div>
        <div>
          <h1 className="text-lg font-semibold text-text-primary">AI access</h1>
          <p className="text-sm text-text-secondary">
            Let AI assistants find, review, download and upload your files over MCP
          </p>
        </div>
      </div>

      {error && <p className="text-xs text-status-error">{error}</p>}

      <section className="space-y-4">
        <h2 className="text-sm font-semibold text-text-primary border-b border-border pb-2">MCP server</h2>
        {server && !server.enabled ? (
          <p className="rounded-lg border border-status-error/30 bg-status-error/10 p-2.5 text-xs text-status-error">
            The MCP server is off on this instance. {server.disabled_reason}
          </p>
        ) : (
          <>
            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1.5">
                <label htmlFor="mcp-url" className="text-xs font-medium text-text-secondary">Server URL</label>
                <Input id="mcp-url" value={url} readOnly className="font-mono text-xs" />
              </div>
              {url && <CopyButton text={url} label="Copy server URL" />}
            </div>
            <p className="text-2xs text-text-tertiary">
              Add this URL to your AI app. The first time it connects, your browser opens a FreeFrame page where
              you approve it. It then acts as you, sees what you can see, and stays connected until you disconnect
              it here.
            </p>
          </>
        )}
      </section>

      {url && server?.enabled && (
        <section className="space-y-4">
          <h2 className="text-sm font-semibold text-text-primary border-b border-border pb-2">Connect an app</h2>
          <Snippet title="Claude Code" code={`claude mcp add --transport http freeframe ${url}`} />
          <div className="space-y-1.5">
            <h3 className="text-xs font-medium text-text-secondary">Claude (desktop or web)</h3>
            <p className="text-2xs text-text-tertiary">In Settings, open Connectors, choose Add custom connector, and paste the server URL.</p>
          </div>
          <Snippet
            title="Cursor"
            hint="~/.cursor/mcp.json"
            code={JSON.stringify({ mcpServers: { freeframe: { url } } }, null, 2)}
          />
          <Snippet
            title="VS Code"
            hint=".vscode/mcp.json"
            code={JSON.stringify({ servers: { freeframe: { type: 'http', url } } }, null, 2)}
          />
        </section>
      )}

      <section className="space-y-4">
        <h2 className="text-sm font-semibold text-text-primary border-b border-border pb-2">Connected apps</h2>
        {apps === null ? (
          <p className="text-xs text-text-tertiary">Loading…</p>
        ) : apps.length === 0 ? (
          <p className="text-xs text-text-tertiary">No apps are connected to your account.</p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {apps.map((app) => (
              <li key={app.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-sm text-text-primary truncate">{app.client_name}</p>
                  <p className="text-2xs text-text-tertiary">
                    {app.scopes.includes(SCOPE_WRITE) ? 'Can make changes' : 'Read-only'}
                    {' · '}connected {formatRelativeTime(app.created_at)}
                    {app.last_used_at && <> · last used {formatRelativeTime(app.last_used_at)}</>}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => disconnect(app)}
                  loading={revoking === app.id}
                  disabled={revoking !== null}
                >
                  Disconnect
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
