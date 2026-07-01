export interface HadesBackendStatusPayload {
  actions?: unknown
  configured?: unknown
  degraded?: unknown
  inbox_counts?: Record<string, unknown>
  job_counts?: Record<string, unknown>
  proposal_counts?: Record<string, unknown>
  sync?: {
    last_error?: Record<string, unknown> | null
    last_summary?: Record<string, unknown> | null
  }
}

export interface HadesBackendStatusSummary {
  detail: string
  label: string
  tone: 'danger' | 'warning'
}

const count = (source: Record<string, unknown> | undefined, key: string): number => {
  const value = source?.[key]
  const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Number.parseInt(value, 10) : 0

  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

export function summarizeHadesBackendStatus(
  payload: HadesBackendStatusPayload | null | undefined
): HadesBackendStatusSummary | null {
  if (!payload?.configured) {
    return null
  }

  const jobCounts = payload.job_counts
  const proposalCounts = payload.proposal_counts
  const inboxCounts = payload.inbox_counts
  const lastError = payload.sync?.last_error
  const waiting = count(jobCounts, 'waiting_confirmation')
  const refused = count(proposalCounts, 'refused') + count(proposalCounts, 'conflicted')
  const unread = count(inboxCounts, 'unread')

  const actions = Array.isArray(payload.actions)
    ? payload.actions.filter((item): item is string => typeof item === 'string')
    : []

  const parts: string[] = []

  if (waiting) {
    parts.push(`${waiting} waiting job${waiting === 1 ? '' : 's'}`)
  }

  if (refused) {
    parts.push(`${refused} proposal${refused === 1 ? '' : 's'} need review`)
  }

  if (unread) {
    parts.push(`${unread} inbox event${unread === 1 ? '' : 's'}`)
  }

  if (lastError) {
    parts.push('sync error')
  }

  if (!payload.degraded && parts.length === 0 && actions.length === 0) {
    return null
  }

  return {
    detail: (actions[0] ?? parts.join(' · ')) || 'Backend needs attention',
    label: 'Hades backend',
    tone: lastError ? 'danger' : 'warning'
  }
}
