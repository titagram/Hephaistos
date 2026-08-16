"""Tests for the store-level CAS fire claim (Phase 4C).

`claim_job_for_fire` gives multi-machine at-most-once semantics when an external
scheduler (Chronos) fires a job: across N gateway replicas, exactly ONE wins the
claim for a given fire. Single-machine deployments always win (unaffected).

These exercise the real store against a temp HERMES_HOME (no mocks) per the
E2E-over-mocks discipline for file-touching code.
"""
import pytest


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME so jobs.json doesn't touch the real store."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # cron.jobs caches no home at import; get_hermes_home() reads the env live.
    yield tmp_path


def test_claim_succeeds_once_then_blocks(temp_home):
    """First claim for a fire wins; a second claim for the same fire loses, and
    next_run_at is advanced (a re-delivery for the old time can't re-fire)."""
    from cron.jobs import create_job, claim_job_for_fire, get_job

    job = create_job(prompt="x", schedule="every 5m", name="t")
    jid = job["id"]
    before = get_job(jid)["next_run_at"]

    assert claim_job_for_fire(jid) is True
    assert claim_job_for_fire(jid) is False
    assert get_job(jid)["next_run_at"] != before


def test_claim_oneshot_cannot_be_double_claimed(temp_home):
    """A one-shot can't be double-claimed (the fresh claim blocks the retry)."""
    from cron.jobs import create_job, claim_job_for_fire

    job = create_job(prompt="x", schedule="30m", name="o")
    assert claim_job_for_fire(job["id"]) is True
    assert claim_job_for_fire(job["id"]) is False


def test_claim_unknown_job_returns_false(temp_home):
    from cron.jobs import claim_job_for_fire

    assert claim_job_for_fire("nope-does-not-exist") is False


def test_claim_paused_job_returns_false(temp_home):
    """A paused job can't be claimed."""
    from cron.jobs import create_job, claim_job_for_fire, pause_job

    job = create_job(prompt="x", schedule="every 5m", name="p")
    pause_job(job["id"])
    assert claim_job_for_fire(job["id"]) is False


def test_stale_claim_is_reclaimable(temp_home, monkeypatch):
    """A claim older than the TTL is overwritten — the fire isn't stuck forever
    if the winning machine crashed before mark_job_run cleared the claim."""
    from cron.jobs import create_job, claim_job_for_fire

    job = create_job(prompt="x", schedule="every 5m", name="s")
    jid = job["id"]
    assert claim_job_for_fire(jid) is True
    # With a 0s TTL, the existing claim is always considered stale.
    assert claim_job_for_fire(jid, claim_ttl_seconds=0) is True


def test_mark_job_run_clears_claim(temp_home):
    """After a recurring job completes, its claim is cleared so the next fire
    can be claimed again."""
    from cron.jobs import create_job, claim_job_for_fire, mark_job_run, get_job

    job = create_job(prompt="x", schedule="every 5m", name="c")
    jid = job["id"]
    assert claim_job_for_fire(jid) is True
    assert get_job(jid).get("fire_claim") is not None

    mark_job_run(jid, success=True)
    assert get_job(jid).get("fire_claim") is None
    # …and the re-armed recurring job is claimable again.
    assert claim_job_for_fire(jid) is True


def test_active_owner_can_renew_stale_claim(temp_home):
    """A live run refreshes its lease before the stale-claim TTL expires."""
    from cron.jobs import (
        claim_job_for_fire,
        create_job,
        get_job,
        load_jobs,
        renew_job_fire_claim,
        save_jobs,
    )

    job = create_job(prompt="x", schedule="every 5m", name="renew")
    jid = job["id"]
    assert claim_job_for_fire(jid, owner="runner-a") is True

    jobs = load_jobs()
    stored = next(item for item in jobs if item["id"] == jid)
    stored["fire_claim"]["at"] = "2000-01-01T00:00:00+00:00"
    save_jobs(jobs)

    assert renew_job_fire_claim(jid, owner="runner-b") is False
    assert renew_job_fire_claim(jid, owner="runner-a") is True
    assert get_job(jid)["fire_claim"]["by"] == "runner-a"
    assert claim_job_for_fire(jid, owner="runner-b") is False


def test_stale_runner_cannot_clear_new_owner_claim(temp_home):
    """A late completion must not erase the lease of a replacement runner."""
    from cron.jobs import claim_job_for_fire, create_job, get_job, mark_job_run

    job = create_job(prompt="x", schedule="every 5m", name="ownership")
    jid = job["id"]
    assert claim_job_for_fire(jid, owner="runner-a") is True
    assert claim_job_for_fire(jid, owner="runner-b", claim_ttl_seconds=0) is True

    mark_job_run(jid, success=True, fire_claim_owner="runner-a")
    assert get_job(jid)["fire_claim"]["by"] == "runner-b"

    mark_job_run(jid, success=True, fire_claim_owner="runner-b")
    assert get_job(jid).get("fire_claim") is None


def test_queued_snapshot_cannot_claim_completed_occurrence(temp_home):
    """A delayed worker must not execute an occurrence another replica finished."""
    from cron.jobs import claim_job_for_fire, create_job, get_job, mark_job_run

    job = create_job(prompt="x", schedule="every 5m", name="queued")
    jid = job["id"]
    queued_next_run = get_job(jid)["next_run_at"]

    assert claim_job_for_fire(jid, owner="runner-b") is True
    mark_job_run(jid, success=True, fire_claim_owner="runner-b")

    assert claim_job_for_fire(
        jid,
        owner="queued-runner-a",
        expected_next_run_at=queued_next_run,
    ) is False
    assert get_job(jid).get("fire_claim") is None
