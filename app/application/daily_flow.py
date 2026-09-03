"""Daily research workflow composed from replaceable foundation services."""

from collections.abc import Callable
from datetime import date

Stage = Callable[..., object]


class DailyFlow:
    """Run research and accounting stages without submitting broker orders."""

    def __init__(
        self,
        *,
        quality: Stage,
        historical_pool: Stage,
        signals: Stage,
        risk: Stage,
        plan: Stage,
        ledger: Stage,
        report: Stage,
        export: Stage,
        ingest: Stage | None = None,
    ) -> None:
        self.ingest = ingest
        self.quality = quality
        self.historical_pool = historical_pool
        self.signals = signals
        self.risk = risk
        self.plan = plan
        self.ledger = ledger
        self.report = report
        self.export = export

    def run(self, trade_date: date, *, account_id: str) -> dict[str, object]:
        context: dict[str, object] = {"trade_date": trade_date.isoformat(), "account_id": account_id}
        if self.ingest is not None:
            context["ingest"] = self.ingest(trade_date, account_id, context)
        for name, stage in (
            ("quality", self.quality),
            ("historical_pool", self.historical_pool),
            ("signals", self.signals),
            ("risk", self.risk),
            ("plan", self.plan),
            ("ledger", self.ledger),
            ("report", self.report),
            ("export", self.export),
        ):
            context[name] = stage(trade_date, account_id, context)
        context["manual_execution"] = True
        context["broker_submission"] = False
        return context
