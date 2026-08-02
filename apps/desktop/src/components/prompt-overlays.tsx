'use client'

import { useStore } from '@nanostores/react'
import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'

import { PendingApprovalFallback } from '@/components/assistant-ui/tool-approval'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { KeyRound, Loader2, Lock } from '@/lib/icons'
import { buildTelosRespondParams } from '@/lib/telos-approval'
import { $gateway } from '@/store/gateway'
import { notifyError } from '@/store/notifications'
import {
  $secretRequest,
  $sudoRequest,
  $telosApprovalRequest,
  clearSecretRequest,
  clearSudoRequest,
  clearTelosApprovalRequest
} from '@/store/prompts'

// Renders the modal mid-turn prompts the gateway raises and waits on: sudo
// password and skill secret capture. Dangerous-command / execute_code approval
// prefers the pending tool row, but also has a chat-level fallback when no row
// is mounted (remote gateway sessions can raise the request before the matching
// tool call is visible). Each Python-side caller blocks the agent thread until
// the matching `*.respond` RPC lands; without a renderer the agent stalls until
// its timeout and the tool is BLOCKED. Any close path (Esc, backdrop
// click) funnels through Radix's single `onOpenChange(false)` and maps to a
// refusal, so silence is never mistaken for consent, matching the TUI. We
// deliberately do NOT add onEscapeKeyDown / onInteractOutside handlers — they'd
// fire a second `*.respond` alongside onOpenChange (double-send) or block the
// backdrop-dismiss path.

function SudoDialog() {
  const { t } = useI18n()
  const copy = t.prompts
  const request = useStore($sudoRequest)
  const gateway = useStore($gateway)
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setPassword('')
    setSubmitting(false)
  }, [request?.requestId])

  const send = useCallback(
    async (value: string) => {
      if (!request) {
        return
      }

      if (!gateway) {
        notifyError(new Error(copy.gatewayDisconnected), copy.sudoSendFailed)

        return
      }

      setSubmitting(true)

      try {
        await gateway.request<{ status?: string }>('sudo.respond', {
          password: value,
          request_id: request.requestId
        })
        triggerHaptic('submit')
        clearSudoRequest(request.sessionId, request.requestId)
      } catch (error) {
        notifyError(error, copy.sudoSendFailed)
        setSubmitting(false)
      }
    },
    [copy.gatewayDisconnected, copy.sudoSendFailed, gateway, request]
  )

  // Cancel → empty password. The backend treats an empty sudo response as a
  // failed sudo (no command runs), so closing the dialog is a safe refusal.
  const onOpenChange = useCallback(
    (open: boolean) => {
      if (!open && !submitting && request) {
        void send('')
      }
    },
    [request, send, submitting]
  )

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      void send(password)
    },
    [password, send]
  )

  if (!request) {
    return null
  }

  return (
    <Dialog onOpenChange={onOpenChange} open>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle icon={Lock}>{copy.sudoTitle}</DialogTitle>
          <DialogDescription>{copy.sudoDesc}</DialogDescription>
        </DialogHeader>

        <form className="grid gap-3" onSubmit={onSubmit}>
          <Input
            autoFocus
            disabled={submitting}
            onChange={event => setPassword(event.target.value)}
            placeholder={copy.sudoPlaceholder}
            type="password"
            value={password}
          />
          <DialogFooter>
            <Button disabled={submitting} onClick={() => void send('')} type="button" variant="ghost">
              {t.common.cancel}
            </Button>
            <Button disabled={submitting} type="submit">
              {submitting ? <Loader2 className="size-3.5 animate-spin" /> : t.common.send}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function TelosApprovalDialog() {
  const { t } = useI18n()
  const copy = t.assistant.telos
  const request = useStore($telosApprovalRequest)
  const gateway = useStore($gateway)
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)

  useEffect(() => {
    submittingRef.current = false
    setSubmitting(false)
  }, [request?.requestId])

  const send = useCallback(
    async (choice: 'approved' | 'denied') => {
      if (!request || submittingRef.current) {
        return
      }

      if (!gateway) {
        notifyError(new Error(copy.gatewayDisconnected), copy.sendFailed)

        return
      }

      submittingRef.current = true
      setSubmitting(true)

      const params = buildTelosRespondParams(request, choice)

      if (!params) {
        submittingRef.current = false
        setSubmitting(false)

        return
      }

      try {
        await gateway.request<{ status?: string }>('approval.respond', params)
        triggerHaptic('submit')
        clearTelosApprovalRequest(request.sessionId, request.requestId)
      } catch (error) {
        notifyError(error, copy.sendFailed)
        submittingRef.current = false
        setSubmitting(false)
      }
    },
    [copy.gatewayDisconnected, copy.sendFailed, gateway, request]
  )

  // Esc / backdrop click → explicit deny without double-send. Matches SudoDialog
  // and SecretDialog: Radix fires onOpenChange(false) exactly once, we send the
  // refusal, then the store clears and the dialog unmounts.
  const onOpenChange = useCallback(
    (open: boolean) => {
      if (!open && !submittingRef.current && request) {
        void send('denied')
      }
    },
    [request, send]
  )

  if (!request) {
    return null
  }

  const title = request.action === 'activate' ? copy.activateTitle : copy.rollbackTitle

  return (
    <Dialog onOpenChange={onOpenChange} open>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{request.boundedSummary}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-2 text-sm">
          <div className="flex flex-col gap-0.5">
            <span className="text-muted-foreground text-xs">{copy.digest}</span>
            <span className="font-mono text-xs break-all">{request.digest}</span>
          </div>

          <div className="flex flex-col gap-0.5">
            <span className="text-muted-foreground text-xs">{copy.summary}</span>
            <span>{request.boundedSummary}</span>
          </div>

          <div className="flex gap-4">
            <div className="flex flex-col gap-0.5">
              <span className="text-muted-foreground text-xs">{copy.nonce}</span>
              <span className="font-mono text-xs">{request.nonce}</span>
            </div>

            {request.expiresAt ? (
              <div className="flex flex-col gap-0.5">
                <span className="text-muted-foreground text-xs">{copy.expires}</span>
                <span className="font-mono text-xs">{request.expiresAt}</span>
              </div>
            ) : null}
          </div>
        </div>

        <DialogFooter>
          <Button disabled={submitting} onClick={() => void send('denied')} type="button" variant="ghost">
            {copy.deny}
          </Button>
          <Button disabled={submitting} onClick={() => void send('approved')} type="button">
            {copy.approve}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function SecretDialog() {
  const { t } = useI18n()
  const copy = t.prompts
  const request = useStore($secretRequest)
  const gateway = useStore($gateway)
  const [value, setValue] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setValue('')
    setSubmitting(false)
  }, [request?.requestId])

  const send = useCallback(
    async (secret: string) => {
      if (!request) {
        return
      }

      if (!gateway) {
        notifyError(new Error(copy.gatewayDisconnected), copy.secretSendFailed)

        return
      }

      setSubmitting(true)

      try {
        await gateway.request<{ status?: string }>('secret.respond', {
          request_id: request.requestId,
          session_id: request.sessionId ?? '',
          value: secret
        })
        triggerHaptic('submit')
        clearSecretRequest(request.sessionId, request.requestId)
      } catch (error) {
        notifyError(error, copy.secretSendFailed)
        setSubmitting(false)
      }
    },
    [copy.gatewayDisconnected, copy.secretSendFailed, gateway, request]
  )

  const onOpenChange = useCallback(
    (open: boolean) => {
      if (!open && !submitting && request) {
        void send('')
      }
    },
    [request, send, submitting]
  )

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      void send(value)
    },
    [send, value]
  )

  if (!request) {
    return null
  }

  return (
    <Dialog onOpenChange={onOpenChange} open>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle icon={KeyRound}>{request.envVar || copy.secretTitle}</DialogTitle>
          <DialogDescription>{request.prompt || copy.secretDesc}</DialogDescription>
        </DialogHeader>

        <form className="grid gap-3" onSubmit={onSubmit}>
          <Input
            autoFocus
            disabled={submitting}
            onChange={event => setValue(event.target.value)}
            placeholder={request.envVar || copy.secretPlaceholder}
            type="password"
            value={value}
          />
          <DialogFooter>
            <Button disabled={submitting} onClick={() => void send('')} type="button" variant="ghost">
              {t.common.cancel}
            </Button>
            <Button disabled={submitting || !value} type="submit">
              {submitting ? <Loader2 className="size-3.5 animate-spin" /> : t.common.send}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function PromptOverlays() {
  return (
    <>
      <PendingApprovalFallback />
      <TelosApprovalDialog />
      <SudoDialog />
      <SecretDialog />
    </>
  )
}
