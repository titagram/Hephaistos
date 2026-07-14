<?php

namespace App\Jobs;

use App\Services\Graph\CanonicalGraphProjectionService;
use App\Services\Graph\CanonicalGraphRepository;
use App\Services\Graph\Neo4jCanonicalGraphProjector;
use App\Services\Neo4jClientFactory;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Queue\Queueable;
use RuntimeException;
use Throwable;

class ProjectCanonicalGraphToNeo4j implements ShouldQueue
{
    use Queueable;

    public int $tries = 3;

    public function __construct(public readonly string $projectionId) {}

    public function handle(CanonicalGraphRepository $repository, CanonicalGraphProjectionService $projections, Neo4jCanonicalGraphProjector $projector, Neo4jClientFactory $clients): void
    {
        $projection = $projections->claimForWorker($this->projectionId);
        if ($projection === null) {
            return;
        }
        try {
            $graph = $repository->findByIdentity($projection->project_id, $projection->source_scope_type, $projection->source_scope_id, $projection->artifact_type, $projection->artifact_id);
            if ($graph === null) {
                throw new RuntimeException('artifact_missing');
            }
            if (! hash_equals($projection->checksum, $graph['identity']['checksum'])) {
                throw new RuntimeException('artifact_changed');
            }
            $counts = $projector->project($graph, $projection, $clients->client());
            $projections->markReady($projection->id, $counts['nodes'], $counts['relationships']);
        } catch (Throwable $exception) {
            $projections->markRetryPending($projection->id, $this->failureCode($exception));
            throw $exception;
        }
    }

    /**
     * Laravel invokes this hook only after the queue has exhausted retries.
     */
    public function failed(Throwable $exception): void
    {
        app(CanonicalGraphProjectionService::class)->markFailed(
            $this->projectionId,
            $this->failureCode($exception),
        );
    }

    private function failureCode(Throwable $exception): string
    {
        if (in_array($exception->getMessage(), ['artifact_missing', 'artifact_changed'], true)) {
            return $exception->getMessage();
        }

        return $this->isNeo4jUnavailable($exception) ? 'neo4j_unavailable' : 'neo4j_query_failed';
    }

    private function isNeo4jUnavailable(Throwable $exception): bool
    {
        for ($current = $exception; $current !== null; $current = $current->getPrevious()) {
            $class = strtolower($current::class);
            if (str_contains($class, 'connectexception') || str_contains($class, 'serviceunavailable') || str_contains($class, 'timeoutexception') || str_contains($class, 'transportexception')) {
                return true;
            }
            $message = strtolower($current->getMessage());
            if (preg_match('/connection (?:refused|reset)|service\s*unavailable|timed?\s*out|timeout|could not resolve|name or service not known|host unreachable|network is unreachable/', $message) === 1) {
                return true;
            }
        }

        return false;
    }
}
