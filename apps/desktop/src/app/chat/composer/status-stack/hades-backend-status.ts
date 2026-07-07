export interface HadesBackendStatusPayload {
  actions?: unknown
  awareness?: {
    status?: unknown
    diagnosable_without_source_bindings?: unknown
    bindings?: unknown
  }
  bindings?: unknown
  configured?: unknown
  degraded?: unknown
  identity?: unknown
  inbox_counts?: Record<string, unknown>
  job_counts?: Record<string, unknown>
  proposal_counts?: Record<string, unknown>
  sync?: {
    last_error?: Record<string, unknown> | null
    last_error_updated_at?: number | null
    last_summary?: Record<string, unknown> | null
    last_summary_updated_at?: number | null
    background?: Record<string, unknown> | null
    background_updated_at?: number | null
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
  const awarenessStatus = typeof payload.awareness?.status === 'string' ? payload.awareness.status : null
  const waiting = count(jobCounts, 'waiting_confirmation')
  const refused = count(proposalCounts, 'refused') + count(proposalCounts, 'conflicted')
  const unread = count(inboxCounts, 'unread')
  const diagnosableBindings = count(payload.awareness, 'diagnosable_without_source_bindings')
  const bindingCount = count(payload.awareness, 'bindings')

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

  if (awarenessStatus && ['partial', 'degraded', 'unmapped'].includes(awarenessStatus)) {
    parts.push(
      bindingCount
        ? `awareness ${awarenessStatus} (${diagnosableBindings}/${bindingCount} source-free ready)`
        : `awareness ${awarenessStatus}`
    )
  }

  if (!payload.degraded && parts.length === 0 && actions.length === 0) {
    return null
  }

  return {
    detail: (actions[0] ?? parts.join(' · ')) || 'Backend needs attention',
    label: 'Hades backend',
    tone: lastError || awarenessStatus === 'degraded' ? 'danger' : 'warning'
  }
}
