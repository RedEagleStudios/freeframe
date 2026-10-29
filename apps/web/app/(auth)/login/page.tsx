'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/api'
import { getAccessToken } from '@/lib/auth'
import { safeInternalPath } from '@/lib/safe-redirect'
import { discordLoginError, discordStartUrl, fetchAuthProviders } from '@/lib/discord-auth'
import { LoginForm } from '@/components/auth/login-form'
import { PoweredByBadge } from '@/components/shared/powered-by-badge'
import type { SetupStatus } from '@/types'


export default function LoginPage() {
  const router = useRouter()
  const [discordHref, setDiscordHref] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    async function checkSetup() {
      try {
        const status = await api.get<SetupStatus>('/setup/status')
        if (status.needs_setup) {
          router.replace('/setup')
        }
      } catch {
        // ignore
      }
    }

    const params = new URLSearchParams(window.location.search)
    const token = getAccessToken()
    if (token) {
      document.cookie = `ff_access_token=${token}; path=/; max-age=${60 * 60 * 24 * 7}; SameSite=Lax`
      router.replace(safeInternalPath(params.get('from')))
      return
    }

    checkSetup()

    setNotice(discordLoginError(params.get('error')))
    // Discord sign-in comes back to the page it was started from; the API checks `from` again.
    const from = safeInternalPath(params.get('from'))
    fetchAuthProviders()
      .then((providers) => {
        if (providers.discord) setDiscordHref(discordStartUrl(from))
      })
      .catch(() => {
        // No providers answer: just leave the button out.
      })
  }, [router])

  return (
    <>
      {/* The branded logo + org name live in the (auth) layout, so every auth screen gets them */}
      <LoginForm discordHref={discordHref} notice={notice} />

      {/* Powered by FreeFrame */}
      <PoweredByBadge className="mt-6 text-center justify-center w-full" />
    </>
  )
}
