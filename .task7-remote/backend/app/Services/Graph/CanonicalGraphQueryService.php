<?php

namespace App\Services\Graph;

use App\Services\Neo4j\Neo4jClient;
use App\Services\Neo4jClientFactory;
use Illuminate\Support\Facades\DB;
use InvalidArgumentException;
use RuntimeException;

class CanonicalGraphQueryService
{
    private const TYPES = ['callers', 'callees', 'path', 'traverse'];

    public function __construct(private readonly ?Neo4jClient $client = null) {}

    /** @param array<string, mixed> $params */
    public function query(string $projectId, string $scopeType, string $scopeId, string $type, array $params = []): array
    {
        if (! in_array($scopeType, ['repository', 'workspace_binding'], true)) {
            throw new InvalidArgumentException('Unsupported graph source scope: '.$scopeType);
        }
        if (! in_array($type, self::TYPES, true)) {
            throw new RuntimeException('Unsupported query type: '.$type);
        }
        $empty = $this->envelope($projectId, $scopeType, $scopeId);
        if ($projectId === '') {
            throw new InvalidArgumentException('A project is required.');
        }
        if ($scopeId === '') {
            return array_merge($empty, ['found' => false, 'reason' => 'graph_scope_not_found']);
        }

        $owned = $scopeType === 'repository'
            ? DB::table('repositories')->where('id', $scopeId)->where('project_id', $projectId)->exists()
            : DB::table('hades_workspace_bindings')->where('id', $scopeId)->where('project_id', $projectId)->where('status', 'linked')->exists();
        if (! $owned) {
            return array_merge($empty, ['found' => false, 'reason' => 'graph_scope_not_found']);
        }

        $projection = DB::table('canonical_graph_projections')
            ->where('project_id', $projectId)->where('source_scope_type', $scopeType)->where('source_scope_id', $scopeId)
            ->where('status', 'ready')->orderByDesc('projected_at')->orderByDesc('id')->first();
        if ($projection === null) {
            return array_merge($empty, ['found' => false, 'reason' => 'graph_projection_not_ready']);
        }

        $base = $this->envelope($projectId, $scopeType, $scopeId, $projection);
        try {
            $client = $this->client ?? app(Neo4jClientFactory::class)->client();
            $rows = match ($type) {
                'callers' => $client->run($this->adjacency('in'), $this->symbolParams($params, $projection)),
                'callees' => $client->run($this->adjacency('out'), $this->symbolParams($params, $projection)),
                'path' => $this->runPath($client, $params, $projection),
                'traverse' => $this->runTraverse($client, $params, $projection),
            };
        } catch (\Throwable) {
            return array_merge($base, ['found' => false, 'reason' => 'query_error']);
        }

        [$nodes, $edges] = $this->normaliseRows($rows);

        return array_merge($base, ['found' => true, 'reason' => null, 'results' => $nodes, 'edges' => $edges]);
    }

    private function adjacency(string $direction): string
    {
        $match = $direction === 'in'
            ? '(result:CanonicalGraphNode)-[edge:CALLS {graph_version: $graph_version}]->(node:CanonicalGraphNode {external_id: $external_id, graph_version: $graph_version})'
            : '(node:CanonicalGraphNode {external_id: $external_id, graph_version: $graph_version})-[edge:CALLS {graph_version: $graph_version}]->(result:CanonicalGraphNode {graph_version: $graph_version})';

        return "MATCH {$match} RETURN properties(result) AS node, labels(result) AS labels, properties(edge) AS edge LIMIT \$limit";
    }

    /** @param array<string, mixed> $params */
    private function symbolParams(array $params, object $projection): array
    {
        return ['external_id' => (string) ($params['symbol_id'] ?? ''), 'graph_version' => (string) $projection->graph_version, 'limit' => max(1, min(50, (int) ($params['limit'] ?? 50)))];
    }

    /** @param array<string, mixed> $params */
    private function runPath(Neo4jClient $client, array $params, object $projection): mixed
    {
        $depth = max(1, min(10, (int) ($params['max_depth'] ?? 5)));

        return $client->run('MATCH (from:CanonicalGraphNode {external_id: $from_external_id, graph_version: $graph_version}), (to:CanonicalGraphNode {external_id: $to_external_id, graph_version: $graph_version}) MATCH p = shortestPath((from)-[:CALLS*1..'.$depth.']-(to)) WHERE ALL(n IN nodes(p) WHERE n.graph_version = $graph_version) AND ALL(r IN relationships(p) WHERE r.graph_version = $graph_version) RETURN [n IN nodes(p) | {node: properties(n), labels: labels(n)}] AS nodes, [r IN relationships(p) | properties(r)] AS edges LIMIT 1', ['from_external_id' => (string) ($params['from_symbol_id'] ?? ''), 'to_external_id' => (string) ($params['to_symbol_id'] ?? ''), 'graph_version' => (string) $projection->graph_version]);
    }

    /** @param array<string, mixed> $params */
    private function runTraverse(Neo4jClient $client, array $params, object $projection): mixed
    {
        $depth = max(1, min(3, (int) ($params['max_depth'] ?? 2)));
        $direction = (string) ($params['direction'] ?? 'any');
        $pattern = match ($direction) {
            'in' => '<-[:CALLS*1..'.$depth.']-',
            'out' => '-[:CALLS*1..'.$depth.']->',
            default => '-[:CALLS*1..'.$depth.']-',
        };

        return $client->run('MATCH p=(start:CanonicalGraphNode {external_id: $start, graph_version: $graph_version})'.$pattern.'(node:CanonicalGraphNode {graph_version: $graph_version}) WHERE ALL(n IN nodes(p) WHERE n.graph_version = $graph_version) AND ALL(r IN relationships(p) WHERE r.graph_version = $graph_version) RETURN [n IN nodes(p) | {node: properties(n), labels: labels(n)}] AS nodes, [r IN relationships(p) | properties(r)] AS edges LIMIT $limit', ['start' => (string) ($params['start'] ?? ''), 'graph_version' => (string) $projection->graph_version, 'limit' => max(1, min(50, (int) ($params['limit'] ?? 20)))]);
    }

    private function envelope(string $projectId, string $scopeType, string $scopeId, ?object $projection = null): array
    {
        $schema = $projection?->artifact_type;
        $headCommit = $projection?->head_commit;
        if ($projection !== null && $scopeType === 'workspace_binding') {
            $artifact = DB::table('hades_agent_artifacts')->where('id', $projection->artifact_id)->first();
            $schema = $artifact?->schema ?? $schema;
            $payload = $artifact ? json_decode((string) $artifact->artifact, true) : [];
            $headCommit = is_array($payload) ? ($payload['head_commit'] ?? $payload['commit'] ?? $headCommit) : $headCommit;
        }

        return ['found' => false, 'reason' => null, 'graph_version' => $projection?->graph_version, 'quality' => $projection?->quality, 'results' => [], 'edges' => [], 'metadata' => ['project_id' => $projectId, 'source_scope_type' => $scopeType, 'source_scope_id' => $scopeId, 'projection_id' => $projection?->id, 'graph_version' => $projection?->graph_version, 'artifact_id' => $projection?->artifact_id, 'artifact_type' => $projection?->artifact_type, 'schema' => $schema, 'head_commit' => $headCommit, 'quality' => $projection?->quality, 'node_count' => $projection?->node_count, 'relationship_count' => $projection?->relationship_count]];
    }

    /** @return array{0: list<array<string,mixed>>, 1: list<array<string,mixed>>} */
    private function normaliseRows(mixed $result): array
    {
        if (! is_array($result)) {
            return [[], []];
        }
        $nodes = [];
        $edges = [];
        foreach ($result as $row) {
            if (! is_array($row)) {
                continue;
            }
            if (isset($row['nodes']) && is_array($row['nodes'])) {
                foreach ($row['nodes'] as $node) {
                    $nodes[] = $this->node(is_array($node) ? $node : []);
                }
            } elseif (isset($row['node'])) {
                $nodes[] = $this->node($row);
            }
            if (isset($row['edge']) && is_array($row['edge'])) {
                $edges[] = $row['edge'];
            }
            if (isset($row['edges']) && is_array($row['edges'])) {
                $edges = array_merge($edges, $row['edges']);
            }
        }

        return [$nodes, $edges];
    }

    private function node(array $row): array
    {
        $props = is_array($row['node'] ?? null) ? $row['node'] : $row;

        return ['id' => (string) ($props['external_id'] ?? $props['symbol_id'] ?? $props['id'] ?? 'node-'.md5(json_encode($props))), 'labels' => array_values($row['labels'] ?? []), 'properties' => $props];
    }
}
