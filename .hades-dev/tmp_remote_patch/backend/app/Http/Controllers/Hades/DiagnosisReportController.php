<?php

namespace App\Http\Controllers\Hades;

use App\Http\Controllers\Controller;
use App\Services\Hades\HadesEvidencePolicy;
use App\Services\Hades\HadesProjectAwareness;
use App\Services\Hades\HadesSearchDocumentIndexer;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;
use Illuminate\Validation\Rule;
use Symfony\Component\HttpFoundation\Response;

class DiagnosisReportController extends Controller
{
    public function __construct(
        private readonly HadesEvidencePolicy $policy,
        private readonly HadesProjectAwareness $awareness,
        private readonly HadesSearchDocumentIndexer $searchIndexer,
    ) {}

    private const CONFIDENCE = ['high', 'medium', 'low', 'insufficient'];

    private const STATUSES = ['draft', 'final'];

    private const PROMOTION_VERIFICATIONS = ['user_confirmed', 'test_passed', 'manual_review'];

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'project_id' => ['required', 'string'],
            'workspace_binding_id' => ['required', 'string'],
            'bug_report_id' => ['nullable', 'string'],
            'status' => ['nullable', 'string', Rule::in(self::STATUSES)],
            'confidence' => ['required', 'string', Rule::in(self::CONFIDENCE)],
            'root_cause' => ['required', 'string', 'max:4000'],
            'mechanism' => ['nullable', 'string', 'max:8000'],
            'evidence_refs' => ['nullable', 'array'],
            'freshness' => ['nullable', 'array'],
            'payload' => ['nullable', 'array'],
            'root_cause_id' => ['nullable', 'string', 'max:191'],
            'bug_class' => ['nullable', 'string', 'max:128'],
            'failure_classification' => ['nullable', 'string', 'max:128'],
            'affected_refs' => ['nullable', 'array'],
            'affected_refs.*' => ['string', 'max:500'],
            'redactions' => ['nullable', 'integer', 'min:0', 'max:100000'],
        ]);

        $diagnosisPayload = $this->diagnosisPayloadWithTaxonomy($validated);

        if ($policyError = $this->policy->validateDiagnosisReport(
            $validated['root_cause'],
            $validated['mechanism'] ?? null,
            $validated['evidence_refs'] ?? [],
            $validated['freshness'] ?? [],
            $diagnosisPayload,
        )) {
            return $this->error($policyError['code'], $policyError['message'], Response::HTTP_UNPROCESSABLE_ENTITY);
        }

        if (in_array($validated['confidence'], ['high', 'medium'], true)) {
            $evidenceRefs = $validated['evidence_refs'] ?? [];
            $freshness = $validated['freshness'] ?? [];

            if ($evidenceRefs === []) {
                return $this->error('diagnosis_evidence_refs_required', 'High or medium confidence diagnosis reports require evidence refs.', Response::HTTP_UNPROCESSABLE_ENTITY);
            }

            if (($freshness['status'] ?? null) !== 'current') {
                return $this->error('diagnosis_freshness_not_current', 'High or medium confidence diagnosis reports require current project awareness freshness.', Response::HTTP_UNPROCESSABLE_ENTITY);
            }
        }

        $auth = $request->attributes->get('hades_auth');
        $agent = $auth['agent'];
        $binding = $this->linkedBinding($agent, $validated['project_id'], $validated['workspace_binding_id']);

        if ($binding instanceof JsonResponse) {
            return $binding;
        }

        if (($validated['bug_report_id'] ?? null) !== null) {
            $exists = DB::table('hades_bug_reports')
                ->where('id', $validated['bug_report_id'])
                ->where('project_id', $validated['project_id'])
                ->where('workspace_binding_id', $binding->id)
                ->exists();

            if (! $exists) {
                return $this->error('bug_report_not_found', 'Hades bug report was not found.', Response::HTTP_NOT_FOUND);
            }
        }

        if (in_array($validated['confidence'], ['high', 'medium'], true)) {
            $awareness = $this->awareness->statusPayload($binding);

            if (! (bool) ($awareness['diagnosable_without_source'] ?? false)) {
                return $this->error(
                    'diagnosis_awareness_not_diagnosable',
                    'High or medium confidence diagnosis reports require current graph, bug evidence, and source slice coverage.',
                    Response::HTTP_UNPROCESSABLE_ENTITY,
                );
            }
        }

        $id = (string) Str::ulid();
        $now = now();

        DB::table('hades_diagnosis_reports')->insert([
            'id' => $id,
            'project_id' => $validated['project_id'],
            'bug_report_id' => $validated['bug_report_id'] ?? null,
            'hades_agent_id' => $agent->id,
            'workspace_binding_id' => $binding->id,
            'status' => $validated['status'] ?? 'draft',
            'confidence' => $validated['confidence'],
            'root_cause' => $validated['root_cause'],
            'mechanism' => $validated['mechanism'] ?? null,
            'evidence_refs' => isset($validated['evidence_refs']) ? json_encode($validated['evidence_refs'], JSON_THROW_ON_ERROR) : null,
            'freshness' => isset($validated['freshness']) ? json_encode($validated['freshness'], JSON_THROW_ON_ERROR) : null,
            'payload' => $diagnosisPayload !== [] ? json_encode($diagnosisPayload, JSON_THROW_ON_ERROR) : null,
            'redactions' => (int) ($validated['redactions'] ?? 0),
            'created_at' => $now,
            'updated_at' => $now,
        ]);

        $report = DB::table('hades_diagnosis_reports')->where('id', $id)->first();

        return response()->json([
            'protocol_version' => 'v1',
            'project_id' => $validated['project_id'],
            'workspace_binding_id' => $binding->id,
            'diagnosis_report' => self::reportPayload($report),
            'server_time' => now()->toISOString(),
        ], Response::HTTP_CREATED);
    }

    public function promote(Request $request, string $diagnosisReport): JsonResponse
    {
        $validated = $request->validate([
            'project_id' => ['required', 'string'],
            'workspace_binding_id' => ['required', 'string'],
            'verification_status' => ['required', 'string', Rule::in(self::PROMOTION_VERIFICATIONS)],
            'fix_commit' => ['nullable', 'string', 'max:191'],
            'fix_pr_url' => ['nullable', 'string', 'max:2048'],
            'regression_tests' => ['nullable', 'array'],
            'affected_symbols' => ['nullable', 'array'],
            'payload' => ['nullable', 'array'],
            'redactions' => ['nullable', 'integer', 'min:0', 'max:100000'],
        ]);

        $auth = $request->attributes->get('hades_auth');
        $agent = $auth['agent'];
        $binding = $this->linkedBinding($agent, $validated['project_id'], $validated['workspace_binding_id']);

        if ($binding instanceof JsonResponse) {
            return $binding;
        }

        $report = DB::table('hades_diagnosis_reports')
            ->where('id', $diagnosisReport)
            ->where('project_id', $validated['project_id'])
            ->where('workspace_binding_id', $binding->id)
            ->first();

        if (! $report) {
            return $this->error('diagnosis_report_not_found', 'Hades diagnosis report was not found.', Response::HTTP_NOT_FOUND);
        }

        if ($report->status !== 'final') {
            return $this->error('diagnosis_report_not_final', 'Only final diagnosis reports can be promoted to resolved bug memory.', Response::HTTP_CONFLICT);
        }

        if (! in_array($report->confidence, ['high', 'medium'], true)) {
            return $this->error('diagnosis_confidence_too_low', 'Only high or medium confidence diagnosis reports can become resolved bug memory.', Response::HTTP_CONFLICT);
        }

        $evidenceRefs = self::decode($report->evidence_refs);
        $freshness = self::decode($report->freshness);
        $reportPayload = self::decode($report->payload);
        $affectedSymbols = self::normaliseStringList($validated['affected_symbols'] ?? ($reportPayload['affected_symbols'] ?? []));
        $regressionTests = self::normaliseStringList($validated['regression_tests'] ?? ($reportPayload['regression_tests'] ?? []));
        $promotionPayload = [
            'verification_status' => $validated['verification_status'],
            'fix_commit' => $validated['fix_commit'] ?? null,
            'fix_pr_url' => $validated['fix_pr_url'] ?? null,
            'affected_symbols' => $affectedSymbols,
            'regression_tests' => $regressionTests,
            'payload' => $validated['payload'] ?? [],
        ];

        if ($policyError = $this->policy->validateDiagnosisReport(
            (string) $report->root_cause,
            $report->mechanism,
            $evidenceRefs,
            $freshness,
            array_merge($reportPayload, ['promotion' => $promotionPayload]),
        )) {
            return $this->error($policyError['code'], $policyError['message'], Response::HTTP_UNPROCESSABLE_ENTITY);
        }

        $existing = DB::table('project_memory_links as links')
            ->join('project_memory_entries as entries', 'entries.id', '=', 'links.memory_entry_id')
            ->where('links.target_type', 'hades_diagnosis_report')
            ->where('links.target_id', $report->id)
            ->where('entries.project_id', $validated['project_id'])
            ->where('entries.kind', 'resolved_bug')
            ->select('entries.*')
            ->first();

        if ($existing) {
            return response()->json([
                'protocol_version' => 'v1',
                'project_id' => $validated['project_id'],
                'workspace_binding_id' => $binding->id,
                'diagnosis_report_id' => $report->id,
                'already_promoted' => true,
                'resolved_bug_memory' => self::memoryEntryPayload($existing),
                'server_time' => now()->toISOString(),
            ]);
        }

        $bugReport = $report->bug_report_id
            ? DB::table('hades_bug_reports')
                ->where('id', $report->bug_report_id)
                ->where('project_id', $validated['project_id'])
                ->first()
            : null;
        $now = now();
        $validFromCommit = trim((string) ($freshness['workspace_head_commit'] ?? $binding->head_commit ?? ''));
        $currentHead = trim((string) ($binding->head_commit ?? ''));
        $validity = [
            'status' => 'current',
            'valid_from_commit' => $validFromCommit !== '' ? $validFromCommit : null,
            'current_workspace_head_commit' => $currentHead !== '' ? $currentHead : null,
        ];

        if ($validFromCommit !== '' && $currentHead !== '' && ! hash_equals($validFromCommit, $currentHead)) {
            $validity['status'] = 'stale';
            $validity['stale_reason'] = 'workspace_head_changed';
        }

        $memory = DB::transaction(function () use ($affectedSymbols, $agent, $binding, $bugReport, $evidenceRefs, $freshness, $now, $promotionPayload, $regressionTests, $report, $reportPayload, $validated, $validity) {
            $memoryEntryId = (string) Str::ulid();
            $summary = self::compact('Resolved bug: '.($bugReport?->symptom ? $bugReport->symptom.' -> ' : '').$report->root_cause, 4000);
            $payload = [
                'schema' => 'hades.resolved_bug.v1',
                'diagnosis_report_id' => (string) $report->id,
                'bug_report_id' => $report->bug_report_id,
                'symptom' => $bugReport?->symptom,
                'root_cause' => (string) $report->root_cause,
                'root_cause_id' => $reportPayload['root_cause_id'] ?? null,
                'bug_class' => $reportPayload['bug_class'] ?? null,
                'failure_classification' => $reportPayload['failure_classification'] ?? null,
                'mechanism' => $report->mechanism,
                'confidence' => (string) $report->confidence,
                'verification_status' => $validated['verification_status'],
                'affected_symbols' => $affectedSymbols,
                'affected_refs' => self::normaliseStringList($reportPayload['affected_refs'] ?? []),
                'fix_commit' => $validated['fix_commit'] ?? null,
                'fix_pr_url' => $validated['fix_pr_url'] ?? null,
                'regression_tests' => $regressionTests,
                'evidence_refs' => $evidenceRefs,
                'freshness' => $freshness,
                'validity' => $validity,
                'workspace_binding_id' => $binding->id,
                'promotion' => $promotionPayload,
                'redactions' => (int) ($validated['redactions'] ?? 0),
                'promoted_at' => $now->toISOString(),
            ];

            DB::table('project_memory_entries')->insert([
                'id' => $memoryEntryId,
                'project_id' => $validated['project_id'],
                'repository_id' => null,
                'task_id' => null,
                'run_id' => null,
                'author_user_id' => null,
                'agent_key' => $agent->external_agent_id,
                'source' => 'hades_diagnosis_report',
                'kind' => 'resolved_bug',
                'completeness' => 'complete',
                'summary' => $summary,
                'payload' => json_encode($payload, JSON_THROW_ON_ERROR),
                'occurred_at' => $now,
                'created_at' => $now,
                'updated_at' => $now,
            ]);

            $links = [
                ['target_type' => 'hades_diagnosis_report', 'target_id' => (string) $report->id],
            ];
            if ($report->bug_report_id) {
                $links[] = ['target_type' => 'hades_bug_report', 'target_id' => (string) $report->bug_report_id];
            }
            foreach ($evidenceRefs as $ref) {
                if (! is_array($ref) || ! isset($ref['id'])) {
                    continue;
                }
                $type = isset($ref['type']) ? 'hades_'.Str::slug((string) $ref['type'], '_') : 'hades_evidence_ref';
                $links[] = ['target_type' => $type, 'target_id' => (string) $ref['id']];
            }

            foreach ($links as $link) {
                DB::table('project_memory_links')->insert([
                    'id' => (string) Str::ulid(),
                    'memory_entry_id' => $memoryEntryId,
                    'target_type' => $link['target_type'],
                    'target_id' => $link['target_id'],
                    'created_at' => $now,
                    'updated_at' => $now,
                ]);
            }

            $entry = DB::table('project_memory_entries')->where('id', $memoryEntryId)->first();
            if ($entry) {
                $this->searchIndexer->indexMemoryEntry($entry);
            }

            return $entry;
        });

        return response()->json([
            'protocol_version' => 'v1',
            'project_id' => $validated['project_id'],
            'workspace_binding_id' => $binding->id,
            'diagnosis_report_id' => $report->id,
            'already_promoted' => false,
            'resolved_bug_memory' => self::memoryEntryPayload($memory),
            'server_time' => now()->toISOString(),
        ], Response::HTTP_CREATED);
    }

    public static function reportPayload(object $report): array
    {
        return [
            'id' => $report->id,
            'project_id' => $report->project_id,
            'workspace_binding_id' => $report->workspace_binding_id,
            'bug_report_id' => $report->bug_report_id,
            'status' => $report->status,
            'confidence' => $report->confidence,
            'root_cause' => $report->root_cause,
            'mechanism' => $report->mechanism,
            'evidence_refs' => self::decode($report->evidence_refs),
            'freshness' => self::decode($report->freshness),
            'payload' => self::decode($report->payload),
            'redactions' => (int) $report->redactions,
            'created_at' => self::toIsoString($report->created_at),
            'updated_at' => self::toIsoString($report->updated_at),
            'version' => 'diagnosis_report_'.hash('sha256', $report->id.'|'.$report->updated_at),
        ];
    }

    private static function memoryEntryPayload(object $entry): array
    {
        return [
            'id' => $entry->id,
            'project_id' => $entry->project_id,
            'source' => $entry->source,
            'kind' => $entry->kind,
            'summary' => $entry->summary,
            'payload' => self::decode($entry->payload),
            'occurred_at' => self::toIsoString($entry->occurred_at),
            'created_at' => self::toIsoString($entry->created_at),
            'updated_at' => self::toIsoString($entry->updated_at),
            'version' => 'mem_'.hash('sha256', $entry->id.'|'.$entry->updated_at),
        ];
    }

    /**
     * @param  array<string, mixed>  $validated
     * @return array<string, mixed>
     */
    private function diagnosisPayloadWithTaxonomy(array $validated): array
    {
        $payload = isset($validated['payload']) && is_array($validated['payload']) ? $validated['payload'] : [];

        foreach (['root_cause_id', 'bug_class', 'failure_classification'] as $key) {
            $value = trim((string) ($validated[$key] ?? ''));
            if ($value !== '') {
                $payload[$key] = self::compact($value, $key === 'root_cause_id' ? 191 : 128);
            }
        }

        $affectedRefs = self::normaliseStringList($validated['affected_refs'] ?? []);
        if ($affectedRefs !== []) {
            $payload['affected_refs'] = $affectedRefs;
        }

        return $payload;
    }

    /**
     * @return list<string>
     */
    private static function normaliseStringList(mixed $value): array
    {
        if (! is_array($value)) {
            return [];
        }

        $items = [];
        foreach ($value as $item) {
            if (! is_scalar($item)) {
                continue;
            }
            $text = trim((string) $item);
            if ($text !== '') {
                $items[] = self::compact($text, 500);
            }
            if (count($items) >= 50) {
                break;
            }
        }

        return array_values(array_unique($items));
    }

    private static function compact(string $value, int $max): string
    {
        $value = trim(preg_replace('/\s+/', ' ', $value) ?? $value);

        if (strlen($value) <= $max) {
            return $value;
        }

        return rtrim(substr($value, 0, $max - 3)).'...';
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

    private static function decode(mixed $value): array
    {
        $decoded = is_string($value) ? json_decode($value, true) : $value;

        return is_array($decoded) ? $decoded : [];
    }

    private static function toIsoString(mixed $value): ?string
    {
        return $value ? Carbon::parse($value)->toISOString() : null;
    }

    private function error(string $code, string $message, int $status): JsonResponse
    {
        return response()->json(['error' => ['code' => $code, 'message' => $message]], $status);
    }
}
