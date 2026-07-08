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
    assert result["automatic_recall_reason"] == "raw chunks are excluded from automatic recall"
    assert result["memory_proposal_ready"] is False
    assert result["promotion_state"] == "review_candidate"
    assert result["quality_score"] == 65
    assert result["quality_grade"] == "reviewable"
    assert result["quality_issues"] == ["raw_chunk_quarantined", "review_required"]
    assert result["candidate_fact_count"] == 1
    fact = result["candidate_facts"][0]
    assert fact["kind"] == "route_handler_group"
    assert fact["predicate"] == "handles_routes"
    assert fact["object_count"] == 3
    assert len(fact["fingerprint"]) == 64
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


def test_note_quality_marks_unstructured_note_as_manual_structuring_needed():
    from hermes_cli.hades_note_quality import analyze_note_quality

    result = analyze_note_quality("Remember to check the flaky import later.", source="note.md")

    assert result["classification"] == "unclassified_note"
    assert result["raw_chunk"] is False
    assert result["automatic_recall_allowed"] is True
    assert result["automatic_recall_reason"] == "freeform notes require manual evidence before promotion"
    assert result["promotion_state"] == "needs_manual_structuring"
    assert result["quality_grade"] == "quarantine"
    assert result["quality_issues"] == ["no_structured_facts", "missing_evidence_fingerprint"]
    assert result["candidate_fact_count"] == 0


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
    assert payload["quality_grade"] == "reviewable"
    assert payload["promotion_state"] == "review_candidate"
    assert payload["candidate_fact_count"] == 1
    assert payload["candidate_facts"][0]["object_count"] == 3


def test_backfill_note_command_can_create_review_proposals(monkeypatch, tmp_path, capsys):
    import hermes_cli.hades_backend_cmd as cmd
    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_runtime import workspace_fingerprint

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint=workspace_fingerprint(workspace, "proj_1"),
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    note = workspace / "raw-note.md"
    note.write_text(RAW_ROUTE_CHUNK, encoding="utf-8")

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="backfill-note",
            create_proposals=True,
            file=str(note),
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    with hdb.connect_closing() as conn:
        proposals = hdb.list_memory_proposals(conn)

    assert rc == 0
    assert payload["created_proposal_count"] == 1
    assert payload["created_proposal_ids"] == [proposals[0].id]
    assert payload["skipped_duplicate_proposal_count"] == 0
    assert proposals[0].status == "pending"
    assert proposals[0].intent == "note_backfill_candidate"
    assert proposals[0].summary.endswith("handles 3 routes in the taxonomy_flock_vocabulary_security_activity_category family.")
    assert proposals[0].provenance["source"] == "hades_note_quality"
    assert len(proposals[0].provenance["candidate_fact_fingerprint"]) == 64
    assert proposals[0].provenance["candidate_fact"]["review_status"] == "candidate"

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="backfill-note",
            create_proposals=True,
            file=str(note),
            json=True,
        )
    )
    second_payload = json.loads(capsys.readouterr().out)

    with hdb.connect_closing() as conn:
        proposals_after_second_run = hdb.list_memory_proposals(conn)

    assert rc == 0
    assert len(proposals_after_second_run) == 1
    assert second_payload["created_proposal_count"] == 0
    assert second_payload["skipped_duplicate_proposal_count"] == 1
    assert second_payload["skipped_duplicate_proposal_ids"] == [proposals[0].id]
