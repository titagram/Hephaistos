<?php

use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

uses(RefreshDatabase::class);

it('supports precise source-free diagnosis from current evidence graph and source slices', function () {
    $agent = hadesNoCodebaseRegisteredAgent();
    $binding = hadesNoCodebaseWorkspaceBinding($agent);
    $headers = hadesNoCodebaseHeaders($agent['agent_token']);
    $projectId = $agent['project_id'];
    $bindingId = $binding['workspace_binding_id'];
    $head = str_repeat('f', 40);

    $artifactId = $this->postJson('/api/hades/v1/artifacts', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'schema' => 'hades.php_graph.v1',
        'artifact' => hadesNoCodebaseGraphArtifact($head),
        'truncated' => false,
        'redactions' => 0,
    ], $headers)
        ->assertCreated()
        ->json('artifact.id');

    $bugReportId = $this->postJson('/api/hades/v1/bug-reports', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'title' => 'Order detail returns 500 without local source access',
        'symptom' => 'GET /orders/123 returns 500 when the customer relation is missing.',
        'severity' => 'high',
        'environment' => ['deploy_commit' => $head, 'runtime' => 'php-fpm'],
        'affected_refs' => [['type' => 'route', 'id' => 'orders.show']],
    ], $headers)
        ->assertCreated()
        ->json('bug_report.id');

    $evidenceId = $this->postJson('/api/hades/v1/bug-evidence', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'kind' => 'stack_trace',
        'summary' => 'Null customer relation in OrderController@show at line 43.',
        'payload' => [
            'schema' => 'hades.stack_trace.v1',
            'frames' => [
                ['path' => 'app/Http/Controllers/OrderController.php', 'line' => 43, 'symbol' => 'OrderController@show'],
            ],
        ],
        'source' => 'laravel.log',
        'redactions' => 0,
        'retention_class' => 'stack_trace',
    ], $headers)
        ->assertCreated()
        ->json('evidence.id');

    $sourceSliceId = $this->postJson('/api/hades/v1/source-slices', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'path' => 'app/Http/Controllers/OrderController.php',
        'start_line' => 41,
        'end_line' => 44,
        'language' => 'php',
        'symbol' => 'App\\Http\\Controllers\\OrderController@show',
        'head_commit' => $head,
        'content_redacted' => "41: public function show(int \$id)\n42: \$order = \$this->orders->findVisible(\$id);\n43: return \$order->customer->active();\n44: }",
        'redactions' => 0,
        'truncated' => false,
        'retention_class' => 'source_slice',
        'policy' => 'manual_review',
    ], $headers)
        ->assertCreated()
        ->json('source_slice.id');

    $evidencePackId = $this->postJson('/api/hades/v1/evidence-packs', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'title' => 'Order detail source-free diagnosis pack',
        'summary' => 'Stack trace, graph path, and source slice all point to a missing customer guard.',
        'evidence_refs' => [['type' => 'bug_evidence', 'id' => $evidenceId]],
        'graph_refs' => [
            ['type' => 'artifact', 'id' => $artifactId, 'start' => 'route:orders.show'],
            ['type' => 'edge', 'from' => 'route:orders.show', 'to' => 'App\Http\Controllers\OrderController@show'],
        ],
        'source_slice_ids' => [$sourceSliceId],
        'payload' => [
            'reproduction_steps' => ['Open booking form', 'Submit order without a customer relation'],
            'expected_behavior' => 'The request returns a validation error.',
            'actual_behavior' => 'The controller dereferences the nullable customer relation.',
            'runtime_context' => ['php' => '8.3', 'queue' => 'sync'],
            'deploy_context' => ['commit' => $head],
            'minimal_input' => ['route' => 'orders.show', 'id' => 123],
            'last_changed_refs' => ['symbol:App\\Http\\Controllers\\OrderController@show'],
            'missing_evidence' => [],
            'next_verification' => 'Run OrderControllerTest::test_show_missing_customer',
        ],
        'redactions' => 0,
        'retention_class' => 'diagnosis_evidence',
        'head_commit' => $head,
    ], $headers)
        ->assertCreated()
        ->assertJsonPath('evidence_pack.payload.reproduction_steps.0', 'Open booking form')
        ->assertJsonPath('evidence_pack.payload.deploy_context.commit', $head)
        ->assertJsonPath('evidence_pack.source_slice_ids.0', $sourceSliceId)
        ->json('evidence_pack.id');

    $this->postJson('/api/hades/v1/evidence-packs', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'title' => 'Unsafe evidence pack',
        'summary' => 'This pack should be rejected by evidence policy.',
        'source_slice_ids' => [$sourceSliceId],
        'payload' => [
            'runtime_context' => ['env' => 'OPENAI_API_KEY=sk-live-secret123'],
        ],
    ], $headers)
        ->assertStatus(422)
        ->assertJsonPath('error.code', 'unredacted_secret_detected');

    expect(DB::table('hades_search_documents')->where('domain', 'bug_evidence')->where('source_table', 'hades_bug_evidence_items')->where('source_id', $evidenceId)->where('body', 'like', '%OrderController@show%')->exists())->toBeTrue()
        ->and(DB::table('hades_search_documents')->where('domain', 'source_slices')->where('source_table', 'hades_source_slices')->where('source_id', $sourceSliceId)->where('body', 'like', '%customer->active%')->exists())->toBeTrue()
        ->and(DB::table('hades_search_documents')->where('domain', 'evidence_packs')->where('source_table', 'hades_evidence_packs')->where('source_id', $evidencePackId)->where('body', 'like', '%missing customer guard%')->exists())->toBeTrue();

    $awareness = $this->getJson('/api/hades/v1/project-awareness/status?'.http_build_query([
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
    ]), $headers)
        ->assertOk()
        ->assertJsonPath('freshness.status', 'current')
        ->assertJsonPath('coverage.code_graph.status', 'current')
        ->assertJsonPath('coverage.bug_evidence.status', 'current')
        ->assertJsonPath('coverage.source_slices.status', 'current')
        ->assertJsonPath('coverage.evidence_packs.status', 'current')
        ->assertJsonPath('diagnosable_without_source', true)
        ->json();

    $graph = $this->getJson('/api/hades/v1/graph/traverse?'.http_build_query([
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'start' => 'orders.show',
        'direction' => 'any',
        'max_depth' => 2,
        'limit' => 10,
    ]), $headers)
        ->assertOk()
        ->assertJsonPath('freshness.status', 'current')
        ->json();

    expect(json_encode($graph['nodes'], JSON_THROW_ON_ERROR))->toContain('OrderController')
        ->and(json_encode($graph['edges'], JSON_THROW_ON_ERROR))->toContain('handles');

    $this->getJson('/api/hades/v1/source-slices?'.http_build_query([
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'id' => $sourceSliceId,
    ]), $headers)
        ->assertOk()
        ->assertJsonPath('freshness.status', 'current')
        ->assertJsonPath('items.0.id', $sourceSliceId)
        ->assertJsonPath('items.0.content_redacted', "41: public function show(int \$id)\n42: \$order = \$this->orders->findVisible(\$id);\n43: return \$order->customer->active();\n44: }");

    $this->getJson('/api/hades/v1/evidence-packs?'.http_build_query([
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'id' => $evidencePackId,
    ]), $headers)
        ->assertOk()
        ->assertJsonPath('items.0.id', $evidencePackId)
        ->assertJsonPath('items.0.source_slice_ids.0', $sourceSliceId);

    $diagnosisId = $this->postJson('/api/hades/v1/diagnosis-reports', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'status' => 'final',
        'confidence' => 'high',
        'root_cause' => 'OrderController@show dereferences the nullable customer relation before checking it exists.',
        'mechanism' => 'The stored graph connects route:orders.show to OrderController@show and OrderService::findVisible; the stack trace and source slice show the failing customer->active call.',
        'evidence_refs' => [
            ['type' => 'bug_evidence', 'id' => $evidenceId],
            ['type' => 'source_slice', 'id' => $sourceSliceId],
            ['type' => 'evidence_pack', 'id' => $evidencePackId],
        ],
        'freshness' => $awareness['freshness'],
        'root_cause_id' => 'rc.order.customer_relation_nullable',
        'bug_class' => 'missing_null_guard',
        'affected_refs' => ['route:orders.show', 'symbol:App\\Http\\Controllers\\OrderController@show'],
        'payload' => [
            'affected_symbols' => ['App\\Http\\Controllers\\OrderController@show'],
            'next_verification' => 'Run the order detail missing-customer regression test.',
        ],
        'redactions' => 0,
    ], $headers)
        ->assertCreated()
        ->assertJsonPath('diagnosis_report.confidence', 'high')
        ->assertJsonPath('diagnosis_report.status', 'final')
        ->assertJsonPath('diagnosis_report.payload.root_cause_id', 'rc.order.customer_relation_nullable')
        ->assertJsonPath('diagnosis_report.payload.bug_class', 'missing_null_guard')
        ->json('diagnosis_report.id');

    $memoryId = $this->postJson('/api/hades/v1/diagnosis-reports/'.$diagnosisId.'/promote', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'verification_status' => 'manual_review',
    ], $headers)
        ->assertCreated()
        ->assertJsonPath('resolved_bug_memory.payload.root_cause_id', 'rc.order.customer_relation_nullable')
        ->assertJsonPath('resolved_bug_memory.payload.bug_class', 'missing_null_guard')
        ->assertJsonPath('resolved_bug_memory.payload.affected_refs.0', 'route:orders.show')
        ->json('resolved_bug_memory.id');

    $this->getJson('/api/hades/v1/memory/search?'.http_build_query([
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'query' => 'rc.order.customer_relation_nullable',
        'domain' => 'project_memory',
        'kind' => 'resolved_bug',
        'schema' => 'hades.resolved_bug.v1',
    ]), $headers)
        ->assertOk()
        ->assertJsonPath('items.0.id', $memoryId)
        ->assertJsonPath('items.0.kind', 'resolved_bug');
});

it('rejects precise source-free diagnosis when coverage is insufficient', function () {
    $agent = hadesNoCodebaseRegisteredAgent();
    $binding = hadesNoCodebaseWorkspaceBinding($agent);
    $headers = hadesNoCodebaseHeaders($agent['agent_token']);
    $projectId = $agent['project_id'];
    $bindingId = $binding['workspace_binding_id'];
    $head = str_repeat('f', 40);

    $this->postJson('/api/hades/v1/artifacts', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'schema' => 'hades.php_graph.v1',
        'artifact' => hadesNoCodebaseGraphArtifact($head),
        'truncated' => false,
        'redactions' => 0,
    ], $headers)->assertCreated();

    $bugReportId = $this->postJson('/api/hades/v1/bug-reports', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'title' => 'Order detail has stack trace but no source slice',
        'symptom' => 'GET /orders/123 returns 500.',
    ], $headers)->assertCreated()->json('bug_report.id');

    $evidenceId = $this->postJson('/api/hades/v1/bug-evidence', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'kind' => 'stack_trace',
        'summary' => 'OrderController@show fails, but no source slice was approved.',
        'payload' => ['frames' => [['path' => 'app/Http/Controllers/OrderController.php', 'line' => 43]]],
        'source' => 'laravel.log',
    ], $headers)->assertCreated()->json('evidence.id');

    $awareness = $this->getJson('/api/hades/v1/project-awareness/status?'.http_build_query([
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
    ]), $headers)
        ->assertOk()
        ->assertJsonPath('freshness.status', 'current')
        ->assertJsonPath('coverage.source_slices.status', 'missing')
        ->assertJsonPath('diagnosable_without_source', false)
        ->json();

    $this->postJson('/api/hades/v1/diagnosis-reports', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'status' => 'final',
        'confidence' => 'high',
        'root_cause' => 'OrderController@show likely dereferences customer without a guard.',
        'evidence_refs' => [['type' => 'bug_evidence', 'id' => $evidenceId]],
        'freshness' => $awareness['freshness'],
    ], $headers)
        ->assertStatus(422)
        ->assertJsonPath('error.code', 'diagnosis_awareness_not_diagnosable');

    $this->postJson('/api/hades/v1/diagnosis-reports', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'status' => 'final',
        'confidence' => 'insufficient',
        'root_cause' => 'Precise cause is not supported until a source slice or verified code fact is available.',
        'evidence_refs' => [['type' => 'bug_evidence', 'id' => $evidenceId]],
        'freshness' => $awareness['freshness'],
    ], $headers)
        ->assertCreated()
        ->assertJsonPath('diagnosis_report.confidence', 'insufficient');
});

it('rejects precise source-free diagnosis when graph and source slices are stale', function () {
    $agent = hadesNoCodebaseRegisteredAgent();
    $binding = hadesNoCodebaseWorkspaceBinding($agent);
    $headers = hadesNoCodebaseHeaders($agent['agent_token']);
    $projectId = $agent['project_id'];
    $bindingId = $binding['workspace_binding_id'];
    $workspaceHead = str_repeat('f', 40);
    $staleHead = str_repeat('e', 40);

    $this->postJson('/api/hades/v1/artifacts', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'schema' => 'hades.php_graph.v1',
        'artifact' => hadesNoCodebaseGraphArtifact($staleHead),
        'truncated' => false,
        'redactions' => 0,
    ], $headers)->assertCreated();

    $bugReportId = $this->postJson('/api/hades/v1/bug-reports', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'title' => 'Order detail graph is from an older checkout',
        'symptom' => 'GET /orders/123 still returns 500 on the current deployment.',
        'environment' => ['deploy_commit' => $workspaceHead],
    ], $headers)->assertCreated()->json('bug_report.id');

    $evidenceId = $this->postJson('/api/hades/v1/bug-evidence', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'kind' => 'stack_trace',
        'summary' => 'OrderController@show stack trace was captured from the current deploy.',
        'payload' => ['frames' => [['path' => 'app/Http/Controllers/OrderController.php', 'line' => 43]]],
        'source' => 'laravel.log',
    ], $headers)->assertCreated()->json('evidence.id');

    $sourceSliceId = $this->postJson('/api/hades/v1/source-slices', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'path' => 'app/Http/Controllers/OrderController.php',
        'start_line' => 41,
        'end_line' => 44,
        'language' => 'php',
        'symbol' => 'App\\Http\\Controllers\\OrderController@show',
        'head_commit' => $staleHead,
        'content_redacted' => "41: public function show(int \$id)\n42: \$order = \$this->orders->findVisible(\$id);\n43: return \$order->customer->active();\n44: }",
        'redactions' => 0,
        'truncated' => false,
        'retention_class' => 'source_slice',
        'policy' => 'manual_review',
    ], $headers)->assertCreated()->json('source_slice.id');

    $awareness = $this->getJson('/api/hades/v1/project-awareness/status?'.http_build_query([
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
    ]), $headers)
        ->assertOk()
        ->assertJsonPath('freshness.status', 'stale')
        ->assertJsonPath('freshness.workspace_head_commit', $workspaceHead)
        ->assertJsonPath('freshness.artifact_head_commit', $staleHead)
        ->assertJsonPath('freshness.stale_reason', 'artifact_head_mismatch')
        ->assertJsonPath('coverage.artifacts.status', 'stale')
        ->assertJsonPath('coverage.code_graph.status', 'stale')
        ->assertJsonPath('coverage.bug_evidence.status', 'current')
        ->assertJsonPath('coverage.source_slices.status', 'stale')
        ->assertJsonPath('coverage.source_slices.stale_reason', 'source_slice_head_mismatch')
        ->assertJsonPath('diagnosable_without_source', false)
        ->assertJsonPath('overall_status', 'stale')
        ->json();

    $this->postJson('/api/hades/v1/diagnosis-reports', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'status' => 'final',
        'confidence' => 'high',
        'root_cause' => 'OrderController@show dereferences customer without a guard.',
        'evidence_refs' => [
            ['type' => 'bug_evidence', 'id' => $evidenceId],
            ['type' => 'source_slice', 'id' => $sourceSliceId],
        ],
        'freshness' => $awareness['freshness'],
    ], $headers)
        ->assertStatus(422)
        ->assertJsonPath('error.code', 'diagnosis_freshness_not_current');

    $this->postJson('/api/hades/v1/diagnosis-reports', [
        'project_id' => $projectId,
        'workspace_binding_id' => $bindingId,
        'bug_report_id' => $bugReportId,
        'status' => 'final',
        'confidence' => 'insufficient',
        'root_cause' => 'Precise diagnosis is blocked until graph and source slices are refreshed to the workspace HEAD.',
        'evidence_refs' => [['type' => 'bug_evidence', 'id' => $evidenceId]],
        'freshness' => $awareness['freshness'],
    ], $headers)
        ->assertCreated()
        ->assertJsonPath('diagnosis_report.confidence', 'insufficient');
});

function hadesNoCodebaseHeaders(?string $token = null): array
{
    return $token === null ? [] : ['Authorization' => 'Bearer '.$token];
}

/**
 * @return array{id: string, user_id: int}
 */
function hadesNoCodebaseProject(): array
{
    $user = User::factory()->create(['status' => 'active']);
    $id = (string) Str::ulid();
    $now = now();

    DB::table('projects')->insert([
        'id' => $id,
        'name' => 'Hades No-Codebase Test Project',
        'slug' => 'hades-no-codebase-test-'.Str::lower(Str::random(8)),
        'description' => null,
        'status' => 'active',
        'default_code_exposure_policy' => 'selected_source_slices',
        'created_by_user_id' => $user->id,
        'created_at' => $now,
        'updated_at' => $now,
    ]);

    return ['id' => $id, 'user_id' => $user->id];
}

/**
 * @return array{id: string, plain_token: string, project_id: string}
 */
function hadesNoCodebaseBootstrapToken(?string $projectId = null): array
{
    $projectId = $projectId ?: hadesNoCodebaseProject()['id'];
    $id = (string) Str::ulid();
    $secret = 'hades-no-codebase-bootstrap-test-secret';
    $prefix = 'hades_bootstrap_'.$id;
    $now = now();

    DB::table('hades_bootstrap_tokens')->insert([
        'id' => $id,
        'project_id' => $projectId,
        'token_prefix' => $prefix,
        'token_hash' => hash('sha256', $secret),
        'name' => 'Hades No-Codebase Bootstrap Token',
        'scopes' => json_encode(['hades.bootstrap'], JSON_THROW_ON_ERROR),
        'allowed_capabilities' => json_encode(['read_files', 'read_source_slice', 'sync_git_tree', 'populate_backend_ast'], JSON_THROW_ON_ERROR),
        'expires_at' => now()->addMonth(),
        'revoked_at' => null,
        'last_used_at' => null,
        'created_at' => $now,
        'updated_at' => $now,
    ]);

    return ['id' => $id, 'plain_token' => $prefix.'|'.$secret, 'project_id' => $projectId];
}

/**
 * @return array{project_id: string, external_agent_id: string, backend_agent_id: string, agent_token: string}
 */
function hadesNoCodebaseRegisteredAgent(): array
{
    $bootstrap = hadesNoCodebaseBootstrapToken();
    $externalAgentId = 'local-agent-'.Str::lower(Str::random(8));
    $test = test();

    $registered = $test->postJson('/api/hades/v1/agents/register', [
        'project_id' => $bootstrap['project_id'],
        'agent_id' => $externalAgentId,
        'label' => 'Hades No-Codebase Agent',
        'platform' => 'linux-x64',
        'version' => '0.3.0',
        'capabilities' => ['read_files', 'read_source_slice', 'sync_git_tree', 'populate_backend_ast'],
    ], hadesNoCodebaseHeaders($bootstrap['plain_token']))->assertOk();

    return [
        'project_id' => $bootstrap['project_id'],
        'external_agent_id' => $externalAgentId,
        'backend_agent_id' => $registered->json('backend_agent_id'),
        'agent_token' => $registered->json('agent_token'),
    ];
}

/**
 * @param  array{project_id: string, external_agent_id: string, backend_agent_id: string, agent_token: string}  $agent
 * @return array{workspace_binding_id: string}
 */
function hadesNoCodebaseWorkspaceBinding(array $agent): array
{
    $test = test();
    $bound = $test->postJson('/api/hades/v1/workspaces/bind', [
        'project_id' => $agent['project_id'],
        'agent_id' => $agent['external_agent_id'],
        'workspace_fingerprint' => 'wf_hades_no_codebase_'.Str::lower(Str::random(8)),
        'display_path' => '~/Code/hades-no-codebase-fixture',
        'git_remote_display' => 'github.com/acme/hades-no-codebase.git',
        'git_remote_hash' => hash('sha256', 'git@github.com:acme/hades-no-codebase.git'),
        'head_commit' => str_repeat('f', 40),
    ], hadesNoCodebaseHeaders($agent['agent_token']))->assertOk();

    return ['workspace_binding_id' => $bound->json('workspace_binding_id')];
}

function hadesNoCodebaseGraphArtifact(string $head): array
{
    return [
        'schema' => 'hades.php_graph.v1',
        'head_commit' => $head,
        'raw_source_included' => false,
        'routes' => [
            [
                'method' => 'GET',
                'uri' => 'orders/{order}',
                'name' => 'orders.show',
                'handler' => 'App\\Http\\Controllers\\OrderController@show',
                'path' => 'routes/web.php',
            ],
        ],
        'symbols' => [
            [
                'name' => 'App\\Http\\Controllers\\OrderController@show',
                'kind' => 'method',
                'class' => 'App\\Http\\Controllers\\OrderController',
                'role' => 'controller',
                'path' => 'app/Http/Controllers/OrderController.php',
                'line' => 41,
            ],
            [
                'name' => 'App\\Services\\OrderService::findVisible',
                'kind' => 'method',
                'class' => 'App\\Services\\OrderService',
                'role' => 'service',
                'path' => 'app/Services/OrderService.php',
                'line' => 18,
            ],
            [
                'name' => 'table:orders',
                'kind' => 'table',
                'role' => 'database',
            ],
        ],
        'edges' => [
            ['kind' => 'handles', 'from' => 'route:orders.show', 'to' => 'App\\Http\\Controllers\\OrderController@show'],
            ['kind' => 'calls', 'from' => 'App\\Http\\Controllers\\OrderController@show', 'to' => 'App\\Services\\OrderService::findVisible'],
            ['kind' => 'reads_table', 'from' => 'App\\Services\\OrderService::findVisible', 'to' => 'table:orders'],
        ],
    ];
}
