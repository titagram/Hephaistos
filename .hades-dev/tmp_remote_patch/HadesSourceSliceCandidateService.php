<?php

namespace App\Services\Hades;

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

class HadesSourceSliceCandidateService
{
    /**
     * @return array{candidates: int, jobs_created: int}
     */
    public function ingestArtifactCandidates(object $agent, object $binding, array $artifact, ?string $headCommit): array
    {
        $items = $artifact['source_slice_candidates'] ?? [];
        if (! is_array($items)) {
            return ['candidates' => 0, 'jobs_created' => 0];
        }

        $candidateCount = 0;
        $jobsCreated = 0;

        foreach ($items as $item) {
            if (! is_array($item)) {
                continue;
            }

            $candidate = $this->normalizeCandidate($item, $headCommit);
            if ($candidate === null) {
                continue;
            }

            $candidateCount++;
            $existing = DB::table('hades_source_slice_candidates')
                ->where('workspace_binding_id', $binding->id)
                ->where('candidate_key', $candidate['candidate_key'])
                ->first();

            if ($existing && in_array($existing->status, ['job_created', 'slice_uploaded', 'rejected'], true)) {
                continue;
            }

            $now = now();
            DB::table('hades_source_slice_candidates')->updateOrInsert(
                [
                    'workspace_binding_id' => $binding->id,
                    'candidate_key' => $candidate['candidate_key'],
                ],
                [
                    'project_id' => $binding->project_id,
                    'path' => $candidate['path'],
                    'start_line' => $candidate['start_line'],
                    'end_line' => $candidate['end_line'],
                    'symbol' => $candidate['symbol'],
                    'reason' => $candidate['reason'],
                    'priority' => $candidate['priority'],
                    'head_commit' => $candidate['head_commit'],
                    'status' => 'pending',
                    'metadata' => json_encode($candidate, JSON_THROW_ON_ERROR),
                    'created_at' => $existing->created_at ?? $now,
                    'updated_at' => $now,
                ],
            );

            $jobId = (string) Str::ulid();
            DB::table('hades_agent_jobs')->insert([
                'id' => $jobId,
                'project_id' => $binding->project_id,
                'hades_agent_id' => $agent->id,
                'workspace_binding_id' => $binding->id,
                'idempotency_key' => 'source_slice_candidate:'.$binding->id.':'.$candidate['candidate_key'],
                'capability' => 'read_source_slice',
                'status' => 'queued',
                'policy' => 'confirm',
                'priority' => 'normal',
                'payload' => json_encode([
                    'path' => $candidate['path'],
                    'start_line' => $candidate['start_line'],
                    'end_line' => $candidate['end_line'],
                    'symbol' => $candidate['symbol'],
                    'candidate_key' => $candidate['candidate_key'],
                    'reason' => $candidate['reason'],
                    'head_commit' => $candidate['head_commit'],
                ], JSON_THROW_ON_ERROR),
                'result' => null,
                'requires_confirmation' => true,
                'deadline_at' => $now->copy()->addDays(7),
                'available_at' => $now,
                'created_at' => $now,
                'updated_at' => $now,
            ]);

            DB::table('hades_source_slice_candidates')
                ->where('workspace_binding_id', $binding->id)
                ->where('candidate_key', $candidate['candidate_key'])
                ->update([
                    'status' => 'job_created',
                    'job_id' => $jobId,
                    'updated_at' => $now,
                ]);

            $jobsCreated++;
        }

        return ['candidates' => $candidateCount, 'jobs_created' => $jobsCreated];
    }

    /**
     * @return array{candidate_key: string, path: string, start_line: int, end_line: int, symbol: string|null, reason: string, priority: int, head_commit: string|null}|null
     */
    private function normalizeCandidate(array $item, ?string $headCommit): ?array
    {
        $path = trim((string) ($item['path'] ?? ''));
        $candidateKey = trim((string) ($item['candidate_key'] ?? ''));
        if ($path === '' || $candidateKey === '' || str_contains($path, '..') || str_starts_with($path, '/')) {
            return null;
        }

        $startLine = max(1, (int) ($item['start_line'] ?? 1));
        $endLine = max($startLine, (int) ($item['end_line'] ?? $startLine));

        return [
            'candidate_key' => $candidateKey,
            'path' => $path,
            'start_line' => $startLine,
            'end_line' => $endLine,
            'symbol' => trim((string) ($item['symbol'] ?? '')) ?: null,
            'reason' => trim((string) ($item['reason'] ?? 'symbol')) ?: 'symbol',
            'priority' => max(1, min(1000, (int) ($item['priority'] ?? 500))),
            'head_commit' => trim((string) ($item['head_commit'] ?? $headCommit ?? '')) ?: null,
        ];
    }
}
