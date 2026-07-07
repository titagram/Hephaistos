from __future__ import annotations

import json
from types import SimpleNamespace


RAW_ROUTE_CHUNK = """{"batch_id":"knowledge-sync-carnovali-2026-07-06-docs-logbooks-v1","chunk_index":245,"chunk_count":267,"path":"graphify-sidecar/carnovali-facts.md","schema":"hades.backend_wiki.file_chunk.v1","sha256":"bfa3043128cbf07e85c43dec949bf7759ef7ef6f704d91781c88f180288eedf7"} ---BEGIN_CONTENT---
> `file:carnovali/src/Controller/TaxonomyFlock/Vocabulary/SecurityActivityCategoryController.php` (extracted)
- `route:taxonomy_flock_vocabulary_security_activity_category_get_active_import_id` --handled_by--> `file:carnovali/src/Controller/TaxonomyFlock/Vocabulary/SecurityActivityCategoryController.php` (extracted)
- `route:taxonomy_flock_vocabulary_security_activity_category_chart` --handled_by--> `file:carnovali/src/Controller/TaxonomyFlock/Vocabulary/SecurityActivityCategoryController.php` (extracted)
- `route:taxonomy_flock_vocabulary_security_activity_category_ajax_list` --handled_by--> `file:carnovali/src/Controller/TaxonomyFlock/Vocabulary/SecurityActivityCategoryController.php` (extracted)
---END_CONTENT---
"""


def test_note_quality_groups_raw_route_chunk_into_reviewed_candidate_fact():
    from hermes_cli.hades_note_quality import analyze_note_quality

    result = analyze_note_quality(RAW_ROUTE_CHUNK, source="sample.md")

    assert result["schema"] == "hades.note_quality.preview.v1"
    assert result["classification"] == "raw_chunk"
    assert result["raw_chunk"] is True
    assert result["automatic_recall_allowed"] is False
    assert result["memory_proposal_ready"] is False
    assert result["candidate_fact_count"] == 1
    fact = result["candidate_facts"][0]
    assert fact["kind"] == "route_handler_group"
    assert fact["predicate"] == "handles_routes"
    assert fact["object_count"] == 3
    assert fact["review_status"] == "candidate"
    assert fact["subject"] == "carnovali/src/Controller/TaxonomyFlock/Vocabulary/SecurityActivityCategoryController.php"
    assert fact["objects"] == [
        "taxonomy_flock_vocabulary_security_activity_category_get_active_import_id",
        "taxonomy_flock_vocabulary_security_activity_category_chart",
        "taxonomy_flock_vocabulary_security_activity_category_ajax_list",
    ]
    assert fact["evidence_ref"]["schema"] == "hades.backend_wiki.file_chunk.v1"
    assert fact["evidence_ref"]["chunk_index"] == 245
    assert "automatic recall" in result["actions"][0]


def test_backfill_note_command_emits_json_preview(tmp_path, capsys):
    import hermes_cli.hades_backend_cmd as cmd

    note = tmp_path / "raw-note.md"
    note.write_text(RAW_ROUTE_CHUNK, encoding="utf-8")

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="backfill-note",
            file=str(note),
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["classification"] == "raw_chunk"
    assert payload["candidate_fact_count"] == 1
    assert payload["candidate_facts"][0]["object_count"] == 3
