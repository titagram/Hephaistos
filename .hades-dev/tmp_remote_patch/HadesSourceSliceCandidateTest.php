<?php

use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

uses(RefreshDatabase::class);

it('creates deduped read source slice jobs from artifact candidates', function () {
    [$projectId, $agent, $token, $workspaceBindingId] = hadesSourceCandidateAgent();

    $candidate = [
        'candidate_key' => hash('sha256', 'abc123|app/Http/Controllers/BookingController.php|1|25|BookingController|laravel_controller'),
        'path' => 'app/Http/Controllers/BookingController.php',
        'start_line' => 1,
        'end_line' => 25,
        'symbol' => 'BookingController',
        'reason' => 'laravel_controller',
        'priority' => 10,
        'head_commit' => str_repeat('a', 40),
        'raw_source_included' => false,
        'retention_class' => 'source_slice_candidate',
    ];

    $payload = [
        'project_id' => $projectId,
        'workspace_binding_id' => $workspaceBindingId,
        'schema' => 'hades.php_graph.v1',
        'artifact' => ['schema' => 'hades.php_graph.v1', 'source_slice_candidates' => [$candidate]],
        'sha256' => hash('sha256', json_encode(['source_slice_candidates' => [$candidate], 'routes' => []], JSON_THROW_ON_ERROR)),
    ];

    $this
        ->postJson('/api/hades/v1/artifacts', $payload, hadesSourceCandidateHeaders($token))
        ->assertCreated();

    $this
        ->postJson('/api/hades/v1/artifacts', $payload, hadesSourceCandidateHeaders($token))
        ->assertOk();

    expect(DB::table('hades_source_slice_candidates')->where('workspace_binding_id', $workspaceBindingId)->count())->toBe(1);
    expect(DB::table('hades_agent_jobs')->where('workspace_binding_id', $workspaceBindingId)->where('capability', 'read_source_slice')->count())->toBe(1);

    $job = DB::table('hades_agent_jobs')->where('workspace_binding_id', $workspaceBindingId)->where('capability', 'read_source_slice')->first();
    $jobPayload = json_decode($job->payload, true, 512, JSON_THROW_ON_ERROR);
    expect($jobPayload['path'])->toBe('app/Http/Controllers/BookingController.php');
    expect($jobPayload['start_line'])->toBe(1);
    expect($jobPayload['end_line'])->toBe(25);
    expect($jobPayload['symbol'])->toBe('BookingController');
    expect($jobPayload['candidate_key'])->toBe($candidate['candidate_key']);
    expect($job->policy)->toBe('confirm');
    expect((bool) $job->requires_confirmation)->toBeTrue();

    $this
        ->postJson('/api/hades/v1/source-slices', [
            'project_id' => $projectId,
            'workspace_binding_id' => $workspaceBindingId,
            'job_id' => $job->id,
            'path' => 'app/Http/Controllers/BookingController.php',
            'start_line' => 1,
            'end_line' => 25,
            'language' => 'php',
            'symbol' => 'BookingController',
            'head_commit' => str_repeat('a', 40),
            'content_redacted' => "<?php\nclass BookingController {}\n",
            'redactions' => 0,
            'candidate_key' => $candidate['candidate_key'],
        ], hadesSourceCandidateHeaders($token))
        ->assertCreated();

    $candidateRow = DB::table('hades_source_slice_candidates')
        ->where('workspace_binding_id', $workspaceBindingId)
        ->where('candidate_key', $candidate['candidate_key'])
        ->first();
    expect($candidateRow->status)->toBe('slice_uploaded');
    expect($candidateRow->source_slice_id)->toBeString();
});

/**
 * @return array{0: string, 1: array<string, string>, 2: string, 3: string}
 */
function hadesSourceCandidateAgent(): array
{
    $projectId = hadesSourceCandidateProjectId();
    $bootstrapToken = hadesSourceCandidateBootstrapToken($projectId);
    $externalAgentId = 'local-agent-'.Str::lower(Str::random(8));

    $registered = test()->postJson('/api/hades/v1/agents/register', [
        'project_id' => $projectId,
        'agent_id' => $externalAgentId,
        'label' => 'Hades Source Candidate Agent',
        'platform' => 'linux-x64',
        'version' => '0.3.0',
        'capabilities' => ['read_files', 'read_source_slice', 'sync_git_tree', 'populate_backend_ast'],
    ], hadesSourceCandidateHeaders($bootstrapToken))->assertOk();

    $agent = [
        'project_id' => $projectId,
        'external_agent_id' => $externalAgentId,
        'backend_agent_id' => $registered->json('backend_agent_id'),
        'agent_token' => $registered->json('agent_token'),
    ];

    $bound = test()->postJson('/api/hades/v1/workspaces/bind', [
        'project_id' => $projectId,
        'agent_id' => $externalAgentId,
        'workspace_fingerprint' => 'wf_hades_source_candidate_'.Str::lower(Str::random(8)),
        'display_path' => '~/Code/hades-source-candidate',
        'git_remote_display' => 'github.com/acme/hades-source-candidate.git',
        'git_remote_hash' => hash('sha256', 'git@github.com:acme/hades-source-candidate.git'),
        'head_commit' => str_repeat('a', 40),
    ], hadesSourceCandidateHeaders($agent['agent_token']))->assertOk();

    return [$projectId, $agent, $agent['agent_token'], $bound->json('workspace_binding_id')];
}

function hadesSourceCandidateProjectId(): string
{
    $user = User::factory()->create(['status' => 'active']);
    $id = (string) Str::ulid();
    $now = now();

    DB::table('projects')->insert([
        'id' => $id,
        'name' => 'Hades Source Candidate Test Project',
        'slug' => 'hades-source-candidate-test-'.Str::lower(Str::random(8)),
        'description' => null,
        'status' => 'active',
        'default_code_exposure_policy' => 'full_code_artifacts',
        'created_by_user_id' => $user->id,
        'created_at' => $now,
        'updated_at' => $now,
    ]);

    return $id;
}

function hadesSourceCandidateBootstrapToken(string $projectId): string
{
    $id = (string) Str::ulid();
    $secret = 'hades-source-candidate-bootstrap-test-secret';
    $prefix = 'hades_bootstrap_'.$id;
    $now = now();

    DB::table('hades_bootstrap_tokens')->insert([
        'id' => $id,
        'project_id' => $projectId,
        'token_prefix' => $prefix,
        'token_hash' => hash('sha256', $secret),
        'name' => 'Hades Source Candidate Bootstrap Token',
        'scopes' => json_encode(['hades.bootstrap'], JSON_THROW_ON_ERROR),
        'allowed_capabilities' => json_encode(['read_files', 'read_source_slice', 'sync_git_tree', 'populate_backend_ast'], JSON_THROW_ON_ERROR),
        'expires_at' => now()->addMonth(),
        'revoked_at' => null,
        'last_used_at' => null,
        'created_at' => $now,
        'updated_at' => $now,
    ]);

    return $prefix.'|'.$secret;
}

function hadesSourceCandidateHeaders(?string $token = null): array
{
    return $token === null ? [] : ['Authorization' => 'Bearer '.$token];
}
