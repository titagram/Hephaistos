<?php

use App\Jobs\ProjectCanonicalGraphToNeo4j;
use App\Models\User;
use App\Services\Graph\CanonicalGraphProjectionService;
use App\Services\Graph\CanonicalGraphRepository;
use App\Services\Graph\Neo4jCanonicalGraphProjector;
use App\Services\Neo4j\FakeNeo4jClient;
use App\Services\Neo4j\Neo4jClient;
use App\Services\Neo4jClientFactory;
use App\Services\Neo4jRebuildService;
use Database\Seeders\DevBoardSeeder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Bus;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

uses(RefreshDatabase::class);

beforeEach(fn () => $this->seed(DevBoardSeeder::class));

it('queues one projection for a new canonical graph and none for its duplicate', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $payload = canonicalProjectionUpload($agent, $bindingId);

    $first = $this->postJson('/api/hades/v1/artifacts', $payload, ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->first();

    expect($projection)->not->toBeNull()->and($projection->artifact_id)->toBe($first->json('artifact.id'));
    Bus::assertDispatchedTimes(ProjectCanonicalGraphToNeo4j::class, 1);
    Bus::assertDispatched(fn (ProjectCanonicalGraphToNeo4j $job) => $job->projectionId === $projection->id);

    $this->postJson('/api/hades/v1/artifacts', $payload, ['Authorization' => 'Bearer '.$agent['token']])
        ->assertOk()->assertJsonPath('deduplicated', true);
    Bus::assertDispatchedTimes(ProjectCanonicalGraphToNeo4j::class, 1);
});

it('projects batches with project scope and graph version', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->first();
    $graph = app(CanonicalGraphRepository::class)->findByIdentity($agent['project_id'], 'workspace_binding', $bindingId, 'hades_agent_artifact', $response->json('artifact.id'));
    $client = new FakeNeo4jClient;

    $counts = app(Neo4jCanonicalGraphProjector::class)->project($graph, $projection, $client);

    expect($counts)->toBe(['nodes' => 2, 'relationships' => 1]);
    foreach (['UNWIND $nodes', 'UNWIND $relationships'] as $needle) {
        $command = collect($client->commands)->first(fn (array $item) => str_contains($item['cypher'], $needle));
        expect($command['params'])->toMatchArray(['project_id' => $agent['project_id'], 'source_scope_type' => 'workspace_binding', 'source_scope_id' => $bindingId, 'graph_version' => $projection->graph_version]);
    }
});

it('refuses to switch current when verification is empty or mismatched', function (array $verification) {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->first();
    $graph = app(CanonicalGraphRepository::class)->findByIdentity($agent['project_id'], 'workspace_binding', $bindingId, 'hades_agent_artifact', $response->json('artifact.id'));
    $client = new class($verification) implements Neo4jClient
    {
        public array $commands = [];

        public function __construct(private array $verification) {}

        public function run(string $cypher, array $parameters = []): mixed
        {
            $this->commands[] = ['cypher' => $cypher, 'params' => $parameters];

            return str_contains($cypher, 'RETURN nodes') ? $this->verification : [];
        }
    };

    expect(fn () => app(Neo4jCanonicalGraphProjector::class)->project($graph, $projection, $client))->toThrow(RuntimeException::class);
    expect(collect($client->commands)->contains(fn (array $command) => str_contains($command['cypher'], 'candidate.current = true')))->toBeFalse();
})->with(['empty' => [[]], 'mismatch' => [[['nodes' => 1, 'relationships' => 1]]], 'non numeric' => [[['nodes' => 'two', 'relationships' => 1]]]]);

it('reasserts reserved identity properties after payload properties', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $payload = canonicalProjectionUpload($agent, $bindingId);
    $payload['artifact']['nodes'][0]['properties'] = ['graph_version' => 'evil', 'external_id' => 'evil'];
    $payload['artifact']['relationships'][0]['properties'] = ['graph_version' => 'evil', 'external_id' => 'evil'];
    $response = $this->postJson('/api/hades/v1/artifacts', $payload, ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->first();
    $graph = app(CanonicalGraphRepository::class)->findByIdentity($agent['project_id'], 'workspace_binding', $bindingId, 'hades_agent_artifact', $response->json('artifact.id'));
    $client = new class implements Neo4jClient
    {
        public array $commands = [];

        public function run(string $cypher, array $parameters = []): mixed
        {
            $this->commands[] = ['cypher' => $cypher, 'params' => $parameters];

            return str_contains($cypher, 'RETURN nodes') ? [['nodes' => 2, 'relationships' => 1]] : [];
        }
    };
    app(Neo4jCanonicalGraphProjector::class)->project($graph, $projection, $client);
    $node = collect($client->commands)->first(fn ($command) => str_contains($command['cypher'], 'UNWIND $nodes'));
    $edge = collect($client->commands)->first(fn ($command) => str_contains($command['cypher'], 'UNWIND $relationships'));
    expect($node['cypher'])->toContain('n.graph_version = $graph_version')->toContain('n.external_id = node.id')
        ->and($edge['cypher'])->toContain('r.graph_version = $graph_version')->toContain('r.external_id = relationship.id');
});

it('does not clear an existing current candidate before verification on retry', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->first();
    $graph = app(CanonicalGraphRepository::class)->findByIdentity($agent['project_id'], 'workspace_binding', $bindingId, 'hades_agent_artifact', $response->json('artifact.id'));
    $client = new class implements Neo4jClient
    {
        public array $commands = [];

        public bool $current = true;

        public function run(string $cypher, array $parameters = []): mixed
        {
            $this->commands[] = ['cypher' => $cypher, 'params' => $parameters];
            if (str_contains($cypher, 'v.current = false') && ! str_contains($cypher, 'ON CREATE')) {
                $this->current = false;
            } if (str_contains($cypher, 'UNWIND $nodes')) {
                throw new RuntimeException('write failed');
            }

            return [];
        }
    };
    expect(fn () => app(Neo4jCanonicalGraphProjector::class)->project($graph, $projection, $client))->toThrow(RuntimeException::class);
    $candidate = collect($client->commands)->first(fn ($command) => str_contains($command['cypher'], 'MERGE (v:CanonicalGraphVersion'));
    expect($candidate['cypher'])->toContain('ON CREATE SET v.current = false')->not->toContain('v.current = false,')
        ->and($client->current)->toBeTrue();
});

it('job projects the exact older artifact identity and marks it ready', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $older = canonicalProjectionUpload($agent, $bindingId);
    $first = $this->postJson('/api/hades/v1/artifacts', $older, ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $olderProjection = DB::table('canonical_graph_projections')->where('artifact_id', $first->json('artifact.id'))->first();
    $newer = canonicalProjectionUpload($agent, $bindingId);
    $newer['artifact']['nodes'][0]['id'] = 'file:newer.php';
    $newer['artifact']['relationships'][0]['source_id'] = 'file:newer.php';
    $this->postJson('/api/hades/v1/artifacts', $newer, ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $client = new FakeNeo4jClient;

    runCanonicalProjectionJob($olderProjection->id, $client);

    $node = collect($client->commands)->first(fn ($command) => str_contains($command['cypher'], 'UNWIND $nodes'));
    expect(collect($node['params']['nodes'])->pluck('id')->all())->toContain('file:a.php')->not->toContain('file:newer.php');
    expect(DB::table('canonical_graph_projections')->where('id', $olderProjection->id)->value('status'))->toBe('ready');
});

it('job rejects checksum changes, returns to queued, and rethrows for retry', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->where('artifact_id', $response->json('artifact.id'))->first();
    DB::table('canonical_graph_projections')->where('id', $projection->id)->update(['checksum' => str_repeat('0', 64)]);

    expect(fn () => runCanonicalProjectionJob($projection->id, new FakeNeo4jClient))->toThrow(RuntimeException::class, 'artifact_changed');
    expect(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('queued')
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('error_code'))->toBe('artifact_changed');
});

it('job keeps an exact missing artifact queued with a bounded code and rethrows for retry', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->where('artifact_id', $response->json('artifact.id'))->first();
    DB::table('hades_agent_artifacts')->where('id', $projection->artifact_id)->delete();

    expect(fn () => runCanonicalProjectionJob($projection->id, new FakeNeo4jClient))
        ->toThrow(RuntimeException::class, 'artifact_missing');
    expect(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('queued')
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('error_code'))->toBe('artifact_missing');
});

it('job marks projecting before client work then queues a bounded retry and rethrows', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->where('artifact_id', $response->json('artifact.id'))->first();
    $client = new class($projection->id) implements Neo4jClient
    {
        public function __construct(private string $projectionId) {}

        public function run(string $cypher, array $parameters = []): mixed
        {
            expect(DB::table('canonical_graph_projections')->where('id', $this->projectionId)->value('status'))->toBe('projecting');
            throw new RuntimeException('Neo.ClientError.Statement.SyntaxError');
        }
    };
    expect(fn () => runCanonicalProjectionJob($projection->id, $client))->toThrow(RuntimeException::class);
    expect(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('queued')
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('error_code'))->toBe('neo4j_query_failed');
});

it('job classifies transport exception chains separately from query errors', function (Throwable $failure, string $expected) {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->where('artifact_id', $response->json('artifact.id'))->first();
    $client = new class($failure) implements Neo4jClient
    {
        public function __construct(private Throwable $failure) {}

        public function run(string $cypher, array $parameters = []): mixed
        {
            throw $this->failure;
        }
    };

    expect(fn () => runCanonicalProjectionJob($projection->id, $client))->toThrow($failure::class);
    expect(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('queued')
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('error_code'))->toBe($expected);
})->with([
    'refused chain' => [new RuntimeException('projection wrapper', 0, new RuntimeException('Connection refused')), 'neo4j_unavailable'],
    'service unavailable' => [new RuntimeException('ServiceUnavailable'), 'neo4j_unavailable'],
    'timeout' => [new RuntimeException('Operation timed out'), 'neo4j_unavailable'],
    'query' => [new RuntimeException('Neo.ClientError.Statement.SyntaxError'), 'neo4j_query_failed'],
]);

it('keeps an intermediate failure queued for the original retry without reconcile dispatch', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->where('artifact_id', $response->json('artifact.id'))->first();
    $failure = new RuntimeException('Connection refused to private-host.example');
    $client = new class($failure) implements Neo4jClient
    {
        public function __construct(private Throwable $failure) {}

        public function run(string $cypher, array $parameters = []): mixed
        {
            throw $this->failure;
        }
    };
    $job = new ProjectCanonicalGraphToNeo4j($projection->id);

    expect(fn () => runCanonicalProjectionJobAttempt($job, $client))->toThrow(RuntimeException::class);
    expect(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('queued')
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('error_code'))->toBe('neo4j_unavailable');

    Bus::fake();
    $summary = app(Neo4jRebuildService::class)->reconcile(['project_id' => $agent['project_id']]);
    expect($summary['queued'])->toBe(0)
        ->and($summary['skipped'])->toBe(1);
    Bus::assertNotDispatched(ProjectCanonicalGraphToNeo4j::class);

    runCanonicalProjectionJobAttempt($job, new FakeNeo4jClient);
    expect(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('ready');
});

it('marks only final exhaustion failed and reconcile dispatches exactly one replacement', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->where('artifact_id', $response->json('artifact.id'))->first();
    $failure = new RuntimeException('projection wrapper', 0, new RuntimeException('Connection refused to private-host.example'));
    $client = new class($failure) implements Neo4jClient
    {
        public function __construct(private Throwable $failure) {}

        public function run(string $cypher, array $parameters = []): mixed
        {
            throw $this->failure;
        }
    };
    $job = new ProjectCanonicalGraphToNeo4j($projection->id);

    expect(fn () => runCanonicalProjectionJobAttempt($job, $client))->toThrow(RuntimeException::class);
    $job->failed($failure);
    expect(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('failed')
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('error_code'))->toBe('neo4j_unavailable')
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('error_code'))->not->toContain('private-host');

    Bus::fake();
    $first = app(Neo4jRebuildService::class)->reconcile(['project_id' => $agent['project_id']]);
    $second = app(Neo4jRebuildService::class)->reconcile(['project_id' => $agent['project_id']]);
    expect($first['queued'])->toBe(1)
        ->and($second['queued'])->toBe(0)
        ->and($second['skipped'])->toBe(1);
    Bus::assertDispatchedTimes(ProjectCanonicalGraphToNeo4j::class, 1);
});

it('does not call Neo4j for duplicate jobs after a projection is nonclaimable', function (string $status) {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->where('artifact_id', $response->json('artifact.id'))->first();
    DB::table('canonical_graph_projections')->where('id', $projection->id)->update(['status' => $status]);
    $client = new class implements Neo4jClient
    {
        public int $calls = 0;

        public function run(string $cypher, array $parameters = []): mixed
        {
            $this->calls++;

            throw new RuntimeException('Neo4j must not be called.');
        }
    };

    runCanonicalProjectionJob($projection->id, $client);

    expect($client->calls)->toBe(0)
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe($status);
})->with(['ready', 'stale', 'projecting', 'failed']);

it('allows only one interleaved job to claim a queued projection', function () {
    Bus::fake();
    [$agent, $bindingId] = canonicalProjectionAgent();
    $response = $this->postJson('/api/hades/v1/artifacts', canonicalProjectionUpload($agent, $bindingId), ['Authorization' => 'Bearer '.$agent['token']])->assertCreated();
    $projection = DB::table('canonical_graph_projections')->where('artifact_id', $response->json('artifact.id'))->first();
    $service = app(CanonicalGraphProjectionService::class);

    $firstClaim = $service->claimForWorker($projection->id);
    $client = new class implements Neo4jClient
    {
        public int $calls = 0;

        public function run(string $cypher, array $parameters = []): mixed
        {
            $this->calls++;

            return [];
        }
    };
    runCanonicalProjectionJob($projection->id, $client);

    expect($firstClaim)->not->toBeNull()
        ->and($client->calls)->toBe(0)
        ->and(DB::table('canonical_graph_projections')->where('id', $projection->id)->value('status'))->toBe('projecting');
});

function canonicalProjectionUpload(array $agent, string $bindingId): array
{
    return ['project_id' => $agent['project_id'], 'workspace_binding_id' => $bindingId, 'schema' => 'hades.code_graph.v1', 'artifact' => [
        'schema' => 'hades.code_graph.v1', 'language' => 'php',
        'graph_contract' => ['version' => 'hades.graph_artifact.v1', 'extractor' => ['name' => 'test', 'version' => '1', 'mode' => 'full', 'quality' => 'complete'], 'coverage' => ['languages' => ['php'], 'files_total' => 1, 'files_analyzed' => 1, 'files_failed' => 0], 'source' => ['branch' => 'main', 'head_commit' => str_repeat('a', 40)]],
        'nodes' => [['id' => 'file:a.php', 'kind' => 'file'], ['id' => 'class:A', 'kind' => 'class']],
        'relationships' => [['id' => 'declares:a', 'type' => 'DECLARES', 'source_id' => 'file:a.php', 'target_id' => 'class:A']],
    ]];
}

function canonicalProjectionAgent(): array
{
    $owner = User::factory()->create(['status' => 'active']);
    $projectId = (string) Str::ulid();
    $now = now();
    DB::table('projects')->insert(['id' => $projectId, 'name' => 'Canonical graph', 'slug' => 'canonical-'.Str::lower(Str::random(8)), 'description' => null, 'status' => 'active', 'default_code_exposure_policy' => 'full_code_artifacts', 'created_by_user_id' => $owner->id, 'created_at' => $now, 'updated_at' => $now]);
    $bootstrapId = (string) Str::ulid();
    $secret = 'canonical-secret';
    $prefix = 'hades_bootstrap_'.$bootstrapId;
    DB::table('hades_bootstrap_tokens')->insert(['id' => $bootstrapId, 'project_id' => $projectId, 'token_prefix' => $prefix, 'token_hash' => hash('sha256', $secret), 'name' => 'Canonical', 'scopes' => json_encode(['hades.bootstrap']), 'allowed_capabilities' => json_encode(['populate_backend_ast']), 'expires_at' => now()->addDay(), 'revoked_at' => null, 'last_used_at' => null, 'created_at' => $now, 'updated_at' => $now]);
    $registered = test()->postJson('/api/hades/v1/agents/register', ['project_id' => $projectId, 'agent_id' => 'canonical-agent', 'label' => 'Canonical', 'platform' => 'linux-x64', 'version' => '1', 'capabilities' => ['populate_backend_ast']], ['Authorization' => 'Bearer '.$prefix.'|'.$secret])->assertOk();
    $agent = ['project_id' => $projectId, 'external_id' => 'canonical-agent', 'token' => $registered->json('agent_token')];
    $bound = test()->postJson('/api/hades/v1/workspaces/bind', ['project_id' => $projectId, 'agent_id' => $agent['external_id'], 'workspace_fingerprint' => 'wf_'.Str::random(8), 'display_path' => '/tmp/canonical', 'git_remote_display' => 'example/repo', 'git_remote_hash' => hash('sha256', 'example/repo'), 'head_commit' => str_repeat('a', 40)], ['Authorization' => 'Bearer '.$agent['token']])->assertOk();

    return [$agent, $bound->json('workspace_binding_id')];
}

function runCanonicalProjectionJob(string $projectionId, Neo4jClient $client): void
{
    runCanonicalProjectionJobAttempt(new ProjectCanonicalGraphToNeo4j($projectionId), $client);
}

function runCanonicalProjectionJobAttempt(ProjectCanonicalGraphToNeo4j $job, Neo4jClient $client): void
{
    $factory = Mockery::mock(Neo4jClientFactory::class);
    $factory->shouldReceive('client')->zeroOrMoreTimes()->andReturn($client);
    $job->handle(app(CanonicalGraphRepository::class), app(CanonicalGraphProjectionService::class), app(Neo4jCanonicalGraphProjector::class), $factory);
}
