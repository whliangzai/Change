from datetime import date

from app.jobs.idempotency import InMemoryJobRunStore, make_idempotency_key
from app.jobs.tasks import run_idempotent_task


def test_supported_job_kinds_have_stable_business_date_keys() -> None:
    keys = {
        make_idempotency_key(kind, date(2026, 9, 3))
        for kind in ("calendar", "data-quality", "daily-report", "backtest", "backup")
    }
    assert keys == {
        "calendar:2026-09-03",
        "data-quality:2026-09-03",
        "daily-report:2026-09-03",
        "backtest:2026-09-03",
        "backup:2026-09-03",
    }


def test_same_key_does_not_duplicate_side_effect() -> None:
    store = InMemoryJobRunStore()
    side_effects: list[str] = []

    def write_report() -> str:
        side_effects.append("report")
        return "report-1"

    run_idempotent_task("daily-report", date(2026, 9, 3), write_report, run_store=store)
    replay = run_idempotent_task(
        "daily-report", date(2026, 9, 3), write_report, run_store=store
    )

    assert replay.value == "report-1"
    assert replay.replayed is True
    assert side_effects == ["report"]
