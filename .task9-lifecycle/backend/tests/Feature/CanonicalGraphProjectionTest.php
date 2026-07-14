<?php

use App\Services\Graph\CanonicalGraphProjectionService;
use Database\Seeders\DevBoardSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;

uses(RefreshDatabase::class);

beforeEach(function () {
    $this->seed(DevBoardSeeder::class);
});

it('keeps the previous projection ready until its replacement is ready', function () {
    $projectId = DB::table('projects')->where('slug', 'demo-project')->value('id');
    $service = app(CanonicalGraphProjectionService::class);
    $first = $service->queue(canonicalProjectionGraph($projectId, 'artifact-1', str_repeat('a', 64)));
    $service->markProjecting($first->id);
    $service->markReady($first->id, 10, 5);
    $second = $service->queue(canonicalProjectionGraph($projectId, 'artifact-2', str_repeat('b', 64)));
    $service->markProjecting($second->id);
    expect($service->readyForScope($projectId, 'workspace_binding', 'binding-1')->id)->toBe($first->id);
    $service->markReady($second->id, 12, 7);
    expect($service->readyForScope($projectId, 'workspace_binding', 'binding-1')->id)->toBe($second->id)
        ->and(DB::table('canonical_graph_projections')->where('id', $first->id)->value('status'))->toBe('stale')
        ->and(DB::table('canonical_graph_projections')
            ->where('project_id', $projectId)
            ->where('source_scope_type', 'workspace_binding')
            ->where('source_scope_id', 'binding-1')
            ->where('status', 'ready')
            ->count())->toBe(1);
});

it('locks the stable project row before locking the projection candidate', function () {
    $projectId = DB::table('projects')->where('slug', 'demo-project')->value('id');
    $service = app(CanonicalGraphProjectionService::class);
    $candidate = $service->queue(canonicalProjectionGraph($projectId, 'artifact-1', str_repeat('a', 64)));
    $queries = [];
    DB::listen(function ($query) use (&$queries): void {
        $queries[] = strtolower($query->sql);
    });

    $service->markReady($candidate->id, 10, 5);

    $projectLock = array_find_key($queries, fn (string $sql): bool => str_contains($sql, 'from "projects"'));
    $candidateLock = array_find_key($queries, fn (string $sql): bool => str_starts_with($sql, 'select *') && str_contains($sql, 'from "canonical_graph_projections"'));
    expect($projectLock)->not->toBeNull()
        ->and($candidateLock)->not->toBeNull()
        ->and($projectLock)->toBeLessThan($candidateLock);
});

it('queues an artifact identity idempotently', function () {
    $projectId = DB::table('projects')->where('slug', 'demo-project')->value('id');
    $service = app(CanonicalGraphProjectionService::class);
    $graph = canonicalProjectionGraph($projectId, 'artifact-1', str_repeat('a', 64));
    $first = $service->queue($graph);
    $second = $service->queue($graph);
    expect($second->id)->toBe($first->id)
        ->and(DB::table('canonical_graph_projections')->count())->toBe(1);
});

it('stores only bounded failure codes and rejects raw exception text', function () {
    $projectId = DB::table('projects')->where('slug', 'demo-project')->value('id');
    $service = app(CanonicalGraphProjectionService::class);
    $raw = $service->queue(canonicalProjectionGraph($projectId, 'artifact-1', str_repeat('a', 64)));
    $bounded = $service->queue(canonicalProjectionGraph($projectId, 'artifact-2', str_repeat('b', 64)));
    $service->markFailed($raw->id, 'SQLSTATE[HY000]: secret connection text');
    $service->markFailed($bounded->id, 'neo4j_timeout');
    expect(DB::table('canonical_graph_projections')->where('id', $raw->id)->value('error_code'))->toBe('projection_failed')
        ->and(DB::table('canonical_graph_projections')->where('id', $bounded->id)->value('error_code'))->toBe('neo4j_timeout');
});

it('claims a queued worker projection exactly once with an atomic state transition', function () {
    $projectId = DB::table('projects')->where('slug', 'demo-project')->value('id');
    $service = app(CanonicalGraphProjectionService::class);
    $projection = $service->queue(canonicalProjectionGraph($projectId, 'artifact-claim', str_repeat('c', 64)));

    $first = $service->claimForWorker($projection->id);
    $second = $service->claimForWorker($projection->id);

    expect($first)->not->toBeNull()
        ->and($first->status)->toBe('projecting')
        ->and($second)->toBeNull()
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('projecting');
});

function canonicalProjectionGraph(string $projectId, string $artifactId, string $checksum): array
{
    return [
        'identity' => [
            'project_id' => $projectId,
            'source_scope_type' => 'workspace_binding',
            'source_scope_id' => 'binding-1',
            'artifact_type' => 'hades_agent_artifact',
            'artifact_id' => $artifactId,
            'checksum' => $checksum,
        ],
        'contract' => [
            'extractor' => ['quality' => 'full'],
            'source' => ['head_commit' => 'abc123'],
        ],
    ];
}
