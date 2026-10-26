"""Direct store/runner rules against a private temporary SQLite database."""

import json

import pytest
from sqlalchemy import func, select

from app.db import RunLogRecord, RunRecord, WorkerRecord
from app.models import WorkerCreate, WorkerUpdate
from app.run_logs import new_run_log, safe_event
from app.store import Problem
from app.workers.runner import HandlerOutcome, WorkerRunner


def worker(store, owner, kind="website_monitor"):
    return store.create_worker(owner, WorkerCreate(
        name="Original", kind=kind, url="https://example.com/original"))


def counts(store):
    with store.users.sessions() as session:
        return (
            session.scalar(select(func.count()).select_from(RunRecord)),
            session.scalar(select(func.count()).select_from(RunLogRecord)),
        )


def expect_problem(code, call):
    with pytest.raises(Problem) as error:
        call()
    assert error.value.code == code


def test_single_active_run_rejects_duplicate_without_new_run_or_log(store, owners):
    alice, _ = owners
    saved = worker(store, alice)
    first = store.run_worker(alice, saved.id)
    before = counts(store)
    expect_problem("RUN_ALREADY_ACTIVE", lambda: store.run_worker(alice, saved.id))
    assert counts(store) == before == (1, 1)
    assert store.get_run(alice, first.run_id).status == "queued"


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_legal_queue_claim_finish_and_terminal_replay_rejection(store, owners, terminal):
    alice, _ = owners
    saved = worker(store, alice)
    queued = store.run_worker(alice, saved.id)
    with WorkerRunner(store.users.database_url) as runner:
        snapshot = runner.claim_oldest()
        assert snapshot.id == queued.run_id
        assert store.get_run(alice, queued.run_id).status == "running"
        assert runner.claim_oldest() is None
        outcome = HandlerOutcome({"http_status": 200, "response_time_ms": 1,
                                  "healthy": True}, http_status=200)
        if terminal == "failed":
            assert runner._finish(snapshot.id, outcome, "HTTP_STATUS_ERROR", "Received HTTP 500")
        else:
            assert runner._finish(snapshot.id, outcome)
        after = counts(store)
        assert runner._finish(snapshot.id, outcome) is False
        assert counts(store) == after
    assert store.get_run(alice, queued.run_id).status == terminal
    assert store.get_logs(alice, queued.run_id, 0).items[-1].event == (
        "RUN_SUCCEEDED" if terminal == "succeeded" else "RUN_FAILED")


@pytest.mark.parametrize("status", ["queued", "running", "succeeded"])
def test_nonfailed_retry_has_no_side_effects(store, owners, status):
    alice, _ = owners
    saved = worker(store, alice)
    queued = store.run_worker(alice, saved.id)
    with store.users.sessions() as session:
        session.get(RunRecord, queued.run_id).status = status
        session.commit()
    before = counts(store)
    expect_problem("INVALID_RETRY_STATE", lambda: store.retry_run(alice, queued.run_id))
    assert counts(store) == before
    assert store.get_run(alice, queued.run_id).status == status


def test_retry_keeps_original_kind_url_name_and_config_after_worker_edit(store, owners):
    alice, bob = owners
    saved = worker(store, alice, "url_summarizer")
    with store.users.sessions() as session:
        session.get(WorkerRecord, saved.id).config_json = '{"version":1}'
        session.commit()
    source = store.run_worker(alice, saved.id)
    with store.users.sessions() as session:
        record = session.get(RunRecord, source.run_id)
        record.status = "failed"
        record.error_code = "FETCH_ERROR"
        record.error_message = "Destination request failed"
        session.commit()
    store.update_worker(alice, saved.id, WorkerUpdate(name="Edited", url="https://example.org/new"))
    with store.users.sessions() as session:
        session.get(WorkerRecord, saved.id).config_json = '{"version":2}'
        session.commit()
    before = counts(store)
    expect_problem("NOT_FOUND", lambda: store.retry_run(bob, source.run_id))
    assert counts(store) == before
    retried = store.retry_run(alice, source.run_id)
    assert retried.run_id != source.run_id
    public = store.get_run(alice, retried.run_id)
    assert (public.retry_of_run_id, public.kind, public.worker_name, public.url) == (
        source.run_id, "url_summarizer", "Original", "https://example.com/original")
    with store.users.sessions() as session:
        old = session.get(RunRecord, source.run_id)
        new = session.get(RunRecord, retried.run_id)
        assert json.loads(old.config_snapshot_json) == json.loads(new.config_snapshot_json) == {"version": 1}
        assert old.status == "failed" and new.status == "queued"


def test_log_events_are_sanitized_and_append_in_id_order(store, owners):
    alice, _ = owners
    saved = worker(store, alice)
    queued = store.run_worker(alice, saved.id)
    secret = "secret-token-and-page-content"
    assert safe_event("UNTRUSTED_" + secret, secret) == ("HANDLER_ERROR", "Worker handler failed")
    with store.users.sessions() as session:
        session.add(new_run_log(queued.run_id, alice, "2026-01-01T00:00:00Z", "SECRET",
                                "UNTRUSTED_" + secret, secret))
        session.add(new_run_log(queued.run_id, alice, "2026-01-01T00:00:01Z", "INFO",
                                "HTTP_RESPONSE_RECEIVED", "Received HTTP 503"))
        session.commit()
    page = store.get_logs(alice, queued.run_id, 0)
    assert [item.id for item in page.items] == sorted(item.id for item in page.items)
    assert [item.event for item in page.items] == ["RUN_QUEUED", "HANDLER_ERROR", "HTTP_RESPONSE_RECEIVED"]
    assert page.items[1].level == "ERROR"
    assert page.items[2].message == "Received HTTP 503"
    assert secret not in json.dumps(page.model_dump())
    newer = store.get_logs(alice, queued.run_id, page.items[0].id)
    assert newer.items == page.items[1:]
