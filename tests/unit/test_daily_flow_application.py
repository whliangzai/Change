from datetime import date

from app.application.daily_flow import DailyFlow


def test_daily_flow_keeps_plan_and_manual_execution_as_separate_stages() -> None:
    calls: list[str] = []

    def stage(name: str, value: object = None):
        def run(*args: object, **kwargs: object) -> object:
            calls.append(name)
            return value if value is not None else {"stage": name}

        return run

    result = DailyFlow(
        quality=stage("quality"),
        historical_pool=stage("historical_pool"),
        signals=stage("signals"),
        risk=stage("risk"),
        plan=stage("plan"),
        ledger=stage("ledger"),
        report=stage("report"),
        export=stage("export"),
    ).run(date(2026, 9, 3), account_id="account-1")

    assert calls == ["quality", "historical_pool", "signals", "risk", "plan", "ledger", "report", "export"]
    assert result["plan"] == {"stage": "plan"}
    assert result["manual_execution"] is True
