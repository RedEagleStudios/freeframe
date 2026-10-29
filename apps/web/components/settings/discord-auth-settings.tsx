"use client";

import * as React from "react";
import useSWR, { mutate } from "swr";
import { Check, Copy } from "lucide-react";
import { copyToClipboard } from "@/lib/utils";
import {
  fetchDiscordAuthConfig,
  parseRoleIds,
  updateDiscordAuthConfig,
  type DiscordAuthConfig,
  type DiscordAuthConfigUpdate,
} from "@/lib/discord-auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/shared/toast";

const KEY = "/admin/auth/discord";

/**
 * Superadmin card for the Discord OAuth app behind "Continue with Discord".
 * The API never returns the secret, only whether one is stored, so the secret
 * field starts empty and is only sent when something was typed into it.
 */
export function DiscordAuthSettings() {
  const { data } = useSWR<DiscordAuthConfig>(KEY, fetchDiscordAuthConfig);
  const toast = useToast();

  const [enabled, setEnabled] = React.useState(false);
  const [clientId, setClientId] = React.useState("");
  const [secret, setSecret] = React.useState("");
  const [serverId, setServerId] = React.useState("");
  const [roles, setRoles] = React.useState("");
  const [rolesError, setRolesError] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [copied, setCopied] = React.useState(false);

  // Follow the saved values whenever a fresh config arrives (load or save).
  React.useEffect(() => {
    if (!data) return;
    setEnabled(data.enabled);
    setClientId(data.client_id ?? "");
    setSecret("");
    setServerId(data.server_id ?? "");
    setRoles(data.allowed_role_ids.join("\n"));
    setRolesError("");
  }, [data]);

  const send = async (update: DiscordAuthConfigUpdate, success: string) => {
    setSaving(true);
    try {
      const updated = await updateDiscordAuthConfig(update);
      mutate(KEY, updated, { revalidate: false });
      toast.success(success);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to save Discord sign-in settings");
    } finally {
      setSaving(false);
    }
  };

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    const server = serverId.trim();
    const roleIds = parseRoleIds(roles);
    // Roles are checked on the server's members, so they mean nothing without one.
    if (roleIds.length > 0 && !server) {
      setRolesError("Set a Server ID to restrict sign-in by role.");
      return;
    }
    setRolesError("");
    const update: DiscordAuthConfigUpdate = {
      enabled,
      client_id: clientId.trim() || null,
      server_id: server || null,
      allowed_role_ids: roleIds,
    };
    // Blank keeps the stored secret: it is never sent back to be shown here.
    if (secret.trim()) update.client_secret = secret.trim();
    void send(update, "Discord sign-in settings saved");
  };

  const handleRemoveSecret = () => {
    void send({ client_secret: "" }, "Client secret removed");
  };

  const handleCopy = async () => {
    if (!data?.redirect_uri) return;
    if (await copyToClipboard(data.redirect_uri)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <section className="max-w-md space-y-4 rounded-lg border border-border p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">Sign in with Discord</h2>
          <p className="mt-0.5 text-xs text-text-tertiary">
            Lets people sign in with the Discord account for their email.
          </p>
        </div>
        <Switch
          aria-label="Enable Sign in with Discord"
          checked={enabled}
          onCheckedChange={setEnabled}
          disabled={!data || saving}
        />
      </div>

      {data?.configured_via === "environment" && (
        <p className="rounded-md border border-border bg-bg-tertiary px-3 py-2 text-xs text-text-secondary">
          These values come from the server&apos;s environment variables. Saving here stores them in
          the database, which then takes precedence.
        </p>
      )}

      <form onSubmit={handleSave} className="flex flex-col gap-4">
        <Input
          id="discord-client-id"
          label="Client ID"
          placeholder="123456789012345678"
          autoComplete="off"
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          disabled={!data || saving}
        />

        <div className="flex flex-col gap-1.5">
          <Input
            id="discord-client-secret"
            label="Client secret"
            type="password"
            autoComplete="new-password"
            placeholder={data?.client_secret_set ? "Saved — leave blank to keep" : "Client secret"}
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            disabled={!data || saving}
          />
          {data?.client_secret_set && (
            <button
              type="button"
              onClick={handleRemoveSecret}
              disabled={saving}
              className="self-start text-xs text-status-error transition-colors hover:underline disabled:opacity-50"
            >
              Remove secret
            </button>
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <Input
            id="discord-server-id"
            label="Server ID"
            placeholder="Optional"
            autoComplete="off"
            value={serverId}
            onChange={(e) => {
              setServerId(e.target.value);
              setRolesError("");
            }}
            disabled={!data || saving}
          />
          <p className="text-xs text-text-tertiary">
            With a Server ID set, only members of that Discord server can sign in, and anyone in it
            (with an allowed role, if any are listed) gets an account on first sign-in. Without
            one, only people already invited can sign in.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="discord-allowed-roles" className="text-sm font-medium text-text-secondary">
            Allowed role IDs
          </label>
          <textarea
            id="discord-allowed-roles"
            rows={3}
            placeholder="Optional — one per line or comma-separated"
            value={roles}
            onChange={(e) => {
              setRoles(e.target.value);
              setRolesError("");
            }}
            disabled={!data || saving}
            className="w-full rounded-md border border-border bg-bg-secondary px-3 py-2 font-mono text-xs text-text-primary placeholder:font-sans placeholder:text-text-tertiary transition-all duration-150 focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
          />
          {rolesError ? (
            <p className="text-xs text-status-error">{rolesError}</p>
          ) : (
            <p className="text-xs text-text-tertiary">
              Leave empty to let every member of the server in.
            </p>
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="discord-redirect-uri" className="text-sm font-medium text-text-secondary">
            Redirect URI
          </label>
          <div className="flex gap-2">
            <div className="min-w-0 flex-1">
              <Input id="discord-redirect-uri" readOnly value={data?.redirect_uri ?? ""} className="font-mono text-xs" />
            </div>
            <Button
              type="button"
              variant="secondary"
              onClick={handleCopy}
              disabled={!data?.redirect_uri}
              aria-label="Copy redirect URI"
              className="h-10"
            >
              {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            </Button>
          </div>
          <p className="text-xs text-text-tertiary">
            Add this in the Discord Developer Portal &gt; Applications &gt; OAuth2 &gt; Redirects.
          </p>
        </div>

        <Button type="submit" loading={saving} disabled={!data} className="self-start">
          Save
        </Button>
      </form>
    </section>
  );
}
