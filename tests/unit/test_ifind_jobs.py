from datetime import date

from app.core.contracts import InMemoryAuditWriter
from app.jobs.idempotency import InMemoryJobRunStore
from app.jobs.tasks import import_ifind_data


def test_ifind_job_uses_scope_qualified_stable_key_and_replays() -> None:
    calls: list[tuple[date, str]] = []
    store = InMemoryJobRunStore()
    audit = InMemoryAuditWriter()

    def service(*, business_date: date, scope: str) -> dict[str, str]:
        calls.append((business_date, scope))
        return {"scope": scope}

    first = import_ifind_data(
        date(2025, 1, 2), service, scope="pilot", run_store=store, audit_writer=audit
    )
    replay = import_ifind_data(
        date(2025, 1, 2), service, scope="pilot", run_store=store, audit_writer=audit
    )

    assert first.idempotency_key == "data-import:2025-01-02:ifind-pilot"
    assert replay.replayed is True
    assert calls == [(date(2025, 1, 2), "pilot")]


def test_provider_worker_claims_a_queued_record_before_running() -> None:
    store = InMemoryJobRunStore()
    store.reserve_queued(
        "data-import",
        date(2025, 1, 2),
        "data-import:2025-01-02:ifind-pilot",
        phase="provider-import",
        value={"provider": "ifind", "scope": "pilot"},
    )

    result = import_ifind_data(
        date(2025, 1, 2),
        lambda *, business_date, scope: {
            "business_date": business_date.isoformat(),
            "scope": scope,
        },
        scope="pilot",
        run_store=store,
    )

    assert result.status == "succeeded"
    assert store.latest("data-import:2025-01-02:ifind-pilot").status == "succeeded"
    assert store.latest("data-import:2025-01-02:ifind-pilot").phase == "provider-import"
