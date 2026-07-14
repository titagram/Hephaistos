<?php

namespace App\Services\Graph;

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;
use RuntimeException;

class CanonicalGraphProjectionService
{
    /**
     * Fetch exact projection identities with one bounded query.
     *
     * @param  list<array>  $graphs
     * @return array<string, object>
     */
    public function findForGraphs(array $graphs, bool $lockForUpdate = false): array
    {
        if ($graphs === []) {
            return [];
        }

        $identities = collect($graphs)->map(fn (array $graph): array => $graph['identity']);
        $query = DB::table('canonical_graph_projections')
            ->where(function ($query) use ($identities): void {
                foreach ($identities->groupBy('artifact_type') as $artifactType => $typedIdentities) {
                    $query->orWhere(function ($typedQuery) use ($artifactType, $typedIdentities): void {
                        $typedQuery->where('artifact_type', $artifactType)
                            ->whereIn('artifact_id', $typedIdentities->pluck('artifact_id'));
                    });
                }
            });
        if ($lockForUpdate) {
            $query->lockForUpdate();
        }

        return $query->get()->keyBy(fn (object $projection): string => $this->identityKey(
            (string) $projection->artifact_type,
            (string) $projection->artifact_id,
        ))->all();
    }

    /**
     * Atomically claim missing and failed projections for reconciliation.
     * The returned `claimed` bit is true only for the caller that won the
     * unique insert or the locked failed-to-queued transition.
     *
     * @param  list<array>  $graphs
     * @return array<string, array{projection: object, claimed: bool, conflict: bool}>
     */
    public function claimForReconcile(array $graphs): array
    {
        if ($graphs === []) {
            return [];
        }

        $projectIds = collect($graphs)->pluck('identity.project_id')->unique()->values();
        if ($projectIds->count() !== 1) {
            throw new RuntimeException('A reconciliation claim must target one project.');
        }

        return DB::transaction(function () use ($graphs, $projectIds): array {
            DB::table('projects')->where('id', (string) $projectIds->first())->lockForUpdate()->firstOrFail();
            $existing = $this->findForGraphs($graphs, lockForUpdate: true);
            $now = now();
            $proposals = [];

            foreach ($graphs as $graph) {
                $identity = $graph['identity'];
                $key = $this->identityKey((string) $identity['artifact_type'], (string) $identity['artifact_id']);
                if (isset($existing[$key])) {
                    continue;
                }

                $proposals[$key] = $this->projectionAttributes($graph, (string) Str::ulid(), $now);
            }

            if ($proposals !== []) {
                DB::table('canonical_graph_projections')->insertOrIgnore(array_values($proposals));
            }

            $current = $this->findForGraphs($graphs, lockForUpdate: true);
            $failedIds = [];
            foreach ($graphs as $graph) {
                $identity = $graph['identity'];
                $key = $this->identityKey((string) $identity['artifact_type'], (string) $identity['artifact_id']);
                $projection = $current[$key];
                $insertWinner = isset($proposals[$key]) && $projection->id === $proposals[$key]['id'];
                if (! $insertWinner && $this->matchesGraph($projection, $graph) && $projection->status === 'failed') {
                    $failedIds[] = (string) $projection->id;
                }
            }

            if ($failedIds !== []) {
                DB::table('canonical_graph_projections')
                    ->whereIn('id', $failedIds)
                    ->where('status', 'failed')
                    ->update([
                        'status' => 'queued',
                        'error_code' => null,
                        'updated_at' => $now,
                    ]);
                $current = $this->findForGraphs($graphs, lockForUpdate: true);
            }

            $claims = [];
            foreach ($graphs as $graph) {
                $identity = $graph['identity'];
                $key = $this->identityKey((string) $identity['artifact_type'], (string) $identity['artifact_id']);
                $projection = $current[$key];
                $claims[$key] = [
                    'projection' => $projection,
                    'claimed' => (isset($proposals[$key]) && $projection->id === $proposals[$key]['id'])
                        || in_array((string) $projection->id, $failedIds, true),
                    'conflict' => ! $this->matchesGraph($projection, $graph),
                ];
            }

            return $claims;
        });
    }

    public function queue(array $graph): object
    {
        $identity = $graph['identity'];
        $artifactType = (string) $identity['artifact_type'];
        $artifactId = (string) $identity['artifact_id'];
        $checksum = (string) $identity['checksum'];
        $now = now();

        DB::table('canonical_graph_projections')->insertOrIgnore(
            $this->projectionAttributes($graph, (string) Str::ulid(), $now),
        );

        return DB::table('canonical_graph_projections')
            ->where('artifact_type', $artifactType)
            ->where('artifact_id', $artifactId)
            ->firstOrFail();
    }

    /**
     * Atomically claim one queued projection for a queue worker.
     *
     * A null result means the projection is missing or another worker/job
     * already owns or completed it. Callers must treat that as a no-op.
     */
    public function claimForWorker(string $id): ?object
    {
        $now = now();
        $claimed = DB::table('canonical_graph_projections')
            ->where('id', $id)
            ->where('status', 'queued')
            ->update([
                'status' => 'projecting',
                'error_code' => null,
                'updated_at' => $now,
            ]);

        if ($claimed !== 1) {
            return null;
        }

        return DB::table('canonical_graph_projections')->where('id', $id)->first();
    }

    public function markProjecting(string $id): void
    {
        DB::table('canonical_graph_projections')->where('id', $id)->update([
            'status' => 'projecting',
            'error_code' => null,
            'updated_at' => now(),
        ]);
    }

    public function markReady(string $id, int $nodes, int $relationships): void
    {
        DB::transaction(function () use ($id, $nodes, $relationships): void {
            $projectId = DB::table('canonical_graph_projections')->where('id', $id)->value('project_id');
            if ($projectId === null) {
                throw new RuntimeException('Canonical graph projection not found.');
            }

            DB::table('projects')->where('id', $projectId)->lockForUpdate()->firstOrFail();

            $candidate = DB::table('canonical_graph_projections')->where('id', $id)->lockForUpdate()->first();
            if ($candidate === null) {
                throw new RuntimeException('Canonical graph projection not found.');
            }

            DB::table('canonical_graph_projections')
                ->where('project_id', $candidate->project_id)
                ->where('source_scope_type', $candidate->source_scope_type)
                ->where('source_scope_id', $candidate->source_scope_id)
                ->where('status', 'ready')
                ->where('id', '!=', $id)
                ->update(['status' => 'stale', 'updated_at' => now()]);

            DB::table('canonical_graph_projections')->where('id', $id)->update([
                'status' => 'ready',
                'node_count' => $nodes,
                'relationship_count' => $relationships,
                'error_code' => null,
                'projected_at' => now(),
                'updated_at' => now(),
            ]);
        });
    }

    public function markFailed(string $id, string $code): void
    {
        $boundedCode = $this->boundedFailureCode($code);
        DB::table('canonical_graph_projections')->where('id', $id)->update([
            'status' => 'failed',
            'error_code' => $boundedCode,
            'updated_at' => now(),
        ]);
    }

    /**
     * Release an owned projection for Laravel's next attempt without making
     * it claimable by reconciliation. Reconcile only claims final `failed`
     * records; queued records remain owned by the already-dispatched job.
     */
    public function markRetryPending(string $id, string $code): void
    {
        DB::table('canonical_graph_projections')
            ->where('id', $id)
            ->where('status', 'projecting')
            ->update([
                'status' => 'queued',
                'error_code' => $this->boundedFailureCode($code),
                'updated_at' => now(),
            ]);
    }

    public function readyForScope(string $projectId, string $scopeType, string $scopeId): ?object
    {
        return DB::table('canonical_graph_projections')
            ->where('project_id', $projectId)
            ->where('source_scope_type', $scopeType)
            ->where('source_scope_id', $scopeId)
            ->where('status', 'ready')
            ->orderByDesc('projected_at')
            ->orderByDesc('id')
            ->first();
    }

    private function identityKey(string $artifactType, string $artifactId): string
    {
        return $artifactType."\0".$artifactId;
    }

    public function matchesGraph(object $projection, array $graph): bool
    {
        $identity = $graph['identity'];
        $expectedVersion = hash(
            'sha256',
            $identity['artifact_type'].'|'.$identity['artifact_id'].'|'.$identity['checksum'],
        );

        return (string) $projection->project_id === (string) $identity['project_id']
            && (string) $projection->source_scope_type === (string) $identity['source_scope_type']
            && (string) $projection->source_scope_id === (string) $identity['source_scope_id']
            && hash_equals((string) $projection->checksum, (string) $identity['checksum'])
            && hash_equals((string) $projection->graph_version, $expectedVersion);
    }

    private function projectionAttributes(array $graph, string $id, mixed $now): array
    {
        $identity = $graph['identity'];
        $artifactType = (string) $identity['artifact_type'];
        $artifactId = (string) $identity['artifact_id'];
        $checksum = (string) $identity['checksum'];

        return [
            'id' => $id,
            'project_id' => $identity['project_id'],
            'source_scope_type' => $identity['source_scope_type'],
            'source_scope_id' => $identity['source_scope_id'],
            'artifact_type' => $artifactType,
            'artifact_id' => $artifactId,
            'graph_version' => hash('sha256', $artifactType.'|'.$artifactId.'|'.$checksum),
            'checksum' => $checksum,
            'head_commit' => $graph['contract']['source']['head_commit'] ?? null,
            'quality' => $graph['contract']['extractor']['quality'],
            'status' => 'queued',
            'created_at' => $now,
            'updated_at' => $now,
        ];
    }

    private function boundedFailureCode(string $code): string
    {
        return preg_match('/\A[a-z0-9_]{1,100}\z/', $code) === 1 ? $code : 'projection_failed';
    }
}
