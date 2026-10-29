'use client'

import { use } from 'react'
import { JoinProject } from '@/components/auth/join-project'

interface JoinPageProps {
  params: Promise<{ token: string }>
}

/** Where a project join link lands. Public: the holder may not have an account yet. */
export default function JoinPage({ params }: JoinPageProps) {
  const { token } = use(params)
  return <JoinProject token={token} />
}
