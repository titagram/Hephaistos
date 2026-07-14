<?php

use App\Jobs\ProjectCanonicalGraphToNeo4j;
use App\Services\Graph\CanonicalGraphProjectionService;
use App\Services\Graph\CanonicalGraphRepository;
use App\Services\Neo4jClientFactory;
use Database\Seeders\DevBoardSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\Bus;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Str;

uses(RefreshDatabase::class);

beforeEach(function () {
    Storage::fake('local');
    $this->seed(DevBoardSeeder::class);
});

it('discovers a Hades-only graph without mutating during dry-run', function () {
    Bus::fake();
    $clients = Mockery::mock(Neo4jClientFactory::class);
    $clients->shouldNotReceive('client');
    app()->instance(Neo4jClientFactory::class, $clients);
    $context = canonicalRebuildHadesContext();

    $exitCode = Artisan::call('devboard:neo4j-rebuild', [
        '--reconcile' => true,
        '--project' => $context['project_id'],
        '--dry-run' => true,
    ]);

    expect($exitCode)->toBe(0)
        ->and(canonicalRebuildSummary())->toBe([
            'scanned' => 1,
            'queued' => 1,
            'ready' => 0,
            'failed' => 0,
            'skipped' => 0,
            'dry_run' => true,
        ])
        ->and(DB::table('canonical_graph_projections')->count())->toBe(0);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
});

it('discovers a legacy repository graph through the canonical repository', function () {
    Bus::fake();
    $context = canonicalRebuildLegacyContext();

    $exitCode = Artisan::call('devboard:neo4j-rebuild', [
        '--reconcile' => true,
        '--project' => $context['project_id'],
        '--dry-run' => true,
    ]);

    expect($exitCode)->toBe(0)
        ->and(canonicalRebuildSummary())->toBe([
            'scanned' => 1,
            'queued' => 1,
            'ready' => 0,
            'failed' => 0,
            'skipped' => 0,
            'dry_run' => true,
        ])
        ->and(DB::table('canonical_graph_projections')->count())->toBe(0);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
});

it('queues and dispatches a missing exact projection', function () {
    Bus::fake();
    $context = canonicalRebuildHadesContext();

    $exitCode = Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);
    $projection = DB::table('canonical_graph_projections')->sole();

    expect($exitCode)->toBe(0)
        ->and(canonicalRebuildSummary())->toBe([
            'scanned' => 1,
            'queued' => 1,
            'ready' => 0,
            'failed' => 0,
            'skipped' => 0,
            'dry_run' => false,
        ])
        ->and($projection->project_id)->toBe($context['project_id'])
        ->and($projection->source_scope_id)->toBe($context['scope_id'])
        ->and($projection->status)->toBe('queued');
    Bus::assertDispatchedTimes(ProjectCanonicalGraphToNeo4j::class, 1);
    Bus::assertDispatched(fn (ProjectCanonicalGraphToNeo4j $job): bool => $job->projectionId === $projection->id);
});

it('filters by the exact project and never queues another project', function () {
    Bus::fake();
    $selected = canonicalRebuildHadesContext();
    $other = canonicalRebuildHadesContext();

    Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $selected['project_id']]);

    expect(canonicalRebuildSummary()['scanned'])->toBe(1)
        ->and(DB::table('canonical_graph_projections')->where('project_id', $selected['project_id'])->count())->toBe(1)
        ->and(DB::table('canonical_graph_projections')->where('project_id', $other['project_id'])->count())->toBe(0);
});

it('filters by one exact optional source scope', function () {
    Bus::fake();
    $selected = canonicalRebuildHadesContext();
    $other = canonicalRebuildHadesContext($selected['project_id']);

    Artisan::call('devboard:neo4j-rebuild', [
        '--reconcile' => true,
        '--project' => $selected['project_id'],
        '--scope-type' => 'workspace_binding',
        '--scope-id' => $selected['scope_id'],
    ]);

    expect(canonicalRebuildSummary()['scanned'])->toBe(1)
        ->and(DB::table('canonical_graph_projections')->where('source_scope_id', $selected['scope_id'])->count())->toBe(1)
        ->and(DB::table('canonical_graph_projections')->where('source_scope_id', $other['scope_id'])->count())->toBe(0);
});

it('retries a failed exact projection without inserting a duplicate', function () {
    $context = canonicalRebuildHadesContext();
    $graph = app(CanonicalGraphRepository::class)->latestForScope($context['project_id'], 'workspace_binding', $context['scope_id']);
    $projection = app(CanonicalGraphProjectionService::class)->queue($graph);
    app(CanonicalGraphProjectionService::class)->markFailed($projection->id, 'neo4j_unavailable');
    Bus::fake();

    Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);

    expect(canonicalRebuildSummary())->toBe([
        'scanned' => 1,
        'queued' => 1,
        'ready' => 0,
        'failed' => 0,
        'skipped' => 0,
        'dry_run' => false,
    ])->and(DB::table('canonical_graph_projections')->count())->toBe(1)
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('queued')
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('error_code'))->toBeNull();
    Bus::assertDispatchedTimes(ProjectCanonicalGraphToNeo4j::class, 1);
});

it('reports and skips a ready exact artifact and checksum match', function () {
    $context = canonicalRebuildHadesContext();
    $graph = app(CanonicalGraphRepository::class)->latestForScope($context['project_id'], 'workspace_binding', $context['scope_id']);
    $projection = app(CanonicalGraphProjectionService::class)->queue($graph);
    app(CanonicalGraphProjectionService::class)->markReady($projection->id, 2, 1);
    Bus::fake();

    Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);

    expect(canonicalRebuildSummary())->toBe([
        'scanned' => 1,
        'queued' => 0,
        'ready' => 1,
        'failed' => 0,
        'skipped' => 0,
        'dry_run' => false,
    ])->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('ready');
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
});

it('skips an exact projection that is already queued or projecting', function (string $status) {
    $context = canonicalRebuildHadesContext();
    $graph = app(CanonicalGraphRepository::class)->latestForScope($context['project_id'], 'workspace_binding', $context['scope_id']);
    $projection = app(CanonicalGraphProjectionService::class)->queue($graph);
    DB::table('canonical_graph_projections')->where('id', $projection->id)->update(['status' => $status]);
    Bus::fake();

    Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);

    expect(canonicalRebuildSummary())->toBe([
        'scanned' => 1,
        'queued' => 0,
        'ready' => 0,
        'failed' => 0,
        'skipped' => 1,
        'dry_run' => false,
    ]);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
})->with(['queued', 'projecting']);

it('fails closed without dispatch when an artifact id is reused with a different checksum', function () {
    $context = canonicalRebuildHadesContext();
    $graph = app(CanonicalGraphRepository::class)->latestForScope($context['project_id'], 'workspace_binding', $context['scope_id']);
    $projection = app(CanonicalGraphProjectionService::class)->queue($graph);
    DB::table('hades_agent_artifacts')->where('id', $context['artifact_id'])->update(['sha256' => str_repeat('f', 64)]);
    Bus::fake();

    $exitCode = Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);

    expect($exitCode)->toBe(1)
        ->and(canonicalRebuildSummary())->toBe([
            'scanned' => 1,
            'queued' => 0,
            'ready' => 0,
            'failed' => 1,
            'skipped' => 0,
            'dry_run' => false,
        ])
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('checksum'))->toBe($graph['identity']['checksum']);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
});

it('fails closed when the persisted graph version does not match the exact identity', function () {
    $context = canonicalRebuildHadesContext();
    $graph = app(CanonicalGraphRepository::class)->latestForScope($context['project_id'], 'workspace_binding', $context['scope_id']);
    $projection = app(CanonicalGraphProjectionService::class)->queue($graph);
    DB::table('canonical_graph_projections')->where('id', $projection->id)->update([
        'graph_version' => str_repeat('e', 64),
        'status' => 'ready',
    ]);
    Bus::fake();

    $exitCode = Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);

    expect($exitCode)->toBe(1)
        ->and(canonicalRebuildSummary()['failed'])->toBe(1);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
});

it('keeps project-only invocation on the legacy rebuild path', function () {
    config()->set('services.devboard.graph_import_mode', 'fake');
    $context = canonicalRebuildLegacyContext();
    Bus::fake();

    $exitCode = Artisan::call('devboard:neo4j-rebuild', ['--project' => $context['project_id']]);

    expect($exitCode)->toBe(0)
        ->and(Artisan::output())->toContain('Scanned 1 graph snapshot(s).')
        ->and(Artisan::output())->not->toStartWith('{');
});

it('requires the explicit reconcile flag for canonical-only options', function (array $options) {
    Bus::fake();

    $exitCode = Artisan::call('devboard:neo4j-rebuild', $options);

    expect($exitCode)->toBe(1)
        ->and(trim(Artisan::output()))->toBe('Canonical options require --reconcile.')
        ->and(strlen(trim(Artisan::output())))->toBeLessThanOrEqual(120);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
})->with([
    'dry run' => [['--project' => '01TEST', '--dry-run' => true]],
    'scope type' => [['--project' => '01TEST', '--scope-type' => 'repository']],
    'scope id' => [['--project' => '01TEST', '--scope-id' => '01SCOPE']],
]);

it('dispatches a missing projection only once across repeated reconciliation', function () {
    $context = canonicalRebuildHadesContext();
    Bus::fake();

    Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);
    $first = canonicalRebuildSummary();
    Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);
    $second = canonicalRebuildSummary();

    expect($first['queued'])->toBe(1)
        ->and($second['queued'])->toBe(0)
        ->and($second['skipped'])->toBe(1)
        ->and(DB::table('canonical_graph_projections')->count())->toBe(1);
    Bus::assertDispatchedTimes(ProjectCanonicalGraphToNeo4j::class, 1);
});

it('dispatches a failed projection retry only once across repeated reconciliation', function () {
    $context = canonicalRebuildHadesContext();
    $graph = app(CanonicalGraphRepository::class)->latestForScope($context['project_id'], 'workspace_binding', $context['scope_id']);
    $projection = app(CanonicalGraphProjectionService::class)->queue($graph);
    app(CanonicalGraphProjectionService::class)->markFailed($projection->id, 'neo4j_unavailable');
    Bus::fake();

    Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);
    $first = canonicalRebuildSummary();
    Artisan::call('devboard:neo4j-rebuild', ['--reconcile' => true, '--project' => $context['project_id']]);
    $second = canonicalRebuildSummary();

    expect($first['queued'])->toBe(1)
        ->and($second['queued'])->toBe(0)
        ->and($second['skipped'])->toBe(1)
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('queued');
    Bus::assertDispatchedTimes(ProjectCanonicalGraphToNeo4j::class, 1);
});

it('does not mutate or dispatch a failed projection during dry-run', function () {
    $context = canonicalRebuildHadesContext();
    $graph = app(CanonicalGraphRepository::class)->latestForScope($context['project_id'], 'workspace_binding', $context['scope_id']);
    $projection = app(CanonicalGraphProjectionService::class)->queue($graph);
    app(CanonicalGraphProjectionService::class)->markFailed($projection->id, 'neo4j_unavailable');
    Bus::fake();

    Artisan::call('devboard:neo4j-rebuild', [
        '--reconcile' => true,
        '--project' => $context['project_id'],
        '--dry-run' => true,
    ]);

    expect(canonicalRebuildSummary()['queued'])->toBe(1)
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('failed')
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('error_code'))->toBe('neo4j_unavailable');
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
});

it('scans more than one scope chunk with bounded database queries and zero dry-run writes', function () {
    $first = canonicalRebuildHadesContext();
    foreach (range(1, 50) as $_) {
        canonicalRebuildHadesContext($first['project_id']);
    }
    Bus::fake();
    DB::flushQueryLog();
    DB::enableQueryLog();

    Artisan::call('devboard:neo4j-rebuild', [
        '--reconcile' => true,
        '--project' => $first['project_id'],
        '--dry-run' => true,
    ]);
    $summary = canonicalRebuildSummary();
    $queries = collect(DB::getQueryLog())
        ->pluck('query')
        ->filter(fn (string $sql): bool => str_contains($sql, 'hades_workspace_bindings')
            || str_contains($sql, 'hades_agent_artifacts')
            || str_contains($sql, 'repositories')
            || str_contains($sql, 'canonical_graph_projections'));

    expect($summary['scanned'])->toBe(51)
        ->and($summary['queued'])->toBe(51)
        ->and($queries->count())->toBeLessThanOrEqual(18)
        ->and(DB::table('canonical_graph_projections')->count())->toBe(0);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
});

it('resolves an exact scope directly without listing all project scopes', function () {
    $selected = canonicalRebuildHadesContext();
    foreach (range(1, 30) as $_) {
        canonicalRebuildHadesContext($selected['project_id']);
    }
    Bus::fake();
    DB::flushQueryLog();
    DB::enableQueryLog();

    Artisan::call('devboard:neo4j-rebuild', [
        '--reconcile' => true,
        '--project' => $selected['project_id'],
        '--scope-type' => 'workspace_binding',
        '--scope-id' => $selected['scope_id'],
        '--dry-run' => true,
    ]);
    $bindingQueries = collect(DB::getQueryLog())
        ->pluck('query')
        ->filter(fn (string $sql): bool => str_contains($sql, 'hades_workspace_bindings'));

    expect(canonicalRebuildSummary()['scanned'])->toBe(1)
        ->and($bindingQueries)->each->toContain('"id" = ?')
        ->and(DB::table('canonical_graph_projections')->count())->toBe(0);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
});

it('rejects invalid scope option combinations with a bounded safe message', function (array $options, string $message) {
    Bus::fake();

    $exitCode = Artisan::call('devboard:neo4j-rebuild', $options);

    expect($exitCode)->toBe(1)
        ->and(trim(Artisan::output()))->toBe($message)
        ->and(strlen(trim(Artisan::output())))->toBeLessThanOrEqual(120)
        ->and(DB::table('canonical_graph_projections')->count())->toBe(0);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);
})->with([
    'type only' => [['--reconcile' => true, '--project' => '01TEST', '--scope-type' => 'repository'], 'Both --scope-type and --scope-id are required together.'],
    'id only' => [['--reconcile' => true, '--project' => '01TEST', '--scope-id' => '01SCOPE'], 'Both --scope-type and --scope-id are required together.'],
    'scope without project' => [['--reconcile' => true, '--scope-type' => 'repository', '--scope-id' => '01SCOPE'], 'Canonical reconcile requires --project.'],
    'no project' => [['--reconcile' => true], 'Canonical reconcile requires --project.'],
    'unsupported scope' => [['--reconcile' => true, '--project' => '01TEST', '--scope-type' => 'filesystem', '--scope-id' => '01SCOPE'], 'Invalid --scope-type. Use workspace_binding or repository.'],
    'legacy option' => [['--reconcile' => true, '--project' => '01TEST', '--snapshot' => '01SNAPSHOT'], 'Canonical reconcile cannot use legacy rebuild options.'],
]);

function canonicalRebuildSummary(): array
{
    $summary = json_decode(trim(Artisan::output()), true, flags: JSON_THROW_ON_ERROR);

    expect(array_keys($summary))->toBe(['scanned', 'queued', 'ready', 'failed', 'skipped', 'dry_run']);

    return $summary;
}

/** @return array{project_id: string, scope_id: string, artifact_id: string} */
function canonicalRebuildHadesContext(?string $projectId = null): array
{
    $now = now();
    $ownerId = DB::table('users')->where('email', 'admin@example.com')->value('id');
    $projectId ??= (string) Str::ulid();
    if (! DB::table('projects')->where('id', $projectId)->exists()) {
        DB::table('projects')->insert([
            'id' => $projectId,
            'name' => 'Canonical rebuild',
            'slug' => 'canonical-rebuild-'.Str::lower(Str::random(10)),
            'description' => null,
            'status' => 'active',
            'default_code_exposure_policy' => 'full_code_artifacts',
            'created_by_user_id' => $ownerId,
            'created_at' => $now,
            'updated_at' => $now,
        ]);
    }
    $agentId = (string) Str::ulid();
    $scopeId = (string) Str::ulid();
    $artifactId = (string) Str::ulid();
    DB::table('hades_agents')->insert([
        'id' => $agentId,
        'project_id' => $projectId,
        'external_agent_id' => 'rebuild-'.Str::lower(Str::random(8)),
        'label' => 'Rebuild test',
        'platform' => 'test',
        'version' => '1',
        'declared_capabilities' => '[]',
        'effective_capabilities' => '[]',
        'last_seen_at' => $now,
        'status' => 'active',
        'created_at' => $now,
        'updated_at' => $now,
    ]);
    DB::table('hades_workspace_bindings')->insert([
        'id' => $scopeId,
        'project_id' => $projectId,
        'hades_agent_id' => $agentId,
        'external_agent_id' => 'rebuild-agent',
        'local_project_id' => null,
        'workspace_fingerprint' => 'wf_'.Str::random(12),
        'display_path' => '/private/rebuild-test',
        'git_remote_display' => null,
        'git_remote_hash' => null,
        'head_commit' => str_repeat('a', 40),
        'platform' => 'test',
        'status' => 'linked',
        'linked_at' => $now,
        'unlinked_at' => null,
        'last_seen_at' => $now,
        'created_at' => $now,
        'updated_at' => $now,
    ]);
    $payload = [
        'schema' => 'hades.code_graph.v1',
        'language' => 'php',
        'graph_contract' => [
            'version' => 'hades.graph_artifact.v1',
            'extractor' => ['name' => 'test', 'version' => '1', 'mode' => 'native', 'quality' => 'full', 'fallback_reason' => null],
            'coverage' => ['languages' => ['php'], 'files_total' => 1, 'files_analyzed' => 1, 'files_failed' => 0],
            'source' => ['branch' => 'main', 'head_commit' => str_repeat('a', 40)],
        ],
        'nodes' => [['id' => 'class:A', 'kind' => 'class', 'name' => 'A']],
        'relationships' => [],
    ];
    $json = json_encode($payload, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
    DB::table('hades_agent_artifacts')->insert([
        'id' => $artifactId,
        'project_id' => $projectId,
        'hades_agent_id' => $agentId,
        'workspace_binding_id' => $scopeId,
        'job_id' => null,
        'schema' => 'hades.code_graph.v1',
        'artifact' => $json,
        'sha256' => hash('sha256', $json),
        'truncated' => false,
        'redactions' => 0,
        'created_at' => $now,
        'updated_at' => $now,
    ]);

    return ['project_id' => $projectId, 'scope_id' => $scopeId, 'artifact_id' => $artifactId];
}

/** @return array{project_id: string, scope_id: string} */
function canonicalRebuildLegacyContext(): array
{
    $now = now();
    $ownerId = DB::table('users')->where('email', 'admin@example.com')->value('id');
    $projectId = (string) Str::ulid();
    $repositoryId = (string) Str::ulid();
    $deviceId = (string) Str::ulid();
    $workspaceId = (string) Str::ulid();
    $runId = (string) Str::ulid();
    $artifactId = (string) Str::ulid();
    $snapshotId = (string) Str::ulid();
    $path = "devboard/artifacts/rebuild/{$artifactId}.json";
    DB::table('projects')->insert(['id' => $projectId, 'name' => 'Legacy rebuild', 'slug' => 'legacy-rebuild-'.Str::lower(Str::random(10)), 'description' => null, 'status' => 'active', 'default_code_exposure_policy' => 'full_code_artifacts', 'created_by_user_id' => $ownerId, 'created_at' => $now, 'updated_at' => $now]);
    DB::table('repositories')->insert(['id' => $repositoryId, 'project_id' => $projectId, 'name' => 'Legacy', 'slug' => 'legacy', 'default_branch' => 'main', 'local_only' => true, 'code_exposure_policy' => 'full_code_artifacts', 'protected_paths' => '[]', 'excluded_paths' => '[]', 'stack_hints' => '[]', 'graph_enabled' => true, 'created_at' => $now, 'updated_at' => $now]);
    DB::table('devices')->insert(['id' => $deviceId, 'user_id' => $ownerId, 'name' => 'Device', 'fingerprint_hash' => 'sha256:'.Str::random(32), 'platform_os' => 'linux', 'platform_arch' => 'amd64', 'plugin_version' => '1', 'last_seen_at' => $now, 'status' => 'active', 'created_at' => $now, 'updated_at' => $now]);
    DB::table('local_workspaces')->insert(['id' => $workspaceId, 'repository_id' => $repositoryId, 'device_id' => $deviceId, 'local_root_hash' => 'sha256:'.Str::random(32), 'display_path' => '/private/legacy', 'current_branch' => 'main', 'last_head_sha' => 'abc123', 'dirty_status' => 'clean', 'last_snapshot_id' => null, 'last_seen_at' => $now, 'created_at' => $now, 'updated_at' => $now]);
    DB::table('runs')->insert(['id' => $runId, 'project_id' => $projectId, 'repository_id' => $repositoryId, 'local_workspace_id' => $workspaceId, 'task_id' => null, 'device_id' => $deviceId, 'started_by_user_id' => $ownerId, 'runtime_profile' => 'test', 'status' => 'finished', 'branch' => 'main', 'base_branch' => 'main', 'base_sha' => 'abc123', 'head_sha' => 'abc123', 'summary' => null, 'risk_level' => 'low', 'started_at' => $now, 'finished_at' => $now, 'created_at' => $now, 'updated_at' => $now]);
    $json = json_encode(['nodes' => [['id' => 'class:Legacy', 'labels' => ['Class'], 'properties' => ['name' => 'Legacy']]], 'relationships' => []], JSON_THROW_ON_ERROR);
    Storage::disk('local')->put($path, $json);
    DB::table('artifacts')->insert(['id' => $artifactId, 'project_id' => $projectId, 'repository_id' => $repositoryId, 'run_id' => $runId, 'artifact_type' => 'graph_snapshot', 'storage_path' => $path, 'sha256' => hash('sha256', $json), 'size_bytes' => strlen($json), 'mime_type' => 'application/json', 'schema_version' => 'v1', 'status' => 'validated', 'producer' => 'test', 'metadata' => '{}', 'created_at' => $now, 'updated_at' => $now]);
    DB::table('snapshots')->insert(['id' => $snapshotId, 'project_id' => $projectId, 'repository_id' => $repositoryId, 'local_workspace_id' => $workspaceId, 'source_type' => 'local_plugin_snapshot', 'branch' => 'main', 'base_sha' => 'abc123', 'head_sha' => 'abc123', 'dirty_status' => 'clean', 'file_inventory_artifact_id' => null, 'graph_snapshot_artifact_id' => $artifactId, 'created_by_run_id' => $runId, 'created_at' => $now]);

    return ['project_id' => $projectId, 'scope_id' => $repositoryId];
}
