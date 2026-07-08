<?php

namespace App\Services\Hades;

use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;

class HadesProjectAwareness
{
    private const CURRENT_GRAPH_SCHEMAS = [
        'hades.code_graph.v1',
        'hades.php_graph.v1',
    ];

    private const PARTIAL_GRAPH_SCHEMAS = [
        'hades.git_tree.v1',
        'hades.symbols.v1',
    ];

    private const HEAD_COMMIT_KEYS = [
        'head_commit',
        'indexed_head_commit',
        'workspace_head_commit',
        'git_head',
        'commit',
    ];

    public function statusPayload(object $binding): array
    {
        $memory = $this->memoryCoverage((string) $binding->project_id);
        $artifacts = $this->artifactCoverage((string) $binding->project_id, (string) $binding->id);
        $bugEvidence = $this->bugEvidenceCoverage((string) $binding->project_id, (string) $binding->id);
        $sourceSlices = $this->sourceSliceCoverage((string) $binding->project_id, (string) $binding->id, $this->blankToNull($binding->head_commit ?? null));
        $sourceSliceCandidates = $this->sourceSliceCandidateCoverage((string) $binding->project_id, (string) $binding->id);
        $evidencePacks = $this->evidencePackCoverage((string) $binding->project_id, (string) $binding->id);
        $freshness = $this->freshness($binding, $artifacts, $bugEvidence);

        $artifacts['status'] = $this->artifactStatusFromFreshness($freshness['status']);
        $coverage = [
            'memory' => $memory,
            'artifacts' => $artifacts,
            'bug_evidence' => $bugEvidence,
            'source_slices' => $sourceSlices,
            'source_slice_candidates' => $sourceSliceCandidates,
            'evidence_packs' => $evidencePacks,
            'code_graph' => $this->codeGraphCoverage($artifacts),
        ];
        $actions = $this->actions($freshness, $coverage);
        $diagnosable = $freshness['status'] === 'current'
            && $coverage['bug_evidence']['status'] === 'current'
            && $coverage['source_slices']['status'] === 'current'
            && $coverage['code_graph']['status'] === 'current';

        return [
            'protocol_version' => 'v1',
            'project_id' => $binding->project_id,
            'workspace_binding_id' => $binding->id,
            'workspace_head_commit' => $this->blankToNull($binding->head_commit ?? null),
            'freshness' => $freshness,
            'coverage' => $coverage,
            'diagnosable_without_source' => $diagnosable,
            'overall_status' => $this->overallStatus($freshness, $coverage, $diagnosable),
            'actions' => $actions,
            'server_time' => now()->toISOString(),
        ];
    }

    public function freshnessForBinding(object $binding): array
    {
        $artifacts = $this->artifactCoverage((string) $binding->project_id, (string) $binding->id);
        $bugEvidence = $this->bugEvidenceCoverage((string) $binding->project_id, (string) $binding->id);

        return $this->freshness($binding, $artifacts, $bugEvidence);
    }

    private function memoryCoverage(string $projectId): array
    {
        $row = DB::table('project_memory_entries')
            ->where('project_id', $projectId)
            ->selectRaw('COUNT(*) as aggregate_count, MAX(updated_at) as updated_at')
            ->first();
        $count = (int) ($row->aggregate_count ?? 0);

        return [
            'status' => $count > 0 ? 'current' : 'missing',
            'count' => $count,
            'updated_at' => $this->toIsoString($row->updated_at ?? null),
        ];
    }

    private function bugEvidenceCoverage(string $projectId, string $bindingId): array
    {
        $row = DB::table('hades_bug_evidence_items')
            ->where('project_id', $projectId)
            ->where('workspace_binding_id', $bindingId)
            ->selectRaw('COUNT(*) as aggregate_count, MAX(updated_at) as updated_at')
            ->first();
        $count = (int) ($row->aggregate_count ?? 0);

        return [
            'status' => $count > 0 ? 'current' : 'missing',
            'count' => $count,
            'updated_at' => $this->toIsoString($row->updated_at ?? null),
        ];
    }

    private function evidencePackCoverage(string $projectId, string $bindingId): array
    {
        if (! DB::getSchemaBuilder()->hasTable('hades_evidence_packs')) {
            return [
                'status' => 'missing',
                'count' => 0,
                'reason' => 'evidence_pack_store_missing',
            ];
        }

        $row = DB::table('hades_evidence_packs')
            ->where('project_id', $projectId)
            ->where('workspace_binding_id', $bindingId)
            ->selectRaw('COUNT(*) as aggregate_count, MAX(updated_at) as updated_at')
            ->first();
        $count = (int) ($row->aggregate_count ?? 0);

        return [
            'status' => $count > 0 ? 'current' : 'missing',
            'count' => $count,
            'updated_at' => $this->toIsoString($row->updated_at ?? null),
        ];
    }

    private function artifactCoverage(string $projectId, string $bindingId): array
    {
        $rows = DB::table('hades_agent_artifacts')
            ->where('project_id', $projectId)
            ->where('workspace_binding_id', $bindingId)
            ->orderByDesc('created_at')
            ->orderByDesc('id')
            ->get();

        $schemas = [];
        $latestSchema = null;
        $latestAt = null;
        $artifactHead = null;
        $artifactHeadSource = null;
        $truncatedCount = 0;
        $redactions = 0;

        foreach ($rows as $row) {
            $schema = (string) $row->schema;
            $schemas[$schema] = ($schemas[$schema] ?? 0) + 1;
            $latestSchema ??= $schema;
            $latestAt ??= $this->toIsoString($row->created_at ?? null);
            $truncatedCount += (bool) ($row->truncated ?? false) ? 1 : 0;
            $redactions += (int) ($row->redactions ?? 0);

            if ($artifactHead === null) {
                $payload = $this->decode($row->artifact ?? null);
                $foundHead = $this->extractHeadCommit($payload);
                if ($foundHead !== null) {
                    $artifactHead = $foundHead;
                    $artifactHeadSource = $schema;
                }
            }
        }

        ksort($schemas);

        return [
            'status' => count($rows) > 0 ? 'unknown' : 'missing',
            'count' => count($rows),
            'schemas' => $schemas,
            'latest_schema' => $latestSchema,
            'updated_at' => $latestAt,
            'artifact_head_commit' => $artifactHead,
            'artifact_head_source' => $artifactHeadSource,
            'truncated_count' => $truncatedCount,
            'redactions' => $redactions,
        ];
    }

    private function freshness(object $binding, array $artifacts, array $bugEvidence): array
    {
        $workspaceHead = $this->blankToNull($binding->head_commit ?? null);
        $artifactHead = $this->blankToNull($artifacts['artifact_head_commit'] ?? null);
        $status = 'current';
        $staleReason = null;

        if ((int) ($artifacts['count'] ?? 0) === 0) {
            $status = 'missing';
            $staleReason = 'artifacts_missing';
        } elseif ($workspaceHead === null) {
            $status = 'unknown';
            $staleReason = 'workspace_head_unknown';
        } elseif ($artifactHead === null) {
            $status = 'unknown';
            $staleReason = 'artifact_head_unknown';
        } elseif (! hash_equals($workspaceHead, $artifactHead)) {
            $status = 'stale';
            $staleReason = 'artifact_head_mismatch';
        }

        return [
            'status' => $status,
            'workspace_head_commit' => $workspaceHead,
            'artifact_head_commit' => $artifactHead,
            'artifact_head_source' => $artifacts['artifact_head_source'] ?? null,
            'index_status' => 'live_query',
            'stale_reason' => $staleReason,
            'last_artifact_at' => $artifacts['updated_at'] ?? null,
            'last_evidence_at' => $bugEvidence['updated_at'] ?? null,
        ];
    }

    private function sourceSliceCoverage(string $projectId, string $bindingId, ?string $workspaceHead): array
    {
        $rows = DB::table('hades_source_slices')
            ->where('project_id', $projectId)
            ->where('workspace_binding_id', $bindingId)
            ->orderByDesc('updated_at')
            ->orderByDesc('id')
            ->get();
        $count = count($rows);

        if ($count === 0) {
            return [
                'status' => 'missing',
                'count' => 0,
                'reason' => 'source_slices_missing',
            ];
        }

        $latest = $rows->first();
        $sliceHead = $this->blankToNull($latest->head_commit ?? null);
        $status = 'current';
        $reason = null;

        if ($workspaceHead === null) {
            $status = 'unknown';
            $reason = 'workspace_head_unknown';
        } elseif ($sliceHead === null) {
            $status = 'unknown';
            $reason = 'source_slice_head_unknown';
        } elseif (! hash_equals($workspaceHead, $sliceHead)) {
            $status = 'stale';
            $reason = 'source_slice_head_mismatch';
        }

        return [
            'status' => $status,
            'count' => $count,
            'updated_at' => $this->toIsoString($latest->updated_at ?? null),
            'source_slice_head_commit' => $sliceHead,
            'stale_reason' => $reason,
            'truncated_count' => $rows->filter(fn (object $row): bool => (bool) ($row->truncated ?? false))->count(),
            'redactions' => $rows->sum(fn (object $row): int => (int) ($row->redactions ?? 0)),
        ];
    }

    private function sourceSliceCandidateCoverage(string $projectId, string $bindingId): array
    {
        if (! DB::getSchemaBuilder()->hasTable('hades_source_slice_candidates')) {
            return [
                'status' => 'missing',
                'count' => 0,
                'waiting_jobs' => 0,
                'reason' => 'source_slice_candidate_store_missing',
            ];
        }

        $rows = DB::table('hades_source_slice_candidates')
            ->where('project_id', $projectId)
            ->where('workspace_binding_id', $bindingId)
            ->selectRaw('COUNT(*) as aggregate_count, MAX(updated_at) as updated_at')
            ->selectRaw("SUM(CASE WHEN status IN ('job_created', 'queued') THEN 1 ELSE 0 END) as waiting_jobs")
            ->first();

        $count = (int) ($rows->aggregate_count ?? 0);
        $waiting = (int) ($rows->waiting_jobs ?? 0);
        $status = 'none';
        if ($waiting > 0) {
            $status = 'pending';
        } elseif ($count > 0) {
            $status = 'present';
        }

        return [
            'status' => $status,
            'count' => $count,
            'waiting_jobs' => $waiting,
            'updated_at' => $this->toIsoString($rows->updated_at ?? null),
        ];
    }

    private function codeGraphCoverage(array $artifacts): array
    {
        $schemas = is_array($artifacts['schemas'] ?? null) ? $artifacts['schemas'] : [];
        foreach (self::CURRENT_GRAPH_SCHEMAS as $schema) {
            if ((int) ($schemas[$schema] ?? 0) > 0) {
                return [
                    'status' => $artifacts['status'] === 'current' ? 'current' : $artifacts['status'],
                    'count' => (int) $schemas[$schema],
                    'schema' => $schema,
                    'coverage_type' => 'code_graph',
                    'updated_at' => $artifacts['updated_at'] ?? null,
                ];
            }
        }

        foreach (self::PARTIAL_GRAPH_SCHEMAS as $schema) {
            if ((int) ($schemas[$schema] ?? 0) > 0) {
                $status = match ($artifacts['status']) {
                    'stale' => 'stale',
                    'unknown' => 'unknown',
                    default => 'partial',
                };

                return [
                    'status' => $status,
                    'count' => (int) $schemas[$schema],
                    'schema' => $schema,
                    'coverage_type' => 'metadata_or_symbol_index',
                    'updated_at' => $artifacts['updated_at'] ?? null,
                ];
            }
        }

        return [
            'status' => 'missing',
            'count' => 0,
            'reason' => 'code_graph_not_indexed',
        ];
    }

    private function artifactStatusFromFreshness(string $freshnessStatus): string
    {
        return match ($freshnessStatus) {
            'current', 'stale', 'missing', 'unknown' => $freshnessStatus,
            default => 'unknown',
        };
    }

    private function overallStatus(array $freshness, array $coverage, bool $diagnosable): string
    {
        if ($diagnosable) {
            return 'ready';
        }

        if ($freshness['status'] === 'stale') {
            return 'stale';
        }

        if ($freshness['status'] === 'missing' || $coverage['artifacts']['status'] === 'missing') {
            return 'missing_index';
        }

        return 'partial';
    }

    private function actions(array $freshness, array $coverage): array
    {
        $actions = [];
        if ($coverage['artifacts']['status'] === 'missing') {
            $actions[] = 'Run `hades backend sync` from the linked checkout to upload project artifacts.';
        } elseif (in_array($freshness['status'], ['stale', 'unknown'], true)) {
            $actions[] = 'Run `hades backend sync` from the current checkout so indexed artifacts match workspace HEAD.';
        }

        if ($coverage['memory']['status'] === 'missing') {
            $actions[] = 'Import or accept shared project memory before relying on historical project facts.';
        }

        if ($coverage['bug_evidence']['status'] === 'missing') {
            $actions[] = 'Capture stack traces, failing tests, logs, or reproduction steps as typed bug evidence before precise root-cause claims.';
        }

        if ($coverage['code_graph']['status'] !== 'current') {
            $actions[] = 'Index a current code graph before claiming exact call paths or owner methods without source access.';
        }

        if (($coverage['source_slice_candidates']['status'] ?? null) === 'pending') {
            $actions[] = 'Approve pending source-slice jobs before precise source-free diagnosis.';
        }

        if ($coverage['source_slices']['status'] !== 'current') {
            $actions[] = 'Index policy-compliant source slices before claiming exact line-level causes without source access.';
        }

        return array_values(array_unique($actions));
    }

    /**
     * @return array<string, mixed>
     */
    private function decode(mixed $payload): array
    {
        $decoded = is_string($payload) ? json_decode($payload, true) : $payload;

        return is_array($decoded) ? $decoded : [];
    }

    /**
     * @param  array<string, mixed>  $payload
     */
    private function extractHeadCommit(array $payload): ?string
    {
        $queue = [[$payload, 0]];
        while ($queue !== []) {
            [$current, $depth] = array_shift($queue);
            if (! is_array($current) || $depth > 4) {
                continue;
            }

            foreach ($current as $key => $value) {
                if (is_string($key) && in_array($key, self::HEAD_COMMIT_KEYS, true)) {
                    $head = $this->blankToNull($value);
                    if ($head !== null) {
                        return $head;
                    }
                }

                if (is_array($value)) {
                    $queue[] = [$value, $depth + 1];
                }
            }
        }

        return null;
    }

    private function blankToNull(mixed $value): ?string
    {
        $text = trim((string) ($value ?? ''));

        return $text === '' ? null : $text;
    }

    private function toIsoString(mixed $value): ?string
    {
        return $value ? Carbon::parse($value)->toISOString() : null;
    }
}
