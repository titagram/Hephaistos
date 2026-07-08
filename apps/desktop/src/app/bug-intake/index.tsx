import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'

import { type HadesBackendStatusPayload } from '@/app/chat/composer/status-stack/hades-backend-status'
import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { Panel, PanelHeader } from '@/app/overlays/panel'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'

const MAX_EVIDENCE_FILE_BYTES = 64_000
const BUG_INTAKE_TIMEOUT_MS = 15_000

interface BugIntakeViewProps {
  onClose: () => void
}

interface BugIntakeResponse {
  agent_id?: string
  bug_report_id?: string
  evidence_ids?: Array<null | string>
  ok?: boolean
  project_id?: string
  status?: string
  summary?: string
  workspace_binding_id?: string
}

interface BugIntakeForm {
  actual: string
  deployCommit: string
  environment: string
  expected: string
  failingTest: string
  requestMethod: string
  requestUrl: string
  responseStatus: string
  runtimeLog: string
  severity: string
  steps: string
  symptom: string
  title: string
}

interface WorkspaceBindingOption {
  awarenessStatus: string
  displayPath: string
  headCommit: string
  id: string
  projectId: string
  sourceFreeReady: boolean
  status: string
}

const EMPTY_FORM: BugIntakeForm = {
  actual: '',
  deployCommit: '',
  environment: '',
  expected: '',
  failingTest: '',
  requestMethod: 'GET',
  requestUrl: '',
  responseStatus: '',
  runtimeLog: '',
  severity: 'medium',
  steps: '',
  symptom: '',
  title: ''
}

const METHOD_OPTIONS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'] as const
const SEVERITY_OPTIONS = ['low', 'medium', 'high', 'critical'] as const

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' ? (value as Record<string, unknown>) : null

const asText = (value: unknown): string => (typeof value === 'string' ? value : '')

function bindingOptions(status: HadesBackendStatusPayload | null): WorkspaceBindingOption[] {
  const bindings = Array.isArray(status?.bindings) ? status.bindings : []

  return bindings
    .map(raw => {
      const binding = asRecord(raw)

      if (!binding) {
        return null
      }

      const id = asText(binding.workspace_binding_id).trim()

      if (!id) {
        return null
      }

      const awareness = asRecord(binding.awareness)

      return {
        awarenessStatus: asText(awareness?.status) || 'unknown',
        displayPath: asText(binding.display_path) || id,
        headCommit: asText(binding.head_commit),
        id,
        projectId: asText(binding.project_id),
        sourceFreeReady: awareness?.diagnosable_without_source === true,
        status: asText(binding.status) || 'unknown'
      }
    })
    .filter((item): item is WorkspaceBindingOption => item !== null)
}

function currentBindingId(status: HadesBackendStatusPayload | null): string {
  const identity = asRecord(status?.identity)
  const workspaceBinding = asRecord(identity?.workspace_binding)

  return asText(workspaceBinding?.current_workspace_binding_id)
}

function truncateMiddle(value: string, max = 72): string {
  if (value.length <= max) {
    return value
  }

  const edge = Math.floor((max - 3) / 2)

  return `${value.slice(0, edge)}...${value.slice(-edge)}`
}

export function redactBugIntakePreview(value: string): string {
  return value
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b/gi, 'Bearer [redacted]')
    .replace(/\b(?:api[_-]?key|token|secret|password)=([^&\s]{6,})/gi, match =>
      match.replace(/=([^&\s]+)/, '=[redacted]')
    )
    .replace(/\b(?:sk|pk|ghp|gho|github_pat)_[A-Za-z0-9_]{12,}\b/g, '[redacted-token]')
}

function selectedBinding(options: WorkspaceBindingOption[], selectedId: string): WorkspaceBindingOption | null {
  return options.find(option => option.id === selectedId) ?? options[0] ?? null
}

function compactPreview(title: string, value: string): string | null {
  const clean = value.trim()

  if (!clean) {
    return null
  }

  return `${title}\n${redactBugIntakePreview(clean).slice(0, 1400)}`
}

async function readEvidenceFile(file: File): Promise<{ text: string; truncated: boolean }> {
  const truncated = file.size > MAX_EVIDENCE_FILE_BYTES
  const text = await file.slice(0, MAX_EVIDENCE_FILE_BYTES).text()

  return {
    text: truncated ? `${text}\n\n[truncated after ${MAX_EVIDENCE_FILE_BYTES} bytes]` : text,
    truncated
  }
}

function Field({
  children,
  className,
  hint,
  label,
  required
}: {
  children: ReactNode
  className?: string
  hint?: string
  label: string
  required?: boolean
}) {
  return (
    <label className={cn('flex min-w-0 flex-col gap-1.5 text-xs text-(--ui-text-secondary)', className)}>
      <span className="flex items-center gap-1 font-medium text-foreground/88">
        {label}
        {required ? <span className="text-destructive">*</span> : null}
      </span>
      {children}
      {hint ? <span className="text-[0.7rem] leading-4 text-muted-foreground/72">{hint}</span> : null}
    </label>
  )
}

function Section({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="border-t border-(--ui-stroke-secondary)/70 py-3 first:border-t-0 first:pt-0">
      <h3 className="mb-2 text-xs font-semibold text-foreground">{title}</h3>
      {children}
    </section>
  )
}

export function BugIntakeView({ onClose }: BugIntakeViewProps) {
  const { requestGateway } = useGatewayRequest()
  const [form, setForm] = useState<BugIntakeForm>(EMPTY_FORM)
  const [status, setStatus] = useState<HadesBackendStatusPayload | null>(null)
  const [selectedBindingId, setSelectedBindingId] = useState('')
  const [loadingStatus, setLoadingStatus] = useState(true)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<BugIntakeResponse | null>(null)

  const bindings = useMemo(() => bindingOptions(status), [status])
  const activeBinding = useMemo(() => selectedBinding(bindings, selectedBindingId), [bindings, selectedBindingId])
  const canSubmit = Boolean(form.title.trim() && form.symptom.trim()) && !submitting

  useEffect(() => {
    let cancelled = false

    void requestGateway<HadesBackendStatusPayload>('backend.status', {}, 5_000)
      .then(payload => {
        if (cancelled) {
          return
        }

        setStatus(payload)
        const current = currentBindingId(payload)
        const options = bindingOptions(payload)
        setSelectedBindingId(current || options[0]?.id || '')
      })
      .catch(error => {
        if (!cancelled) {
          setStatusError(error instanceof Error ? error.message : String(error))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingStatus(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [requestGateway])

  const updateField = useCallback(
    (field: keyof BugIntakeForm) => (value: string) => {
      setResult(null)
      setForm(current => ({ ...current, [field]: value }))
    },
    []
  )

  const attachFile = useCallback(
    async (field: 'failingTest' | 'runtimeLog', file: File | null) => {
      if (!file) {
        return
      }

      try {
        const { text, truncated } = await readEvidenceFile(file)
        updateField(field)(text)
        notify({
          kind: 'success',
          message: truncated ? `${file.name} imported and truncated to 64 KB.` : `${file.name} imported.`
        })
      } catch (error) {
        notifyError(error, 'Could not read evidence file')
      }
    },
    [updateField]
  )

  const evidencePreview = useMemo(
    () =>
      [
        compactPreview('Failing test', form.failingTest),
        compactPreview('Runtime log', form.runtimeLog),
        compactPreview('HTTP', [form.requestMethod, form.requestUrl, form.responseStatus].filter(Boolean).join(' ')),
        compactPreview(
          'Deploy',
          form.deployCommit ? [form.deployCommit, activeBinding?.headCommit].filter(Boolean).join(' -> ') : ''
        )
      ]
        .filter((item): item is string => item !== null)
        .join('\n\n'),
    [
      activeBinding?.headCommit,
      form.deployCommit,
      form.failingTest,
      form.requestMethod,
      form.requestUrl,
      form.responseStatus,
      form.runtimeLog
    ]
  )

  const submit = useCallback(async () => {
    if (!canSubmit) {
      return
    }

    setSubmitting(true)
    setResult(null)

    try {
      const response = await requestGateway<BugIntakeResponse>(
        'backend.bug_intake',
        {
          actual: form.actual,
          deploy_commit: form.deployCommit,
          environment: form.environment,
          expected: form.expected,
          failing_test: form.failingTest,
          request_method: form.requestMethod,
          request_url: form.requestUrl,
          response_status: form.responseStatus,
          runtime_log: form.runtimeLog,
          severity: form.severity,
          steps: form.steps,
          symptom: form.symptom,
          title: form.title,
          workspace_binding_id: activeBinding?.id || selectedBindingId || undefined,
          workspace_head: activeBinding?.headCommit || undefined
        },
        BUG_INTAKE_TIMEOUT_MS
      )

      setResult(response)
      notify({ kind: 'success', message: response.summary || 'Bug report created.' })
    } catch (error) {
      notifyError(error, 'Bug intake failed')
    } finally {
      setSubmitting(false)
    }
  }, [activeBinding?.headCommit, activeBinding?.id, canSubmit, form, requestGateway, selectedBindingId])

  return (
    <Panel contentClassName="flex h-full min-h-0 flex-col" onClose={onClose}>
      <PanelHeader
        actions={
          <Button disabled={!canSubmit} onClick={() => void submit()} size="sm" type="button">
            {submitting ? <Codicon className="animate-spin" name="loading" size="0.9rem" /> : <Codicon name="bug" />}
            Create
          </Button>
        }
        subtitle="Hades backend evidence intake"
        title="Bug intake"
      />

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_18rem] gap-4 overflow-hidden max-[54rem]:grid-cols-1">
        <div className="min-h-0 overflow-y-auto pr-1">
          <Section title="Target">
            <div className="grid grid-cols-[minmax(0,1fr)_9rem] gap-2 max-[42rem]:grid-cols-1">
              <Field
                hint={
                  loadingStatus
                    ? 'Loading backend bindings.'
                    : statusError
                      ? statusError
                      : activeBinding
                        ? `${activeBinding.status} · ${activeBinding.awarenessStatus}`
                        : 'No linked workspace binding found.'
                }
                label="Workspace"
              >
                <Select
                  disabled={loadingStatus || bindings.length === 0}
                  onValueChange={setSelectedBindingId}
                  value={selectedBindingId || bindings[0]?.id || ''}
                >
                  <SelectTrigger size="sm">
                    <SelectValue placeholder={loadingStatus ? 'Loading...' : 'Current workspace'} />
                  </SelectTrigger>
                  <SelectContent>
                    {bindings.map(binding => (
                      <SelectItem key={binding.id} value={binding.id}>
                        {truncateMiddle(binding.displayPath)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>

              <Field label="Severity">
                <Select onValueChange={updateField('severity')} value={form.severity}>
                  <SelectTrigger size="sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SEVERITY_OPTIONS.map(option => (
                      <SelectItem key={option} value={option}>
                        {option}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
          </Section>

          <Section title="Bug">
            <div className="grid gap-2">
              <Field label="Title" required>
                <Input onChange={event => updateField('title')(event.target.value)} size="sm" value={form.title} />
              </Field>
              <Field label="Symptom" required>
                <Textarea
                  className="min-h-20 resize-y"
                  onChange={event => updateField('symptom')(event.target.value)}
                  size="sm"
                  value={form.symptom}
                />
              </Field>
              <div className="grid grid-cols-2 gap-2 max-[42rem]:grid-cols-1">
                <Field label="Expected">
                  <Textarea
                    className="min-h-16 resize-y"
                    onChange={event => updateField('expected')(event.target.value)}
                    size="sm"
                    value={form.expected}
                  />
                </Field>
                <Field label="Actual">
                  <Textarea
                    className="min-h-16 resize-y"
                    onChange={event => updateField('actual')(event.target.value)}
                    size="sm"
                    value={form.actual}
                  />
                </Field>
              </div>
              <Field label="Reproduction steps">
                <Textarea
                  className="min-h-20 resize-y"
                  onChange={event => updateField('steps')(event.target.value)}
                  size="sm"
                  value={form.steps}
                />
              </Field>
            </div>
          </Section>

          <Section title="Evidence">
            <div className="grid grid-cols-2 gap-2 max-[42rem]:grid-cols-1">
              <Field hint="Paste failure output or import a text file." label="Failing test">
                <Textarea
                  className="min-h-28 resize-y font-mono text-[0.72rem]"
                  onChange={event => updateField('failingTest')(event.target.value)}
                  size="sm"
                  value={form.failingTest}
                />
                <Input
                  className="mt-1"
                  onChange={event => void attachFile('failingTest', event.target.files?.[0] ?? null)}
                  size="xs"
                  type="file"
                />
              </Field>
              <Field hint="Paste relevant logs only; secrets are redacted before upload." label="Runtime log">
                <Textarea
                  className="min-h-28 resize-y font-mono text-[0.72rem]"
                  onChange={event => updateField('runtimeLog')(event.target.value)}
                  size="sm"
                  value={form.runtimeLog}
                />
                <Input
                  className="mt-1"
                  onChange={event => void attachFile('runtimeLog', event.target.files?.[0] ?? null)}
                  size="xs"
                  type="file"
                />
              </Field>
            </div>
          </Section>

          <Section title="Runtime context">
            <div className="grid grid-cols-[8rem_minmax(0,1fr)_7rem] gap-2 max-[42rem]:grid-cols-1">
              <Field label="Method">
                <Select onValueChange={updateField('requestMethod')} value={form.requestMethod}>
                  <SelectTrigger size="sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {METHOD_OPTIONS.map(option => (
                      <SelectItem key={option} value={option}>
                        {option}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Request URL">
                <Input
                  onChange={event => updateField('requestUrl')(event.target.value)}
                  size="sm"
                  value={form.requestUrl}
                />
              </Field>
              <Field label="Status">
                <Input
                  inputMode="numeric"
                  onChange={event => updateField('responseStatus')(event.target.value.replace(/[^\d]/g, ''))}
                  size="sm"
                  value={form.responseStatus}
                />
              </Field>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2 max-[42rem]:grid-cols-1">
              <Field label="Environment">
                <Input
                  onChange={event => updateField('environment')(event.target.value)}
                  placeholder="production, staging, local"
                  size="sm"
                  value={form.environment}
                />
              </Field>
              <Field label="Deploy commit">
                <Input
                  className="font-mono text-[0.72rem]"
                  onChange={event => updateField('deployCommit')(event.target.value)}
                  size="sm"
                  value={form.deployCommit}
                />
              </Field>
            </div>
          </Section>
        </div>

        <aside className="flex min-h-0 flex-col gap-3 border-l border-(--ui-stroke-secondary)/70 pl-4 max-[54rem]:border-l-0 max-[54rem]:border-t max-[54rem]:pl-0 max-[54rem]:pt-3">
          <div className="min-w-0">
            <h3 className="text-xs font-semibold text-foreground">Backend target</h3>
            <div className="mt-2 space-y-1.5 text-xs text-muted-foreground">
              <p className="truncate">
                Project: <span className="font-mono text-foreground/80">{activeBinding?.projectId || 'unknown'}</span>
              </p>
              <p className="truncate">
                Binding: <span className="font-mono text-foreground/80">{activeBinding?.id || 'current'}</span>
              </p>
              <p className="truncate">
                Head:{' '}
                <span className="font-mono text-foreground/80">
                  {activeBinding?.headCommit ? activeBinding.headCommit.slice(0, 12) : 'unknown'}
                </span>
              </p>
            </div>
          </div>

          <div className="min-h-0 flex-1">
            <h3 className="mb-2 text-xs font-semibold text-foreground">Redacted preview</h3>
            <pre className="h-full min-h-32 overflow-auto whitespace-pre-wrap rounded-[3px] border border-(--ui-stroke-secondary)/70 bg-(--ui-bg-secondary) p-2 text-[0.7rem] leading-4 text-muted-foreground">
              {evidencePreview || 'No evidence captured yet.'}
            </pre>
          </div>

          {result ? (
            <div className="rounded-[3px] border border-emerald-500/35 bg-emerald-500/8 p-2 text-xs text-emerald-700 dark:text-emerald-300">
              <div className="flex items-center gap-1.5 font-medium">
                <Codicon name="check" size="0.85rem" />
                {result.summary || 'Bug report created'}
              </div>
              <div className="mt-1 space-y-0.5 font-mono text-[0.68rem]">
                <p>{result.bug_report_id || 'bug_report_id unavailable'}</p>
                <p>{(result.evidence_ids ?? []).length} evidence item(s)</p>
              </div>
            </div>
          ) : null}

          <div className="flex shrink-0 justify-end gap-2 border-t border-(--ui-stroke-secondary)/70 pt-3">
            <Button onClick={onClose} size="sm" type="button" variant="text">
              Close
            </Button>
            <Button disabled={!canSubmit} onClick={() => void submit()} size="sm" type="button">
              {submitting ? (
                <Codicon className="animate-spin" name="loading" size="0.9rem" />
              ) : (
                <Codicon name="cloud-upload" />
              )}
              Send
            </Button>
          </div>
        </aside>
      </div>
    </Panel>
  )
}
