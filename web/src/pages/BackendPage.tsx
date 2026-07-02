import { type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  Clock,
  Database,
  GitBranch,
  Inbox,
  Link2,
  Play,
  RefreshCw,
  Server,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { api } from "@/lib/api";
import type {
  HadesBackendActionResponse,
  HadesBackendBinding,
  HadesBackendJob,
  HadesBackendMemoryProposal,
  HadesBackendStatus,
} from "@/lib/api";
import { usePageHeader } from "@/contexts/usePageHeader";

function recordTotal(record: Record<string, number> | null | undefined): number {
  return Object.values(record ?? {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

function count(record: Record<string, number> | null | undefined, key: string): number {
  return Number(record?.[key] || 0);
}

function formatAgo(epochSeconds?: number | null): string {
  if (!epochSeconds) return "Never";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
  if (seconds < 60) return "Just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function valueLabel(value: unknown): string {
  if (value === null || value === undefined || value === "") return "None";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function statusTone(status?: string | null): "success" | "warning" | "destructive" | "secondary" | "outline" {
  if (!status) return "outline";
  if (["linked", "completed", "accepted", "ok", "success"].includes(status)) return "success";
  if (["waiting_confirmation", "pending", "degraded", "expired"].includes(status)) return "warning";
  if (["failed", "refused", "conflicted", "error"].includes(status)) return "destructive";
  return "secondary";
}

function Metric({
  icon: Icon,
  label,
  value,
  tone = "secondary",
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  tone?: "success" | "warning" | "destructive" | "secondary" | "outline";
}) {
  return (
    <Card>
      <CardContent className="flex min-h-24 items-center justify-between gap-4 py-4">
        <div className="min-w-0">
          <div className="text-xs uppercase text-muted-foreground">{label}</div>
          <div className="mt-1 truncate text-2xl font-semibold text-foreground">{value}</div>
        </div>
        <Badge tone={tone}>
          <Icon className="h-4 w-4" />
        </Badge>
      </CardContent>
    </Card>
  );
}

function CountList({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, value]) => Number(value || 0) > 0);
  if (entries.length === 0) {
    return <div className="text-sm text-muted-foreground">No items</div>;
  }
  return (
    <div className="grid gap-2">
      {entries.map(([key, value]) => (
        <div className="flex items-center justify-between gap-3 text-sm" key={key}>
          <span className="min-w-0 truncate font-mono text-muted-foreground">{key}</span>
          <Badge tone={statusTone(key)}>{value}</Badge>
        </div>
      ))}
    </div>
  );
}

function BindingRow({ binding }: { binding: HadesBackendBinding }) {
  return (
    <div className="grid gap-2 border-t border-border py-3 first:border-t-0 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {binding.display_path || "Linked workspace"}
        </span>
        <Badge tone={statusTone(binding.status)}>{binding.status || "unknown"}</Badge>
      </div>
      <div className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
        <span className="truncate font-mono">project {binding.project_id || "unknown"}</span>
        <span className="truncate font-mono">binding {binding.workspace_binding_id || "local only"}</span>
      </div>
    </div>
  );
}

function SyncSummary({ status }: { status: HadesBackendStatus }) {
  const summary = status.sync.last_summary ?? {};
  const entries = Object.entries(summary);
  if (entries.length === 0) {
    return <div className="text-sm text-muted-foreground">No sync summary yet</div>;
  }
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {entries.map(([key, value]) => (
        <div className="border border-border bg-background/40 px-3 py-2" key={key}>
          <div className="truncate font-mono text-[0.68rem] text-muted-foreground">{key}</div>
          <div className="mt-1 text-sm font-semibold">{valueLabel(value)}</div>
        </div>
      ))}
    </div>
  );
}

function ReviewEmpty({ label }: { label: string }) {
  return (
    <div className="border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">
      {label}
    </div>
  );
}

function ReviewMeta({ children }: { children: ReactNode }) {
  return <div className="mt-1 truncate font-mono text-xs text-muted-foreground">{children}</div>;
}

export default function BackendPage() {
  const [status, setStatus] = useState<HadesBackendStatus | null>(null);
  const [jobs, setJobs] = useState<HadesBackendJob[]>([]);
  const [proposals, setProposals] = useState<HadesBackendMemoryProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const { toast, showToast } = useToast();
  const { setEnd } = usePageHeader();

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [nextStatus, nextJobs, nextProposals] = await Promise.all([
        api.getHadesBackendStatus(),
        api.getHadesBackendJobs(),
        api.getHadesBackendProposals(),
      ]);
      setStatus(nextStatus);
      setJobs(nextJobs.jobs);
      setProposals(nextProposals.proposals);
    } catch (error) {
      showToast(`Failed to load backend status: ${error}`, "error");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [showToast]);

  useEffect(() => {
    let cancelled = false;

    Promise.all([
      api.getHadesBackendStatus(),
      api.getHadesBackendJobs(),
      api.getHadesBackendProposals(),
    ])
      .then(([nextStatus, nextJobs, nextProposals]) => {
        if (!cancelled) {
          setStatus(nextStatus);
          setJobs(nextJobs.jobs);
          setProposals(nextProposals.proposals);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          showToast(`Failed to load backend status: ${error}`, "error");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [showToast]);

  useLayoutEffect(() => {
    setEnd(
      <Button
        className="uppercase"
        size="sm"
        onClick={() => void load()}
        disabled={refreshing}
        prefix={refreshing ? <Spinner /> : <RefreshCw className="h-4 w-4" />}
      >
        Refresh
      </Button>,
    );
    return () => setEnd(null);
  }, [load, refreshing, setEnd]);

  const nextAction = useMemo(() => {
    if (!status) return "Loading backend status";
    if (!status.configured) return "Run hades backend bootstrap";
    if (status.actions.length > 0) return status.actions[0];
    if (status.bindings.length === 0) return "Link a workspace with hades project link";
    return "No action needed";
  }, [status]);

  const runReviewAction = useCallback(
    async (key: string, action: () => Promise<HadesBackendActionResponse>, success: string) => {
      setBusyAction(key);
      try {
        const result = await action();
        showToast(result.ok ? success : `${result.status}: ${result.summary}`, result.ok ? "success" : "error");
        await load();
      } catch (error) {
        showToast(`Backend action failed: ${error}`, "error");
      } finally {
        setBusyAction(null);
      }
    },
    [load, showToast],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  if (!status) {
    return (
      <div className="flex flex-col gap-4">
        <Toast toast={toast} />
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Backend status unavailable
          </CardContent>
        </Card>
      </div>
    );
  }

  const jobTotal = recordTotal(status.job_counts);
  const proposalTotal = recordTotal(status.proposal_counts);
  const inboxUnread = count(status.inbox_counts, "unread");
  const inboxTotal = count(status.inbox_counts, "total");
  const healthTone = !status.configured ? "outline" : status.degraded ? "warning" : "success";

  return (
    <div className="flex flex-col gap-6">
      <Toast toast={toast} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(18rem,0.6fr)]">
        <Card>
          <CardContent className="grid gap-4 py-5">
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone={healthTone}>
                {status.configured ? (
                  status.degraded ? <AlertTriangle className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />
                ) : (
                  <Server className="h-4 w-4" />
                )}
                {status.configured ? (status.degraded ? "Needs review" : "Healthy") : "Not configured"}
              </Badge>
              <span className="text-sm text-muted-foreground">{nextAction}</span>
            </div>

            <div className="grid gap-2 text-sm sm:grid-cols-2">
              <div className="min-w-0">
                <div className="text-xs uppercase text-muted-foreground">Agent</div>
                <div className="mt-1 truncate font-mono">{status.agent?.label || status.agent?.agent_id || "None"}</div>
              </div>
              <div className="min-w-0">
                <div className="text-xs uppercase text-muted-foreground">Backend URL</div>
                <div className="mt-1 truncate font-mono">{status.agent?.base_url || "Not configured"}</div>
              </div>
              <div className="min-w-0">
                <div className="text-xs uppercase text-muted-foreground">Project</div>
                <div className="mt-1 truncate font-mono">{status.agent?.project_id || "None"}</div>
              </div>
              <div className="min-w-0">
                <div className="text-xs uppercase text-muted-foreground">Capabilities</div>
                <div className="mt-1 truncate font-mono">
                  {status.agent?.capabilities?.length ? status.agent.capabilities.join(", ") : "None"}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="grid gap-3 py-5">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Clock className="h-4 w-4 text-muted-foreground" />
              Sync
            </div>
            <div className="text-2xl font-semibold">{formatAgo(status.sync.last_summary_updated_at)}</div>
            <div className="text-sm text-muted-foreground">
              {status.sync.last_error
                ? "Last sync recorded an error"
                : status.sync.background?.status
                  ? `Background ${valueLabel(status.sync.background.status)}`
                  : "Manual sync available"}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Metric icon={Link2} label="Workspaces" value={status.bindings.length} tone={status.bindings.length ? "success" : "outline"} />
        <Metric icon={GitBranch} label="Jobs" value={jobTotal} tone={count(status.job_counts, "waiting_confirmation") ? "warning" : "secondary"} />
        <Metric icon={Brain} label="Proposals" value={proposalTotal} tone={count(status.proposal_counts, "refused") || count(status.proposal_counts, "conflicted") ? "warning" : "secondary"} />
        <Metric icon={Inbox} label="Inbox unread" value={`${inboxUnread}/${inboxTotal}`} tone={inboxUnread ? "warning" : "secondary"} />
      </div>

      {status.actions.length > 0 && (
        <Card>
          <CardContent className="py-4">
            <H2 variant="sm" className="mb-3 flex items-center gap-2 text-muted-foreground">
              <AlertTriangle className="h-4 w-4" />
              Next actions
            </H2>
            <div className="grid gap-2">
              {status.actions.map((action) => (
                <div className="border border-border bg-background/40 px-3 py-2 text-sm" key={action}>
                  {action}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardContent className="py-4">
            <H2 variant="sm" className="mb-3 flex items-center gap-2 text-muted-foreground">
              <Database className="h-4 w-4" />
              Linked workspaces
            </H2>
            {status.bindings.length === 0 ? (
              <div className="text-sm text-muted-foreground">No linked workspaces</div>
            ) : (
              <div>
                {status.bindings.map((binding, index) => (
                  <BindingRow binding={binding} key={binding.workspace_binding_id || index} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="grid gap-5 py-4">
            <section>
              <H2 variant="sm" className="mb-3 text-muted-foreground">Backend jobs</H2>
              {jobs.length === 0 ? (
                <ReviewEmpty label="No jobs waiting for confirmation" />
              ) : (
                <div className="grid gap-2">
                  {jobs.map((job) => (
                    <div className="border border-border bg-background/40 px-3 py-3" key={job.job_id}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                            <span className="truncate">{job.capability}</span>
                            <Badge tone={statusTone(job.status)}>{job.status}</Badge>
                          </div>
                          <ReviewMeta>
                            {job.job_id} / {job.workspace_binding_id}
                          </ReviewMeta>
                        </div>
                        {job.status === "waiting_confirmation" && (
                          <div className="flex shrink-0 items-center gap-2">
                            <Button
                              size="sm"
                              prefix={
                                busyAction === `job:${job.job_id}:approve` ? (
                                  <Spinner />
                                ) : (
                                  <Play className="h-4 w-4" />
                                )
                              }
                              disabled={busyAction !== null}
                              onClick={() =>
                                void runReviewAction(
                                  `job:${job.job_id}:approve`,
                                  () => api.approveHadesBackendJob(job.job_id),
                                  "Backend job approved",
                                )
                              }
                            >
                              Approve
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              prefix={
                                busyAction === `job:${job.job_id}:refuse` ? (
                                  <Spinner />
                                ) : (
                                  <XCircle className="h-4 w-4" />
                                )
                              }
                              disabled={busyAction !== null}
                              onClick={() =>
                                void runReviewAction(
                                  `job:${job.job_id}:refuse`,
                                  () => api.refuseHadesBackendJob(job.job_id),
                                  "Backend job refused",
                                )
                              }
                            >
                              Refuse
                            </Button>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
            <section>
              <H2 variant="sm" className="mb-3 text-muted-foreground">Memory proposals</H2>
              {proposals.length === 0 ? (
                <ReviewEmpty label="No refused or conflicted proposals" />
              ) : (
                <div className="grid gap-2">
                  {proposals.map((proposal) => (
                    <div className="border border-border bg-background/40 px-3 py-3" key={proposal.proposal_id}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                            <span className="truncate">{proposal.summary}</span>
                            <Badge tone={statusTone(proposal.status)}>{proposal.status}</Badge>
                          </div>
                          <ReviewMeta>
                            {proposal.action} / {proposal.intent}
                            {proposal.reason ? ` / ${proposal.reason}` : ""}
                          </ReviewMeta>
                        </div>
                        {(proposal.status === "refused" || proposal.status === "conflicted") && (
                          <Button
                            size="sm"
                            variant="outline"
                            prefix={
                              busyAction === `proposal:${proposal.proposal_id}:ack` ? (
                                <Spinner />
                              ) : (
                                <CheckCircle2 className="h-4 w-4" />
                              )
                            }
                            disabled={busyAction !== null}
                            onClick={() =>
                              void runReviewAction(
                                `proposal:${proposal.proposal_id}:ack`,
                                () => api.acknowledgeHadesBackendProposal(proposal.proposal_id),
                                "Memory proposal acknowledged",
                              )
                            }
                          >
                            Acknowledge
                          </Button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
            <section>
              <H2 variant="sm" className="mb-3 text-muted-foreground">Persephone inbox</H2>
              <CountList counts={status.inbox_counts} />
            </section>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="grid gap-4 py-4">
          <H2 variant="sm" className="text-muted-foreground">Last sync summary</H2>
          <SyncSummary status={status} />
          {status.sync.last_error && (
            <div className="border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm">
              {Object.entries(status.sync.last_error).map(([key, value]) => (
                <div className="grid gap-1 sm:grid-cols-[10rem_minmax(0,1fr)]" key={key}>
                  <span className="font-mono text-muted-foreground">{key}</span>
                  <span className="min-w-0 break-words">{valueLabel(value)}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
