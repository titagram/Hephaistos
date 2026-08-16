"""Characterization + unit tests for the `run_one_job` shared helper (Phase 4A).

`tick`'s per-job body (`_process_job`) is the execute → save → deliver → mark
sequence that fires ONE due job. Phase 4A extracts it into a module-level
`run_one_job(job, *, adapters=None, loop=None, verbose=False)` so the external
Chronos provider's `fire_due` can reuse the IDENTICAL body — no duplicated
correctness.

The first test characterizes the sequence as driven through `tick()` (proving
the extraction didn't change `tick`'s behavior); the rest unit-test the
extracted helper directly.
"""
import threading

import cron.scheduler as s


def _patch_pipeline(monkeypatch, *, success=True, output="out", final="final response",
                    error=None, silent_marker_in=None):
    """Patch the job pipeline primitives and record the call order."""
    calls = []

    def fake_run_job(job):
        calls.append(("run_job", job["id"]))
        fr = final if silent_marker_in is None else silent_marker_in
        return (success, output, fr, error)

    def fake_save(jid, out):
        calls.append(("save", jid))
        return f"/tmp/{jid}.txt"

    def fake_deliver(job, content, adapters=None, loop=None):
        calls.append(("deliver", job["id"]))
        return None

    def fake_mark(jid, ok, err=None, delivery_error=None, **kwargs):
        calls.append(("mark", jid, ok))

    monkeypatch.setattr(s, "run_job", fake_run_job)
    monkeypatch.setattr(s, "save_job_output", fake_save)
    monkeypatch.setattr(s, "_deliver_result", fake_deliver)
    monkeypatch.setattr(s, "mark_job_run", fake_mark)
    return calls


def test_tick_process_job_sequence(monkeypatch):
    """Characterization: a single due job driven through tick() runs the
    sequence run_job → save → deliver → mark, in that order."""
    calls = _patch_pipeline(monkeypatch)
    monkeypatch.setattr(s, "get_due_jobs", lambda: [{"id": "j1", "name": "t"}])
    monkeypatch.setattr(s, "advance_next_run", lambda jid: True)
    monkeypatch.setattr(
        s,
        "_claim_due_job",
        lambda job: {**job, "fire_claim": {"by": "tick-owner"}},
    )
    monkeypatch.setattr(s, "renew_job_fire_claim", lambda *_a, **_kw: True)

    s.tick(verbose=False, sync=True)

    assert [c[0] for c in calls] == ["run_job", "save", "deliver", "mark"]
    assert calls[-1] == ("mark", "j1", True)


def test_tick_lost_store_claim_does_not_dispatch(monkeypatch):
    """The built-in ticker participates in the same CAS as external fires."""
    calls = _patch_pipeline(monkeypatch)
    monkeypatch.setattr(s, "get_due_jobs", lambda: [{"id": "j-lost", "name": "t"}])
    monkeypatch.setattr(s, "advance_next_run", lambda jid: True)
    monkeypatch.setattr(s, "_claim_due_job", lambda job: None)

    assert s.tick(verbose=False, sync=True) == 0
    assert calls == []


def test_run_one_job_success_sequence(monkeypatch):
    """The extracted helper runs the same execute→save→deliver→mark sequence
    for a successful job."""
    calls = _patch_pipeline(monkeypatch)

    ok = s.run_one_job({"id": "j2", "name": "t"})

    assert ok is True
    assert [c[0] for c in calls] == ["run_job", "save", "deliver", "mark"]
    assert calls[-1] == ("mark", "j2", True)


def test_run_one_job_refuses_stale_claim_before_execution(monkeypatch):
    """A caller must not execute after its persisted lease was replaced."""
    calls = _patch_pipeline(monkeypatch)
    monkeypatch.setattr(s, "renew_job_fire_claim", lambda *_a, **_kw: False)

    ok = s.run_one_job(
        {"id": "stale", "name": "t", "fire_claim": {"by": "runner-a"}}
    )

    assert ok is False
    assert calls == []


def test_run_one_job_discards_output_and_delivery_after_claim_loss(monkeypatch):
    """Lease loss during execution fences persisted output and delivery."""
    release = threading.Event()
    claim_lost = threading.Event()
    renewals = []
    calls = []

    def blocking_run(job):
        calls.append(("run_job", job["id"]))
        assert release.wait(timeout=2)
        return True, "stale output", "stale response", None

    def fake_renew(jid, *, owner):
        renewals.append((jid, owner))
        if len(renewals) == 1:
            return True
        claim_lost.set()
        return False

    monkeypatch.setattr(s, "run_job", blocking_run)
    monkeypatch.setattr(
        s,
        "save_job_output",
        lambda jid, _out: calls.append(("save", jid)) or "/tmp/out",
    )
    monkeypatch.setattr(
        s,
        "_deliver_result",
        lambda job, *_a, **_kw: calls.append(("deliver", job["id"])),
    )
    monkeypatch.setattr(
        s,
        "mark_job_run",
        lambda jid, *_a, **_kw: calls.append(("mark", jid)),
    )
    monkeypatch.setattr(s, "renew_job_fire_claim", fake_renew)
    monkeypatch.setattr(s, "_FIRE_CLAIM_HEARTBEAT_SECONDS", 0.01)

    result = []
    worker = threading.Thread(
        target=lambda: result.append(
            s.run_one_job(
                {"id": "lost", "name": "t", "fire_claim": {"by": "runner-a"}}
            )
        )
    )
    worker.start()

    assert claim_lost.wait(timeout=1)
    release.set()
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert result == [False]
    assert calls == [("run_job", "lost")]


def test_run_one_job_does_not_deliver_if_claim_is_lost_while_saving(monkeypatch):
    """A lease lost during output persistence must fence later delivery."""
    save_started = threading.Event()
    loss_observed = threading.Event()
    calls = []

    def fake_renew(_jid, *, owner):
        if threading.current_thread() is threading.main_thread():
            return True
        if save_started.is_set():
            loss_observed.set()
            return False
        return True

    def blocking_save(jid, _output):
        calls.append(("save", jid))
        save_started.set()
        assert loss_observed.wait(timeout=1)
        return "/tmp/out"

    monkeypatch.setattr(
        s,
        "run_job",
        lambda _job: (True, "stale output", "stale response", None),
    )
    monkeypatch.setattr(s, "save_job_output", blocking_save)
    monkeypatch.setattr(
        s,
        "_deliver_result",
        lambda job, *_a, **_kw: calls.append(("deliver", job["id"])),
    )
    monkeypatch.setattr(
        s,
        "mark_job_run",
        lambda jid, *_a, **_kw: calls.append(("mark", jid)),
    )
    monkeypatch.setattr(s, "renew_job_fire_claim", fake_renew)
    monkeypatch.setattr(s, "_FIRE_CLAIM_HEARTBEAT_SECONDS", 0.01)

    assert s.run_one_job(
        {"id": "save-race", "name": "t", "fire_claim": {"by": "runner-a"}}
    ) is False
    assert loss_observed.is_set()
    assert calls == [("save", "save-race")]


def test_run_one_job_renews_fire_claim_while_active(monkeypatch):
    """Long active runs keep their cross-process lease alive until completion."""
    entered = threading.Event()
    release = threading.Event()
    renewed = threading.Event()
    renewals = []

    def blocking_run(job):
        entered.set()
        assert release.wait(timeout=2)
        return True, "out", "final response", None

    monkeypatch.setattr(s, "run_job", blocking_run)
    monkeypatch.setattr(s, "save_job_output", lambda *_a, **_kw: "/tmp/out")
    monkeypatch.setattr(s, "_deliver_result", lambda *_a, **_kw: None)
    monkeypatch.setattr(s, "mark_job_run", lambda *_a, **_kw: None)
    monkeypatch.setattr(s, "_FIRE_CLAIM_HEARTBEAT_SECONDS", 0.01)

    def fake_renew(jid, *, owner):
        renewals.append((jid, owner))
        renewed.set()
        return True

    monkeypatch.setattr(s, "renew_job_fire_claim", fake_renew)
    worker = threading.Thread(
        target=s.run_one_job,
        args=({"id": "leased", "name": "t", "fire_claim": {"by": "runner-a"}},),
    )
    worker.start()

    assert entered.wait(timeout=1)
    assert renewed.wait(timeout=1)
    release.set()
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert renewals[0] == ("leased", "runner-a")


def test_run_one_job_heartbeat_recovers_after_transient_renewal_error(monkeypatch):
    """One storage error must not abandon an otherwise-owned active lease."""
    release = threading.Event()
    renewed = threading.Event()
    attempts = []

    def blocking_run(job):
        assert release.wait(timeout=2)
        return True, "out", "final response", None

    def flaky_renew(jid, *, owner):
        attempts.append((jid, owner))
        if len(attempts) == 1:
            raise OSError("transient store error")
        renewed.set()
        return True

    monkeypatch.setattr(s, "run_job", blocking_run)
    monkeypatch.setattr(s, "save_job_output", lambda *_a, **_kw: "/tmp/out")
    monkeypatch.setattr(s, "_deliver_result", lambda *_a, **_kw: None)
    monkeypatch.setattr(s, "mark_job_run", lambda *_a, **_kw: None)
    monkeypatch.setattr(s, "_FIRE_CLAIM_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(s, "renew_job_fire_claim", flaky_renew)

    worker = threading.Thread(
        target=s.run_one_job,
        args=({"id": "flaky", "name": "t", "fire_claim": {"by": "runner-a"}},),
    )
    worker.start()

    assert renewed.wait(timeout=1)
    release.set()
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert len(attempts) >= 2


def test_run_one_job_silent_skips_delivery(monkeypatch):
    """A [SILENT] final response saves output + marks the run but does NOT
    deliver."""
    calls = _patch_pipeline(monkeypatch, silent_marker_in="[SILENT]")

    s.run_one_job({"id": "j3", "name": "t"})

    kinds = [c[0] for c in calls]
    assert "run_job" in kinds and "save" in kinds and "mark" in kinds
    assert "deliver" not in kinds


def test_run_one_job_empty_response_is_soft_failure(monkeypatch):
    """An empty final response marks the run as NOT ok (issue #8585)."""
    calls = _patch_pipeline(monkeypatch, final="   ")

    s.run_one_job({"id": "j4", "name": "t"})

    mark = [c for c in calls if c[0] == "mark"][0]
    assert mark == ("mark", "j4", False)


def test_run_one_job_failed_job_delivers_error(monkeypatch):
    """A failed job still delivers (the error notice) and marks not-ok."""
    calls = _patch_pipeline(monkeypatch, success=False, final="", error="boom")

    s.run_one_job({"id": "j5", "name": "t"})

    kinds = [c[0] for c in calls]
    assert "deliver" in kinds  # failures always deliver
    mark = [c for c in calls if c[0] == "mark"][0]
    assert mark == ("mark", "j5", False)


def test_run_one_job_exception_marks_failure(monkeypatch):
    """If run_job raises, the helper marks the run failed and returns False
    rather than propagating."""
    def boom(job):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(s, "run_job", boom)
    marks = []
    monkeypatch.setattr(
        s, "mark_job_run",
        lambda jid, ok, err=None, delivery_error=None: marks.append((jid, ok)),
    )

    ok = s.run_one_job({"id": "j6", "name": "t"})

    assert ok is False
    assert marks == [("j6", False)]
