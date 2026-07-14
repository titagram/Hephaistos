import copy

from hermes_cli.gnothi.wiki import render_wiki


def _artifact():
    return {
        "schema": "hades.organism_graph.v1",
        "organism_contract": {
            "version": "hades.gnothi_seauton.v1",
            "revision_id": "rev-1",
            "generation": {"id": "git:abc", "scope": "stable"},
            "status": "partial",
            "coverage": {
                "source": {"status": "current", "verified_at": "2026-07-14T12:00:00Z"},
                "experience": {"status": "missing", "verified_at": None},
            },
        },
        "nodes": [
            {
                "id": "capability:terminal",
                "kind": "capability",
                "label": "Terminal",
                "state": {"available": True, "degraded": False},
                "evidence_refs": ["evidence:terminal"],
                "properties": {"token": "wiki-secret"},
            },
            {
                "id": "invariant:no-secrets",
                "kind": "invariant",
                "label": "No secrets",
                "state": {"available": True},
                "evidence_refs": ["evidence:contract"],
                "properties": {},
            },
        ],
        "edges": [],
        "evidence": [
            {"id": "evidence:terminal", "kind": "probe", "path": "/private/work/tool.py"},
            {"id": "evidence:contract", "kind": "test", "path": "tests/test.py"},
        ],
    }


def test_wiki_is_deterministic_evidence_linked_and_explicit_about_unknowns():
    artifact = _artifact()
    reordered = copy.deepcopy(artifact)
    reordered["nodes"].reverse()
    reordered["evidence"].reverse()

    rendered = render_wiki(artifact)

    assert rendered == render_wiki(reordered)
    headings = [
        "## Anatomy",
        "## Capabilities",
        "## Dependencies",
        "## Contracts and invariants",
        "## Runtime state",
        "## Known degradation",
        "## Generations and rollback history",
        "## Coverage, freshness, and unknown areas",
        "## Evidence index",
    ]
    assert [rendered.index(heading) for heading in headings] == sorted(
        rendered.index(heading) for heading in headings
    )
    assert "generated" in rendered.lower()
    assert "Partial" in rendered
    assert "Unknown" in rendered
    assert "evidence-terminal" in rendered
    assert "evidence-contract" in rendered
    assert "wiki-secret" not in rendered
    assert "/private/work" not in rendered
