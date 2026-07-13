<?php

use App\Services\Graph\CanonicalGraphQueryService;
use App\Services\Graph\GraphQueryService;
use App\Services\Neo4j\FakeNeo4jClient;
use Database\Seeders\DevBoardSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

uses(RefreshDatabase::class);

beforeEach(function () {
    $this->seed(DevBoardSeeder::class);

    $client = new FakeNeo4jClient;
    $this->app->instance(FakeNeo4jClient::class, $client);
    $this->app->bind(GraphQueryService::class, fn () => new GraphQueryService($client));
    $this->app->bind(CanonicalGraphQueryService::class, fn () => new CanonicalGraphQueryService($client));
});

function graphQueryProjectId(): string
{
    return DB::table('projects')->where('slug', 'demo-project')->value('id');
}

function graphQueryEnsureSnapshot(string $projectId, string $repoId, string $createdByUserId): string
{
    $deviceId = (string) Str::ulid();
    $workspaceId = (string) Str::ulid();
    $runId = (string) Str::ulid();
    $snapshotId = (string) Str::ulid();

    DB::table('devices')->insert([
        'id' => $deviceId,
        'user_id' => $createdByUserId,
        'name' => 'gq-test-device',
        'fingerprint_hash' => 'fake-gq-fingerprint',
        'platform_os' => 'linux',
        'platform_arch' => 'amd64',
        'plugin_version' => '0.1.0',
        'status' => 'active',
        'last_seen_at' => now(),
        'created_at' => now()->addSecond(),
        'updated_at' => now()->addSecond(),
    ]);

    DB::table('local_workspaces')->insert([
        'id' => $workspaceId,
        'repository_id' => $repoId,
        'device_id' => $deviceId,
        'local_root_hash' => 'fake-root-hash',
        'display_path' => '/tmp/fake-repo',
        'current_branch' => 'main',
        'last_head_sha' => 'abc123',
        'dirty_status' => 'clean',
        'last_seen_at' => now(),
        'created_at' => now(),
        'updated_at' => now(),
    ]);

    DB::table('runs')->insert([
        'id' => $runId,
        'project_id' => $projectId,
        'repository_id' => $repoId,
        'local_workspace_id' => $workspaceId,
        'device_id' => $deviceId,
        'started_by_user_id' => $createdByUserId,
        'runtime_profile' => 'default',
        'status' => 'completed',
        'branch' => 'main',
        'base_branch' => 'main',
        'base_sha' => 'abc123',
        'head_sha' => 'abc123',
        'risk_level' => 'low',
        'started_at' => now(),
        'created_at' => now(),
        'updated_at' => now(),
    ]);

    $artifactId = (string) Str::ulid();
    DB::table('artifacts')->insert([
        'id' => $artifactId,
        'project_id' => $projectId,
        'repository_id' => $repoId,
        'run_id' => $runId,
        'artifact_type' => 'graph_snapshot',
        'storage_path' => 'fake-graph.json',
        'sha256' => 'test-hash',
        'size_bytes' => 100,
        'mime_type' => 'application/json',
        'schema_version' => 'v1',
        'producer' => 'devboard-plugin',
        'status' => 'uploaded',
        'metadata' => json_encode([]),
        'created_at' => now(),
        'updated_at' => now(),
    ]);

    DB::table('snapshots')->insert([
        'id' => $snapshotId,
        'project_id' => $projectId,
        'repository_id' => $repoId,
        'local_workspace_id' => $workspaceId,
        'source_type' => 'local_plugin_snapshot',
        'branch' => 'main',
        'base_sha' => 'abc123',
        'head_sha' => 'abc123',
        'dirty_status' => 'clean',
        'graph_snapshot_artifact_id' => $artifactId,
        'created_by_run_id' => $runId,
        'created_at' => now(),
    ]);

    DB::table('canonical_graph_projections')->insert([
        'id' => (string) Str::ulid(),
        'project_id' => $projectId,
        'source_scope_type' => 'repository',
        'source_scope_id' => $repoId,
        'artifact_type' => 'graph_snapshot',
        'artifact_id' => $artifactId,
        'checksum' => str_repeat('b', 64),
        'graph_version' => 'graph-version-'.$snapshotId,
        'quality' => 'verified',
        'status' => 'ready',
        'created_at' => now(),
        'updated_at' => now(),
    ]);

    return $snapshotId;
}

function graphQueryUserId(): string
{
    return DB::table('users')->where('email', 'admin@example.com')->value('id');
}

function graphQueryCreateToken(string $scopes = 'projects.read'): array
{
    $user = DB::table('users')->where('email', 'admin@example.com')->first();
    $id = (string) Str::ulid();
    $secret = 'test-gq-secret';
    $prefix = 'devb_live_'.$id;
    $now = now();

    DB::table('api_tokens')->insert([
        'id' => $id,
        'token_prefix' => $prefix,
        'token_hash' => hash('sha256', $secret),
        'user_id' => $user->id,
        'device_id' => null,
        'name' => 'Graph Query Token',
        'scopes' => json_encode(explode(',', $scopes), JSON_THROW_ON_ERROR),
        'expires_at' => now()->addMonth(),
        'revoked_at' => null,
        'last_used_at' => null,
        'created_at' => $now,
        'updated_at' => $now,
    ]);

    return [
        'id' => $id,
        'plain_token' => $prefix.'|'.$secret,
    ];
}

function graphQueryHeaders(string $token): array
{
    return [
        'Authorization' => 'Bearer '.$token,
        'Accept' => 'application/json',
        'Content-Type' => 'application/json',
        'X-DevBoard-Protocol' => 'v1',
        'X-DevBoard-Plugin-Version' => '0.1.0',
    ];
}

function graphQueryBody(array $overrides = []): array
{
    return array_merge(['protocol_version' => 'v1'], $overrides);
}

function graphQueryFakeClient(): FakeNeo4jClient
{
    return app(FakeNeo4jClient::class);
}

it('returns 200 with structured query results for callers', function () {
    $token = graphQueryCreateToken('projects.read');
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    $userId = graphQueryUserId();
    graphQueryEnsureSnapshot($projectId, $repo->id, $userId);

    $response = $this->postJson(
        '/api/plugin/v1/projects/'.$projectId.'/graph/query',
        graphQueryBody(['type' => 'callers', 'symbol_id' => 'App\\Services\\InvoiceService', 'limit' => 10]),
        graphQueryHeaders($token['plain_token']),
    )->assertOk()
        ->assertJsonPath('protocol_version', 'v1')
        ->assertJsonPath('query_type', 'callers')
        ->assertJsonPath('source_scope_type', 'repository')
        ->assertJsonPath('source_scope_id', $repo->id);

    expect($response->json('found'))->toBeTrue();
});

it('returns 200 with structured query results for callees', function () {
    $token = graphQueryCreateToken('projects.read');
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    $userId = graphQueryUserId();
    graphQueryEnsureSnapshot($projectId, $repo->id, $userId);

    $response = $this->postJson(
        '/api/plugin/v1/projects/'.$projectId.'/graph/query',
        graphQueryBody(['type' => 'callees', 'symbol_id' => 'App\\Services\\InvoiceService', 'limit' => 10]),
        graphQueryHeaders($token['plain_token']),
    )->assertOk()
        ->assertJsonPath('protocol_version', 'v1')
        ->assertJsonPath('query_type', 'callees');

    expect($response->json('found'))->toBeTrue();
});

it('returns 200 with structured query results for path queries', function () {
    $token = graphQueryCreateToken('projects.read');
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    $userId = graphQueryUserId();
    graphQueryEnsureSnapshot($projectId, $repo->id, $userId);

    $response = $this->postJson(
        '/api/plugin/v1/projects/'.$projectId.'/graph/query',
        graphQueryBody(['type' => 'path', 'from_symbol_id' => 'App\\Controllers\\Foo', 'to_symbol_id' => 'App\\Services\\Bar', 'max_depth' => 5]),
        graphQueryHeaders($token['plain_token']),
    )->assertOk()
        ->assertJsonPath('protocol_version', 'v1')
        ->assertJsonPath('query_type', 'path');

    expect($response->json('found'))->toBeTrue();
});

it('returns 403 for missing projects.read scope', function () {
    $token = graphQueryCreateToken('runs.write');
    $projectId = graphQueryProjectId();

    $this->postJson(
        '/api/plugin/v1/projects/'.$projectId.'/graph/query',
        graphQueryBody(['type' => 'callers', 'symbol_id' => 'App\\Services\\InvoiceService', 'limit' => 10]),
        graphQueryHeaders($token['plain_token']),
    )->assertForbidden();
});

it('returns 422 for unsupported query type', function () {
    $token = graphQueryCreateToken('projects.read');
    $projectId = graphQueryProjectId();

    $this->postJson(
        '/api/plugin/v1/projects/'.$projectId.'/graph/query',
        graphQueryBody(['type' => 'invalid', 'symbol_id' => 'App\\Services\\InvoiceService']),
        graphQueryHeaders($token['plain_token']),
    )->assertStatus(422);
});

it('returns 401 without a valid token', function () {
    $projectId = graphQueryProjectId();

    $this->postJson(
        '/api/plugin/v1/projects/'.$projectId.'/graph/query',
        graphQueryBody(['type' => 'callers', 'symbol_id' => 'test']),
    )->assertUnauthorized();
});

it('resolves caller edges from fake neo4j client', function () {
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    $userId = graphQueryUserId();
    $snapshotId = graphQueryEnsureSnapshot($projectId, $repo->id, $userId);

    $this->app->bind(GraphQueryService::class, function () {
        return new GraphQueryService(new FakeNeo4jClient);
    });

    $token = graphQueryCreateToken('projects.read');

    $this->postJson(
        '/api/plugin/v1/projects/'.$projectId.'/graph/query',
        graphQueryBody(['type' => 'callers', 'symbol_id' => 'App\\Services\\InvoiceService', 'limit' => 10]),
        graphQueryHeaders($token['plain_token']),
    )->assertOk()
        ->assertJsonPath('protocol_version', 'v1')
        ->assertJsonPath('query_type', 'callers')
        ->assertJsonPath('found', true);
});

it('callers cypher uses canonical graph version and external_id', function () {
    $fakeClient = new FakeNeo4jClient;
    $service = new GraphQueryService($fakeClient);

    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    $userId = graphQueryUserId();
    graphQueryEnsureSnapshot($projectId, $repo->id, $userId);

    $service->query('callers', [
        'project_id' => $projectId,
        'symbol_id' => 'TestSymbol',
        'limit' => 5,
    ]);

    expect($fakeClient->commands)->toHaveCount(1);
    $cmd = $fakeClient->commands[0];

    expect($cmd['cypher'])->toContain('external_id');
    expect($cmd['cypher'])->toContain('CanonicalGraphNode');
    expect($cmd['cypher'])->not->toContain('{symbol_id:');

    expect($cmd['params'])->toHaveKey('graph_version');
    expect($cmd['params']['external_id'])->toBe('TestSymbol');
    expect($cmd['params'])->not->toHaveKey('symbol_id');
});

it('queries the current canonical graph projection', function () {
    $fakeClient = new FakeNeo4jClient;
    $service = new GraphQueryService($fakeClient);
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    $userId = graphQueryUserId();
    graphQueryEnsureSnapshot($projectId, $repo->id, $userId);
    DB::table('canonical_graph_projections')->insert([
        'id' => (string) Str::ulid(),
        'project_id' => $projectId,
        'source_scope_type' => 'repository',
        'source_scope_id' => $repo->id,
        'artifact_type' => 'graph_snapshot',
        'artifact_id' => (string) Str::ulid(),
        'checksum' => str_repeat('a', 64),
        'graph_version' => 'canonical-version-1',
        'quality' => 'verified',
        'status' => 'ready',
        'created_at' => now()->addSecond(),
        'updated_at' => now()->addSecond(),
    ]);

    $service->query('callers', ['project_id' => $projectId, 'symbol_id' => 'TestSymbol']);

    expect($fakeClient->commands[0]['cypher'])->toContain('CanonicalGraphNode')
        ->and($fakeClient->commands[0]['params']['graph_version'])->toBe('canonical-version-1');
});

it('callees cypher uses canonical graph version and external_id', function () {
    $fakeClient = new FakeNeo4jClient;
    $service = new GraphQueryService($fakeClient);

    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    $userId = graphQueryUserId();
    graphQueryEnsureSnapshot($projectId, $repo->id, $userId);

    $service->query('callees', [
        'project_id' => $projectId,
        'symbol_id' => 'TestSymbol',
        'limit' => 5,
    ]);

    expect($fakeClient->commands)->toHaveCount(1);
    $cmd = $fakeClient->commands[0];

    expect($cmd['cypher'])->toContain('external_id');
    expect($cmd['cypher'])->toContain('CanonicalGraphNode');
    expect($cmd['cypher'])->not->toContain('{symbol_id:');

    expect($cmd['params'])->toHaveKey('graph_version');
    expect($cmd['params']['external_id'])->toBe('TestSymbol');
    expect($cmd['params'])->not->toHaveKey('symbol_id');
});

it('path cypher uses canonical graph version and external_id', function () {
    $fakeClient = new FakeNeo4jClient;
    $service = new GraphQueryService($fakeClient);

    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    $userId = graphQueryUserId();
    graphQueryEnsureSnapshot($projectId, $repo->id, $userId);

    $service->query('path', [
        'project_id' => $projectId,
        'from_symbol_id' => 'FromSymbol',
        'to_symbol_id' => 'ToSymbol',
        'max_depth' => 5,
    ]);

    expect($fakeClient->commands)->toHaveCount(1);
    $cmd = $fakeClient->commands[0];

    expect($cmd['cypher'])->toContain('external_id');
    expect($cmd['cypher'])->toContain('CanonicalGraphNode');
    expect($cmd['cypher'])->toContain(':CALLS*1..5');
    expect($cmd['cypher'])->not->toContain('{symbol_id:');
    expect($cmd['cypher'])->not->toContain('{from_symbol_id:');

    expect($cmd['params'])->toHaveKey('graph_version');
    expect($cmd['params']['from_external_id'])->toBe('FromSymbol');
    expect($cmd['params']['to_external_id'])->toBe('ToSymbol');
    expect($cmd['params'])->not->toHaveKey('from_symbol_id');
    expect($cmd['params'])->not->toHaveKey('to_symbol_id');
});

it('returns graph_snapshot_not_found when project has no snapshot', function () {
    $fakeClient = new FakeNeo4jClient;
    $service = new GraphQueryService($fakeClient);

    $projectId = graphQueryProjectId();
    DB::table('snapshots')->where('project_id', $projectId)->delete();

    $result = $service->query('callers', [
        'project_id' => $projectId,
        'symbol_id' => 'SomeSymbol',
    ]);

    expect($result['found'])->toBeFalse();
    expect($result['reason'])->toBe('graph_snapshot_not_found');
    expect($result['results'])->toBe([]);
    expect($fakeClient->commands)->toHaveCount(0);
});

it('queries are scoped to the project canonical graph version', function () {
    $fakeClient = new FakeNeo4jClient;
    $service = new GraphQueryService($fakeClient);

    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    $userId = graphQueryUserId();
    $snapshotId = graphQueryEnsureSnapshot($projectId, $repo->id, $userId);

    $service->query('callers', [
        'project_id' => $projectId,
        'symbol_id' => 'SomeSymbol',
    ]);

    expect($fakeClient->commands)->toHaveCount(1);
    expect($fakeClient->commands[0]['params']['graph_version'])->toBe('graph-version-'.$snapshotId);
});

it('returns canonical projection metadata and rejects a scope from another project', function () {
    $fakeClient = new FakeNeo4jClient;
    $service = new CanonicalGraphQueryService($fakeClient);
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    graphQueryEnsureSnapshot($projectId, $repo->id, graphQueryUserId());

    $result = $service->query($projectId, 'repository', $repo->id, 'callers', ['symbol_id' => 'TestSymbol']);
    expect($result['metadata']['source_scope_type'])->toBe('repository')
        ->and($result['metadata']['source_scope_id'])->toBe($repo->id)
        ->and($result['metadata']['graph_version'])->toStartWith('graph-version-');

    $otherProject = DB::table('projects')->where('id', '!=', $projectId)->value('id');
    $isolated = $service->query($projectId, 'repository', (string) $otherProject, 'callers', ['symbol_id' => 'TestSymbol']);
    expect($isolated['found'])->toBeFalse()->and($isolated['reason'])->toBe('graph_scope_not_found');
});

it('emits version-isolated Cypher for every traversal direction', function (string $direction, string $pattern) {
    $fakeClient = new FakeNeo4jClient;
    $service = new CanonicalGraphQueryService($fakeClient);
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    graphQueryEnsureSnapshot($projectId, $repo->id, graphQueryUserId());

    $service->query($projectId, 'repository', $repo->id, 'traverse', [
        'start' => 'Start', 'direction' => $direction, 'max_depth' => 3, 'limit' => 10,
    ]);

    $cypher = $fakeClient->commands[0]['cypher'];
    expect($cypher)->toContain($pattern)
        ->and($cypher)->toContain('start:CanonicalGraphNode {external_id: $start, graph_version: $graph_version}')
        ->and($cypher)->toContain('node:CanonicalGraphNode {graph_version: $graph_version}')
        ->and($cypher)->toContain('ALL(n IN nodes(p) WHERE n.graph_version = $graph_version)')
        ->and($cypher)->toContain('ALL(r IN relationships(p) WHERE r.graph_version = $graph_version)')
        ->and($fakeClient->commands[0]['params']['graph_version'])->toStartWith('graph-version-');
})->with([
    'out' => ['out', '-[:CALLS*1..3]->'],
    'in' => ['in', '<-[:CALLS*1..3]-'],
    'any' => ['any', '-[:CALLS*1..3]-'],
]);

it('returns path relationships and constrains every path node and edge to the graph version', function () {
    $fakeClient = new FakeNeo4jClient;
    $service = new CanonicalGraphQueryService($fakeClient);
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    graphQueryEnsureSnapshot($projectId, $repo->id, graphQueryUserId());

    $service->query($projectId, 'repository', $repo->id, 'path', [
        'from_symbol_id' => 'From', 'to_symbol_id' => 'To', 'max_depth' => 4,
    ]);

    $cypher = $fakeClient->commands[0]['cypher'];
    expect($cypher)->toContain('ALL(n IN nodes(p) WHERE n.graph_version = $graph_version)')
        ->and($cypher)->toContain('ALL(r IN relationships(p) WHERE r.graph_version = $graph_version)')
        ->and($cypher)->toContain('RETURN [n IN nodes(p) | {node: properties(n), labels: labels(n)}] AS nodes')
        ->and($cypher)->toContain('[r IN relationships(p) | properties(r)] AS edges')
        ->and($cypher)->not->toContain('UNWIND nodes(p)');
});

it('rejects plugin traversal exposure and graph version overrides', function () {
    $token = graphQueryCreateToken('projects.read');
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    graphQueryEnsureSnapshot($projectId, $repo->id, graphQueryUserId());

    $this->postJson('/api/plugin/v1/projects/'.$projectId.'/graph/query', graphQueryBody([
        'type' => 'traverse', 'start' => 'Start', 'repository_id' => $repo->id,
    ]), graphQueryHeaders($token['plain_token']))->assertStatus(422);

    $this->postJson('/api/plugin/v1/projects/'.$projectId.'/graph/query', graphQueryBody([
        'type' => 'callers', 'symbol_id' => 'Start', 'repository_id' => $repo->id, 'graph_version' => 'forged',
    ]), graphQueryHeaders($token['plain_token']))->assertStatus(422);
});

it('requires an explicit scope when zero or multiple graph scopes exist', function () {
    $token = graphQueryCreateToken('projects.read');
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    DB::table('repositories')->where('id', $repo->id)->delete();

    $this->postJson('/api/plugin/v1/projects/'.$projectId.'/graph/query', graphQueryBody([
        'type' => 'callers', 'symbol_id' => 'Start',
    ]), graphQueryHeaders($token['plain_token']))->assertStatus(422)->assertJsonPath('reason', 'scope_required');

    DB::table('repositories')->insert([
        'id' => (string) Str::ulid(), 'project_id' => $projectId, 'name' => 'second', 'slug' => 'second',
        'default_branch' => 'main', 'local_only' => true, 'code_exposure_policy' => 'full_code_artifacts',
        'protected_paths' => json_encode([]), 'excluded_paths' => json_encode([]), 'stack_hints' => json_encode([]),
        'graph_enabled' => true, 'created_at' => now(), 'updated_at' => now(),
    ]);
    DB::table('repositories')->insert([
        'id' => (string) Str::ulid(), 'project_id' => $projectId, 'name' => 'third', 'slug' => 'third',
        'default_branch' => 'main', 'local_only' => true, 'code_exposure_policy' => 'full_code_artifacts',
        'protected_paths' => json_encode([]), 'excluded_paths' => json_encode([]), 'stack_hints' => json_encode([]),
        'graph_enabled' => true, 'created_at' => now(), 'updated_at' => now(),
    ]);
    DB::table('canonical_graph_projections')->insert([
        'id' => (string) Str::ulid(), 'project_id' => $projectId, 'source_scope_type' => 'repository',
        'source_scope_id' => $repo->id, 'artifact_type' => 'graph_snapshot', 'artifact_id' => (string) Str::ulid(),
        'checksum' => str_repeat('c', 64), 'graph_version' => 'second-version', 'quality' => 'verified',
        'status' => 'ready', 'created_at' => now()->addSecond(), 'updated_at' => now()->addSecond(),
    ]);

    $this->postJson('/api/plugin/v1/projects/'.$projectId.'/graph/query', graphQueryBody([
        'type' => 'callers', 'symbol_id' => 'Start',
    ]), graphQueryHeaders($token['plain_token']))->assertStatus(422)->assertJsonPath('reason', 'scope_required');
});

it('defaults an omitted scope only when exactly one graph scope exists', function () {
    $token = graphQueryCreateToken('projects.read');
    $projectId = graphQueryProjectId();
    $repo = DB::table('repositories')->where('project_id', $projectId)->first();
    graphQueryEnsureSnapshot($projectId, $repo->id, graphQueryUserId());

    $this->postJson('/api/plugin/v1/projects/'.$projectId.'/graph/query', graphQueryBody([
        'type' => 'callers', 'symbol_id' => 'Start',
    ]), graphQueryHeaders($token['plain_token']))
        ->assertOk()
        ->assertJsonPath('source_scope_type', 'repository')
        ->assertJsonPath('source_scope_id', $repo->id)
        ->assertJsonPath('found', true);

    expect(graphQueryFakeClient()->commands)->toHaveCount(1);
});

it('rejects foreign repository and linked binding scopes before Neo4j commands', function () {
    $fakeClient = graphQueryFakeClient();
    $service = new CanonicalGraphQueryService($fakeClient);
    $projectId = graphQueryProjectId();
    $foreignProject = DB::table('projects')->where('id', '!=', $projectId)->first();
    if ($foreignProject === null) {
        $foreignProjectId = (string) Str::ulid();
        DB::table('projects')->insert([
            'id' => $foreignProjectId, 'name' => 'Foreign', 'slug' => 'foreign-'.Str::lower(Str::random(6)),
            'status' => 'active', 'default_code_exposure_policy' => 'full_code_artifacts',
            'created_by_user_id' => graphQueryUserId(), 'created_at' => now(), 'updated_at' => now(),
        ]);
        $foreignProject = DB::table('projects')->where('id', $foreignProjectId)->first();
    }
    $foreignRepoId = (string) Str::ulid();
    DB::table('repositories')->insert([
        'id' => $foreignRepoId, 'project_id' => $foreignProject->id, 'name' => 'foreign', 'slug' => 'foreign',
        'default_branch' => 'main', 'local_only' => true, 'code_exposure_policy' => 'full_code_artifacts',
        'protected_paths' => json_encode([]), 'excluded_paths' => json_encode([]), 'stack_hints' => json_encode([]),
        'graph_enabled' => true, 'created_at' => now(), 'updated_at' => now(),
    ]);
    $foreignRepo = DB::table('repositories')->where('project_id', $foreignProject->id)->first();

    expect($service->query($projectId, 'repository', $foreignRepo->id, 'callers', ['symbol_id' => 'Start'])['reason'])
        ->toBe('graph_scope_not_found');

    $agentId = (string) Str::ulid();
    $bindingId = (string) Str::ulid();
    DB::table('hades_agents')->insert([
        'id' => $agentId, 'project_id' => $foreignProject->id, 'external_agent_id' => 'foreign-agent', 'label' => 'foreign',
        'platform' => 'test', 'version' => '1', 'declared_capabilities' => json_encode([]), 'effective_capabilities' => json_encode([]),
        'status' => 'active', 'created_at' => now(), 'updated_at' => now(),
    ]);
    DB::table('hades_workspace_bindings')->insert([
        'id' => $bindingId, 'project_id' => $foreignProject->id, 'hades_agent_id' => $agentId, 'external_agent_id' => 'foreign-agent',
        'workspace_fingerprint' => 'foreign-fingerprint', 'display_path' => '/foreign', 'status' => 'linked',
        'linked_at' => now(), 'created_at' => now(), 'updated_at' => now(),
    ]);

    expect($service->query($projectId, 'workspace_binding', $bindingId, 'callers', ['symbol_id' => 'Start'])['reason'])
        ->toBe('graph_scope_not_found')
        ->and($fakeClient->commands)->toBe([]);

    $pluginToken = graphQueryCreateToken('projects.read');
    $response = $this->postJson('/api/plugin/v1/projects/'.$projectId.'/graph/query', graphQueryBody([
        'type' => 'callers', 'symbol_id' => 'Start', 'repository_id' => $foreignRepo->id,
    ]), graphQueryHeaders($pluginToken['plain_token']))
        ->assertOk()->assertJsonPath('reason', 'graph_scope_not_found');

    expect($response->json('results'))->toBe([]);
    expect($fakeClient->commands)->toBe([]);

    $bindingResponse = $this->postJson('/api/plugin/v1/projects/'.$projectId.'/graph/query', graphQueryBody([
        'type' => 'callers', 'symbol_id' => 'Start', 'workspace_binding_id' => $bindingId,
    ]), graphQueryHeaders($pluginToken['plain_token']))
        ->assertOk()->assertJsonPath('reason', 'graph_scope_not_found');

    expect($bindingResponse->json('results'))->toBe([]);
    expect($fakeClient->commands)->toBe([]);
});
