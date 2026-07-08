import { describe, expect, it } from 'vitest'

import { summarizeHadesBackendStatus } from './hades-backend-status'

describe('summarizeHadesBackendStatus', () => {
  it('stays hidden when the backend is not configured or healthy', () => {
    expect(summarizeHadesBackendStatus({ configured: false })).toBeNull()
    expect(
      summarizeHadesBackendStatus({
        configured: true,
        degraded: false,
        inbox_counts: { total: 0, unread: 0 },
        job_counts: {},
        proposal_counts: {}
      })
    ).toBeNull()
  })

  it('summarizes waiting jobs, proposals, and inbox events', () => {
    expect(
      summarizeHadesBackendStatus({
        configured: true,
        degraded: true,
        inbox_counts: { total: 3, unread: 2 },
        job_counts: { waiting_confirmation: 1 },
        proposal_counts: { conflicted: 1, refused: 2 }
      })
    ).toEqual({
      detail: '1 waiting job · 3 proposals need review · 2 inbox events',
      label: 'Hades backend',
      tone: 'warning'
    })
  })

  it('uses actionable backend text and marks sync errors as danger', () => {
    expect(
      summarizeHadesBackendStatus({
        actions: ['Inspect last backend sync error and rerun `hades backend sync`.'],
        configured: true,
        degraded: true,
        sync: { last_error: { message: 'timeout' } }
      })
    ).toEqual({
      detail: 'Inspect last backend sync error and rerun `hades backend sync`.',
      label: 'Hades backend',
      tone: 'danger'
    })
  })

  it('summarizes incomplete project awareness', () => {
    expect(
      summarizeHadesBackendStatus({
        awareness: {
          bindings: 2,
          diagnosable_without_source_bindings: 0,
          status: 'partial'
        },
        configured: true,
        degraded: false
      })
    ).toEqual({
      detail: 'awareness partial (0/2 source-free ready)',
      label: 'Hades backend',
      tone: 'warning'
    })
  })

  it('summarizes backend task work and worker setup gaps', () => {
    expect(
      summarizeHadesBackendStatus({
        configured: true,
        degraded: false,
        task_work: {
          queued: 2,
          claimed: 1,
          failed: 0,
          missing_shared_memory_context: 1,
          next_step: 'Run `hades backend worker-setup` in this checkout before claiming backend task work.',
          worker_setup: { status: 'missing' }
        }
      })
    ).toEqual({
      detail: 'Run `hades backend worker-setup` in this checkout before claiming backend task work.',
      label: 'Hades backend',
      tone: 'warning'
    })
  })

  it('marks failed backend task work as danger', () => {
    expect(
      summarizeHadesBackendStatus({
        configured: true,
        degraded: true,
        task_work: {
          failed: 1,
          next_step: 'Run `hades backend tasks explain <work_item_id>` on failed items.'
        }
      })
    ).toEqual({
      detail: 'Run `hades backend tasks explain <work_item_id>` on failed items.',
      label: 'Hades backend',
      tone: 'danger'
    })
  })
})
