import { type FormEvent, type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Brain,
  Bug,
  CheckCircle2,
  Clock,
  Database,
  GitBranch,
  Inbox,
  Link2,
  Play,
  RefreshCw,
  Send,
  Server,
  ShieldCheck,
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
  HadesBackendBindingAwareness,
  HadesBackendBugIntakeRequest,
  HadesBackendCoverageItem,
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
  if (["linked", "completed", "accepted", "ok", "present", "ready", "success", "high", "passed"].includes(status)) return "success";
  if (
    [
      "waiting_confirmation",
      "pending",
      "partial",
      "aggregate",
      "missing",
      "unknown",
      "unmapped",
      "unlinked",
      "expired",
      "incomplete",
      "medium",
      "low",
      "stale",
      "blocked",
      "attention",
    ].includes(status)
  ) return "warning";
  if (["failed", "refused", "conflicted", "degraded", "error", "insufficient"].includes(status)) return "destructive";
  return "secondary";
}

function titleLabel(value: string): string {
  return value.replaceAll("_", " ");
}

function coverageValue(item?: HadesBackendCoverageItem): string {
  if (!item) return "0";
  if (typeof item.items === "number") return String(item.items);
  if (typeof item.uploaded_last_sync === "number") return String(item.uploaded_last_sync);
  if (typeof item.items_last_sync === "number") return String(item.items_last_sync);
  return "0";
}

function awarenessReadyCount(status: HadesBackendStatus): string {
  const awareness = status.awareness;
  if (!awareness) return "0/0";
  return `${awareness.diagnosable_without_source_bindings}/${awareness.bindings}`;
}

function qualityMissing(status: HadesBackendStatus): string[] {
  return Array.from(
    new Set(
      status.bindings.flatMap((binding) =>
        Array.isArray(binding.awareness?.quality?.missing) ? binding.awareness.quality.missing : [],
      ),
    ),
  ).sort();
}

function qualityConfidenceCounts(status: HadesBackendStatus): Record<string, number> {
  return status.bindings.reduce<Record<string, number>>((counts, binding) => {
    if (!binding.awareness) return counts;
    const confidence = binding.awareness.quality?.confidence || "unknown";
    counts[confidence] = Number(counts[confidence] || 0) + 1;
    return counts;
  }, {});
}

function latestQualityUpdate(status: HadesBackendStatus): number | null {
  const timestamps = status.bindings
    .map((binding) => binding.awareness?.quality?.last_sync_summary_updated_at)
    .filter((value): value is number => typeof value === "number" && value > 0);
  return timestamps.length ? Math.max(...timestamps) : null;
}

function sourceFreeReadyCount(status: HadesBackendStatus): number {
  return status.awareness?.diagnosable_without_source_bindings
    ?? status.bindings.filter((binding) => Boolean(binding.awareness?.diagnosable_without_source)).length;
}

function coverageReady(status: HadesBackendStatus, key: "source_slices" | "bug_evidence"): number {
  return status.bindings.filter((binding) => {
    const coverage = binding.awareness?.coverage?.[key];
    return ["current", "present", "ready"].includes(coverage?.status || "");
  }).length;
}

function policyMissing(status: HadesBackendStatus): string[] {
  return qualityMissing(status).filter((item) => {
    const lower = item.toLowerCase();
    return lower.includes("source") || lower.includes("evidence") || lower.includes("policy");
  });
}

function isPolicyJob(job: HadesBackendJob): boolean {
  const text = [job.capability, job.status, ...job.payload_keys].join(" ").toLowerCase();
  return (
    text.includes("source") ||
    text.includes("evidence") ||
    text.includes("artifact") ||
    text.includes("index") ||
    text.includes("read_files") ||
    text.includes("populate_backend_ast")
  );
}

function reportSummaryValue(summary: Record<string, number> | undefined, key: string): number {
  const value = summary?.[key];
  return typeof value === "number" ? value : 0;
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
  const awareness = binding.awareness;
  return (
    <div className="grid gap-2 border-t border-border py-3 first:border-t-0 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {binding.display_path || "Linked workspace"}
        </span>
        <Badge tone={statusTone(binding.status)}>{binding.status || "unknown"}</Badge>
        {awareness && <Badge tone={statusTone(awareness.status)}>awareness {awareness.status}</Badge>}
      </div>
      <div className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
        <span className="truncate font-mono">project {binding.project_id || "unknown"}</span>
        <span className="truncate font-mono">binding {binding.workspace_binding_id || "local only"}</span>
      </div>
      {binding.head_commit && (
        <div className="truncate font-mono text-xs text-muted-foreground">head {binding.head_commit}</div>
      )}
      {awareness && <AwarenessCoverage awareness={awareness} />}
    </div>
  );
}

function AwarenessCoverage({ awareness }: { awareness: HadesBackendBindingAwareness }) {
  const coverage = awareness.coverage ?? {};
  const items: Array<[string, HadesBackendCoverageItem | undefined]> = [
    ["Memory", coverage.memory_cache],
    ["Artifacts", coverage.project_artifacts],
    ["Source slices", coverage.source_slices],
    ["Bug evidence", coverage.bug_evidence],
  ];
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {items.map(([label, item]) => (
        <div className="border border-border bg-background/40 px-2.5 py-2" key={label}>
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-xs text-muted-foreground">{label}</span>
            <Badge tone={statusTone(item?.status)}>{item?.status || "unknown"}</Badge>
          </div>
          <div className="mt-1 font-mono text-sm font-semibold">{coverageValue(item)}</div>
        </div>
      ))}
    </div>
  );
}

function AwarenessPanel({ status }: { status: HadesBackendStatus }) {
  const awareness = status.awareness;
  if (!awareness) return null;
  const missing = qualityMissing(status);
  return (
    <Card>
      <CardContent className="grid gap-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
            <Database className="h-4 w-4" />
            Project awareness
          </H2>
          <Badge tone={statusTone(awareness.status)}>{awareness.status}</Badge>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Source-free ready</div>
            <div className="mt-1 text-lg font-semibold">{awarenessReadyCount(status)}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Ready bindings</div>
            <div className="mt-1 text-lg font-semibold">{awareness.ready_bindings}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Partial bindings</div>
            <div className="mt-1 text-lg font-semibold">{awareness.partial_bindings}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Degraded bindings</div>
            <div className="mt-1 text-lg font-semibold">{awareness.degraded_bindings}</div>
          </div>
        </div>
        {missing.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {missing.map((item) => (
              <Badge tone="warning" key={item}>{titleLabel(item)}</Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DiagnosisQualityPanel({ status }: { status: HadesBackendStatus }) {
  const total = status.awareness?.bindings ?? status.bindings.length;
  const ready = sourceFreeReadyCount(status);
  const blocked = Math.max(0, total - ready);
  const missing = qualityMissing(status);
  const confidenceCounts = qualityConfidenceCounts(status);
  const latest = latestQualityUpdate(status) ?? status.sync.last_summary_updated_at;
  const qualitySignals = recordTotal(confidenceCounts);
  const panelStatus = !status.configured
    ? "not configured"
    : total === 0
      ? "unmapped"
      : blocked > 0
        ? "blocked"
        : "ready";
  const nextGate = !status.configured
    ? "Run hades backend bootstrap"
    : total === 0
      ? "Link a workspace with hades project link"
      : missing.length > 0
        ? `Repair ${titleLabel(missing[0])}`
        : blocked > 0
          ? "Refresh project awareness"
          : "Ready for source-free diagnosis";

  return (
    <Card>
      <CardContent className="grid gap-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
            <Brain className="h-4 w-4" />
            Diagnosis quality
          </H2>
          <Badge tone={statusTone(panelStatus)}>{panelStatus}</Badge>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Source-free ready</div>
            <div className="mt-1 text-lg font-semibold">{ready}/{total}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Blocked bindings</div>
            <div className="mt-1 text-lg font-semibold">{blocked}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Confidence signals</div>
            <div className="mt-1 text-lg font-semibold">{qualitySignals}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Latest quality signal</div>
            <div className="mt-1 text-lg font-semibold">{formatAgo(latest)}</div>
          </div>
        </div>

        <div className="grid gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Next gate</div>
            <div className="mt-1 text-sm font-medium">{nextGate}</div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="border border-border bg-background/40 px-3 py-2">
              <div className="mb-2 text-xs uppercase text-muted-foreground">Confidence</div>
              <CountList counts={confidenceCounts} />
            </div>
            <div className="border border-border bg-background/40 px-3 py-2">
              <div className="mb-2 text-xs uppercase text-muted-foreground">Missing evidence</div>
              {missing.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {missing.map((item) => (
                    <Badge tone="warning" key={item}>{titleLabel(item)}</Badge>
                  ))}
                </div>
              ) : (
                <Badge tone="success">no blockers</Badge>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function GovernanceQualityPanel({ status }: { status: HadesBackendStatus }) {
  const quality = status.quality;
  const report = quality?.last_report ?? null;
  const reportStatus = report?.status || "not recorded";
  const summary = report?.summary;
  const actions = Array.isArray(report?.action_queue) ? report.action_queue.slice(0, 4) : [];
  const history = quality?.history;
  const historyEntries = Array.isArray(history?.entries) ? history.entries.slice(0, 4) : [];
  const latestFailure = history?.latest_failure ?? null;
  const blockers = reportSummaryValue(summary, "blockers");
  const warnings = reportSummaryValue(summary, "warnings");
  const actionCount = reportSummaryValue(summary, "actions");

  return (
    <Card>
      <CardContent className="grid gap-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
            <CheckCircle2 className="h-4 w-4" />
            Governance quality
          </H2>
          <Badge tone={report ? statusTone(reportStatus) : "outline"}>{reportStatus}</Badge>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Blockers</div>
            <div className="mt-1 text-lg font-semibold">{blockers}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Warnings</div>
            <div className="mt-1 text-lg font-semibold">{warnings}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Actions</div>
            <div className="mt-1 text-lg font-semibold">{actionCount}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Last report</div>
            <div className="mt-1 text-lg font-semibold">{formatAgo(quality?.last_report_updated_at)}</div>
          </div>
        </div>

        {report ? (
          actions.length > 0 ? (
            <div className="grid gap-2">
              {actions.map((action, index) => (
                <div className="border border-border bg-background/40 px-3 py-2" key={action.id || index}>
                  <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    <span className="min-w-0 truncate">{action.id || "quality_action"}</span>
                    <Badge tone={statusTone(action.severity)}>{action.severity || "unknown"}</Badge>
                  </div>
                  {action.message && (
                    <div className="mt-1 text-sm text-muted-foreground">{action.message}</div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="border border-border bg-background/40 px-3 py-2 text-sm">
              No governance actions
            </div>
          )
        ) : (
          <div className="border border-border bg-background/40 px-3 py-2 text-sm">
            Run `hades backend quality-report --record`
          </div>
        )}

        {historyEntries.length > 0 && (
          <div className="grid gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div className="border border-border bg-background/40 px-3 py-2">
              <div className="mb-2 text-xs uppercase text-muted-foreground">Recent reports</div>
              <CountList counts={history?.by_status ?? {}} />
            </div>
            <div className="grid gap-2">
              {historyEntries.map((entry, index) => (
                <div className="border border-border bg-background/40 px-3 py-2" key={`${entry.recorded_at ?? index}-${entry.status ?? "unknown"}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <Badge tone={statusTone(entry.status)}>{entry.status || "unknown"}</Badge>
                    <span className="text-xs text-muted-foreground">{formatAgo(entry.recorded_at)}</span>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                    <span>Blockers {reportSummaryValue(entry.summary, "blockers")}</span>
                    <span>Warnings {reportSummaryValue(entry.summary, "warnings")}</span>
                    <span>Actions {reportSummaryValue(entry.summary, "actions")}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {latestFailure && (
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs uppercase text-muted-foreground">Latest failure</div>
              <Badge tone={statusTone(latestFailure.status)}>{latestFailure.status || "unknown"}</Badge>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {(latestFailure.action_ids || []).slice(0, 5).map((id) => (
                <Badge tone="warning" key={id}>{id}</Badge>
              ))}
              {(latestFailure.action_ids || []).length === 0 && (
                <span className="text-sm text-muted-foreground">No action ids</span>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

type ReviewActionRunner = (
  key: string,
  action: () => Promise<HadesBackendActionResponse>,
  success: string,
) => Promise<void>;

function PolicyControlsPanel({
  status,
  jobs,
  busyAction,
  runReviewAction,
}: {
  status: HadesBackendStatus;
  jobs: HadesBackendJob[];
  busyAction: string | null;
  runReviewAction: ReviewActionRunner;
}) {
  const total = status.awareness?.bindings ?? status.bindings.length;
  const sourceReady = coverageReady(status, "source_slices");
  const evidenceReady = coverageReady(status, "bug_evidence");
  const missing = policyMissing(status);
  const policyJobs = jobs.filter(isPolicyJob);
  const waiting = policyJobs.filter((job) => job.status === "waiting_confirmation");
  const panelStatus = waiting.length > 0 || missing.length > 0 ? "attention" : "ready";

  return (
    <Card>
      <CardContent className="grid gap-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
            <ShieldCheck className="h-4 w-4" />
            Policy controls
          </H2>
          <Badge tone={statusTone(panelStatus)}>{panelStatus}</Badge>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Source slices</div>
            <div className="mt-1 text-lg font-semibold">{sourceReady}/{total}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Bug evidence</div>
            <div className="mt-1 text-lg font-semibold">{evidenceReady}/{total}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Policy reviews</div>
            <div className="mt-1 text-lg font-semibold">{waiting.length}</div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Policy blockers</div>
            <div className="mt-1 text-lg font-semibold">{missing.length}</div>
          </div>
        </div>

        {missing.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {missing.map((item) => (
              <Badge tone="warning" key={item}>{titleLabel(item)}</Badge>
            ))}
          </div>
        )}

        {waiting.length > 0 ? (
          <div className="grid gap-2">
            {waiting.slice(0, 4).map((job) => (
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
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">
            No source or evidence policy reviews
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function IdentityRecoveryPanel({ status }: { status: HadesBackendStatus }) {
  const identity = status.identity;
  if (!identity) return null;
  const recovery = identity.login_recovery;
  const workspace = identity.workspace_binding;
  const projectMemory = identity.project_memory;
  const ready = Boolean(recovery?.source_free_diagnosis_ready);
  return (
    <Card>
      <CardContent className="grid gap-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
            <Link2 className="h-4 w-4" />
            Identity recovery
          </H2>
          <Badge tone={ready ? "success" : "warning"}>
            {ready ? "ready on this device" : "setup needed"}
          </Badge>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Project memory</div>
            <div className="mt-1 text-sm font-semibold">
              {projectMemory.portable_between_devices ? "portable" : "local only"}
            </div>
            <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
              {projectMemory.project_id || "not configured"}
            </div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Current workspace</div>
            <div className="mt-1 text-sm font-semibold">
              {recovery?.current_workspace_mapped ? "mapped" : "unmapped"}
            </div>
            <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
              {workspace.current_workspace_binding_id || "no binding"}
            </div>
          </div>
          <div className="border border-border bg-background/40 px-3 py-2">
            <div className="text-xs uppercase text-muted-foreground">Source-free diagnosis</div>
            <div className="mt-1 text-sm font-semibold">
              {workspace.current_source_free_ready ? "ready" : "not ready"}
            </div>
            <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
              {workspace.current_status || "unknown"}
            </div>
          </div>
        </div>
        {recovery?.recommended_next_action && (
          <div className="border border-border bg-background/40 px-3 py-2 text-sm">
            {recovery.recommended_next_action}
          </div>
        )}
      </CardContent>
    </Card>
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

interface BugIntakeFormState {
  workspaceBindingId: string;
  title: string;
  symptom: string;
  steps: string;
  expected: string;
  actual: string;
  severity: string;
  environment: string;
  failingTest: string;
  runtimeLog: string;
  deployCommit: string;
  workspaceHead: string;
  requestUrl: string;
  requestMethod: string;
  responseStatus: string;
}

function optionalText(value: string): string | undefined {
  const clean = value.trim();
  return clean || undefined;
}

function optionalNumber(value: string): number | undefined {
  const clean = value.trim();
  if (!clean) return undefined;
  const parsed = Number(clean);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function defaultBugIntakeBindingId(status: HadesBackendStatus): string {
  const current = status.identity?.workspace_binding.current_workspace_binding_id;
  if (current) return current;
  return status.bindings.find((binding) => binding.workspace_binding_id)?.workspace_binding_id ?? "";
}

function initialBugIntakeForm(status: HadesBackendStatus): BugIntakeFormState {
  const selected = status.bindings.find(
    (binding) => binding.workspace_binding_id === defaultBugIntakeBindingId(status),
  );
  return {
    workspaceBindingId: selected?.workspace_binding_id ?? "",
    title: "",
    symptom: "",
    steps: "",
    expected: "",
    actual: "",
    severity: "medium",
    environment: "",
    failingTest: "",
    runtimeLog: "",
    deployCommit: "",
    workspaceHead: selected?.head_commit ?? "",
    requestUrl: "",
    requestMethod: "GET",
    responseStatus: "",
  };
}

function bugIntakeBindingLabel(binding: HadesBackendBinding): string {
  const label = binding.display_path || binding.workspace_binding_id || "Workspace";
  const head = binding.head_commit ? ` (${binding.head_commit.slice(0, 12)})` : "";
  return `${label}${head}`;
}

function FormField({
  label,
  children,
  className = "",
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`grid gap-1.5 text-xs font-medium text-muted-foreground ${className}`}>
      {label}
      {children}
    </label>
  );
}

const inputClassName =
  "w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60";

const textareaClassName =
  "min-h-[86px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60";

function BugIntakePanel({
  status,
  onCreated,
  showToast,
}: {
  status: HadesBackendStatus;
  onCreated: () => Promise<void>;
  showToast: (message: string, tone?: "success" | "error") => void;
}) {
  const [form, setForm] = useState<BugIntakeFormState>(() => initialBugIntakeForm(status));
  const [submitting, setSubmitting] = useState(false);
  const bindingOptions = status.bindings.filter((binding) => Boolean(binding.workspace_binding_id));
  const selectedBinding = bindingOptions.find((binding) => binding.workspace_binding_id === form.workspaceBindingId);
  const canSubmit = Boolean(form.title.trim() && form.symptom.trim() && form.workspaceBindingId && !submitting);

  const setField = useCallback(
    (field: keyof BugIntakeFormState, value: string) => {
      setForm((current) => ({
        ...current,
        [field]: value,
      }));
    },
    [],
  );

  const handleBindingChange = useCallback(
    (value: string) => {
      const binding = bindingOptions.find((item) => item.workspace_binding_id === value);
      setForm((current) => ({
        ...current,
        workspaceBindingId: value,
        workspaceHead: current.workspaceHead || binding?.head_commit || "",
      }));
    },
    [bindingOptions],
  );

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!canSubmit) return;
      const payload: HadesBackendBugIntakeRequest = {
        workspace_binding_id: form.workspaceBindingId,
        title: form.title.trim(),
        symptom: form.symptom.trim(),
        steps: optionalText(form.steps),
        expected: optionalText(form.expected),
        actual: optionalText(form.actual),
        severity: optionalText(form.severity),
        environment: optionalText(form.environment),
        failing_test: optionalText(form.failingTest),
        runtime_log: optionalText(form.runtimeLog),
        deploy_commit: optionalText(form.deployCommit),
        workspace_head: optionalText(form.workspaceHead),
        request_url: optionalText(form.requestUrl),
        request_method: optionalText(form.requestMethod),
        response_status: optionalNumber(form.responseStatus),
      };
      setSubmitting(true);
      try {
        const result = await api.createHadesBackendBugIntake(payload);
        const evidenceCount = result.evidence_ids?.filter(Boolean).length ?? 0;
        showToast(
          `Bug report ${result.bug_report_id || "created"} saved with ${evidenceCount} evidence item(s)`,
          "success",
        );
        setForm((current) => ({
          ...initialBugIntakeForm(status),
          workspaceBindingId: current.workspaceBindingId,
          workspaceHead: current.workspaceHead,
        }));
        await onCreated();
      } catch (error) {
        showToast(`Bug intake failed: ${error}`, "error");
      } finally {
        setSubmitting(false);
      }
    },
    [canSubmit, form, onCreated, showToast, status],
  );

  return (
    <Card>
      <CardContent className="grid gap-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
            <Bug className="h-4 w-4" />
            Bug intake
          </H2>
          <Badge tone={bindingOptions.length ? "success" : "warning"}>
            {bindingOptions.length ? "workspace selected" : "no workspace"}
          </Badge>
        </div>

        <form className="grid gap-4" onSubmit={handleSubmit}>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_12rem_12rem]">
            <FormField label="Workspace">
              <select
                className={inputClassName}
                value={form.workspaceBindingId}
                disabled={bindingOptions.length === 0 || submitting}
                onChange={(event) => handleBindingChange(event.target.value)}
              >
                {bindingOptions.length === 0 ? (
                  <option value="">No linked workspace</option>
                ) : (
                  bindingOptions.map((binding) => (
                    <option value={binding.workspace_binding_id || ""} key={binding.workspace_binding_id}>
                      {bugIntakeBindingLabel(binding)}
                    </option>
                  ))
                )}
              </select>
            </FormField>
            <FormField label="Severity">
              <select
                className={inputClassName}
                value={form.severity}
                disabled={submitting}
                onChange={(event) => setField("severity", event.target.value)}
              >
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
                <option value="critical">critical</option>
              </select>
            </FormField>
            <FormField label="Environment">
              <input
                className={inputClassName}
                value={form.environment}
                disabled={submitting}
                placeholder="production"
                onChange={(event) => setField("environment", event.target.value)}
              />
            </FormField>
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <FormField label="Title">
              <input
                className={inputClassName}
                value={form.title}
                disabled={submitting}
                placeholder="Short bug title"
                onChange={(event) => setField("title", event.target.value)}
              />
            </FormField>
            <FormField label="Symptom">
              <textarea
                className={textareaClassName}
                value={form.symptom}
                disabled={submitting}
                placeholder="Observed behavior"
                onChange={(event) => setField("symptom", event.target.value)}
              />
            </FormField>
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            <FormField label="Steps">
              <textarea
                className={textareaClassName}
                value={form.steps}
                disabled={submitting}
                placeholder="Reproduction path"
                onChange={(event) => setField("steps", event.target.value)}
              />
            </FormField>
            <FormField label="Expected">
              <textarea
                className={textareaClassName}
                value={form.expected}
                disabled={submitting}
                placeholder="Expected result"
                onChange={(event) => setField("expected", event.target.value)}
              />
            </FormField>
            <FormField label="Actual">
              <textarea
                className={textareaClassName}
                value={form.actual}
                disabled={submitting}
                placeholder="Actual result"
                onChange={(event) => setField("actual", event.target.value)}
              />
            </FormField>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <FormField label="Failing test output">
              <textarea
                className={textareaClassName}
                value={form.failingTest}
                disabled={submitting}
                placeholder="Paste failing test output"
                onChange={(event) => setField("failingTest", event.target.value)}
              />
            </FormField>
            <FormField label="Runtime log">
              <textarea
                className={textareaClassName}
                value={form.runtimeLog}
                disabled={submitting}
                placeholder="Paste relevant log excerpt"
                onChange={(event) => setField("runtimeLog", event.target.value)}
              />
            </FormField>
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_9rem]">
            <FormField label="Deploy commit">
              <input
                className={inputClassName}
                value={form.deployCommit}
                disabled={submitting}
                placeholder="deployed SHA"
                onChange={(event) => setField("deployCommit", event.target.value)}
              />
            </FormField>
            <FormField label="Workspace head">
              <input
                className={inputClassName}
                value={form.workspaceHead}
                disabled={submitting}
                placeholder={selectedBinding?.head_commit || "indexed SHA"}
                onChange={(event) => setField("workspaceHead", event.target.value)}
              />
            </FormField>
            <FormField label="Request URL">
              <input
                className={inputClassName}
                value={form.requestUrl}
                disabled={submitting}
                placeholder="https://..."
                onChange={(event) => setField("requestUrl", event.target.value)}
              />
            </FormField>
            <FormField label="Status">
              <input
                className={inputClassName}
                value={form.responseStatus}
                disabled={submitting}
                inputMode="numeric"
                placeholder="500"
                onChange={(event) => setField("responseStatus", event.target.value)}
              />
            </FormField>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <FormField label="Method" className="w-32">
              <select
                className={inputClassName}
                value={form.requestMethod}
                disabled={submitting}
                onChange={(event) => setField("requestMethod", event.target.value)}
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
                <option value="PATCH">PATCH</option>
                <option value="DELETE">DELETE</option>
              </select>
            </FormField>
            <Button
              className="uppercase"
              type="submit"
              disabled={!canSubmit}
              prefix={submitting ? <Spinner /> : <Send className="h-4 w-4" />}
            >
              Save bug report
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
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
    if (status.identity?.login_recovery?.recommended_next_action) {
      return status.identity.login_recovery.recommended_next_action;
    }
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

      <AwarenessPanel status={status} />
      <DiagnosisQualityPanel status={status} />
      <PolicyControlsPanel
        status={status}
        jobs={jobs}
        busyAction={busyAction}
        runReviewAction={runReviewAction}
      />
      <BugIntakePanel status={status} onCreated={load} showToast={showToast} />
      <GovernanceQualityPanel status={status} />
      <IdentityRecoveryPanel status={status} />

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
