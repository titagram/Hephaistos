<?php

namespace App\Http\Controllers\Hades;

use App\Http\Controllers\Controller;
use App\Services\Graph\CanonicalGraphQueryService;
use App\Services\Hades\HadesProjectAwareness;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Validation\Rule;
use Symfony\Component\HttpFoundation\Response;

class GraphTraversalController extends Controller
{
    private const DIRECTIONS = ['any', 'out', 'in'];

    public function __construct(
        private readonly HadesProjectAwareness $awareness,
        private readonly CanonicalGraphQueryService $queryService,
    ) {}

    public function __invoke(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'project_id' => ['required', 'string'],
            'workspace_binding_id' => ['required', 'string'],
            'start' => ['required', 'string', 'max:1000'],
            'direction' => ['nullable', 'string', Rule::in(self::DIRECTIONS)],
            'max_depth' => ['nullable', 'integer', 'min:1', 'max:3'],
            'limit' => ['nullable', 'integer', 'min:1', 'max:50'],
        ]);

        $auth = $request->attributes->get('hades_auth');
        $agent = $auth['agent'];
        $binding = $this->linkedBinding($agent, $validated['project_id'], $validated['workspace_binding_id']);

        if ($binding instanceof JsonResponse) {
            return $binding;
        }

        $direction = (string) ($validated['direction'] ?? 'any');
        $result = $this->queryService->query(
            (string) $validated['project_id'],
            'workspace_binding',
            (string) $binding->id,
            'traverse',
            ['start' => (string) $validated['start'], 'direction' => $direction, 'limit' => (int) ($validated['limit'] ?? 20), 'max_depth' => min(3, (int) ($validated['max_depth'] ?? 2))],
        );
        if (! $result['found']) {
            return $this->error((string) $result['reason'], 'No canonical Hades graph projection is available for this workspace.', Response::HTTP_NOT_FOUND);
        }
        $metadata = $result['metadata'];

        $traversalVersion = 'hades-traverse-'.hash('sha256', implode('|', [
            (string) $metadata['graph_version'], (string) $validated['start'], $direction,
            (string) ($validated['max_depth'] ?? 2), (string) ($validated['limit'] ?? 20),
        ]));
        $matchFields = is_array($result['traversal_match_fields'] ?? null) ? $result['traversal_match_fields'] : ['id'];
        foreach ($result['results'] as $node) {
            $properties = is_array($node['properties'] ?? null) ? $node['properties'] : [];
            foreach (['name', 'label', 'path', 'kind'] as $field) {
                if (isset($properties[$field]) && str_contains(strtolower((string) $properties[$field]), strtolower((string) $validated['start']))) {
                    $matchFields[] = $field;
                }
            }
        }
        if ($matchFields === ['id'] && $result['edges'] !== []) {
            $matchFields[] = 'edge';
        }
        $truncated = count($result['results']) >= (int) ($validated['limit'] ?? 20);

        return response()->json([
            'protocol_version' => 'v1',
            'project_id' => $validated['project_id'],
            'workspace_binding_id' => $binding->id,
            'version' => $traversalVersion,
            'etag' => $traversalVersion,
            'projection_id' => $metadata['projection_id'],
            'artifact_id' => $metadata['artifact_id'],
            'schema' => $metadata['schema'] ?? $metadata['artifact_type'],
            'graph_version' => $metadata['graph_version'],
            'head_commit' => $metadata['head_commit'],
            'start' => (string) $validated['start'],
            'direction' => (string) ($validated['direction'] ?? 'any'),
            'max_depth' => (int) ($validated['max_depth'] ?? 2),
            'limit' => (int) ($validated['limit'] ?? 20),
            'quality' => $result['quality'],
            'count' => count($result['results']),
            'edge_count' => count($result['edges']),
            'truncated' => $truncated,
            'match_fields' => array_values(array_unique($matchFields)),
            'freshness' => $this->awareness->freshnessForBinding($binding),
            'provenance' => [
                'projection_id' => $metadata['projection_id'],
                'artifact_id' => $metadata['artifact_id'],
                'graph_version' => $metadata['graph_version'],
                'schema' => $metadata['schema'] ?? $metadata['artifact_type'],
            ],
            'nodes' => $result['results'],
            'edges' => $result['edges'],
            'server_time' => now()->toISOString(),
        ]);
    }

    private function linkedBinding(object $agent, string $projectId, string $bindingId): mixed
    {
        if ($agent->project_id !== $projectId) {
            return $this->error('project_mismatch', 'Hades agent token is scoped to a different project.', Response::HTTP_FORBIDDEN);
        }

        $binding = DB::table('hades_workspace_bindings')
            ->where('id', $bindingId)
            ->where('project_id', $projectId)
            ->where('hades_agent_id', $agent->id)
            ->first();

        if (! $binding) {
            return $this->error('workspace_binding_not_found', 'Workspace binding was not found.', Response::HTTP_NOT_FOUND);
        }

        if ($binding->status !== 'linked') {
            return $this->error('workspace_binding_unlinked', 'Workspace binding is not linked.', Response::HTTP_CONFLICT);
        }

        return $binding;
    }

    private function error(string $code, string $message, int $status): JsonResponse
    {
        return response()->json(['error' => ['code' => $code, 'message' => $message]], $status);
    }
}
