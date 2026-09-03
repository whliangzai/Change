"""High-water-mark drawdown state machine with explicit recovery confirmations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class DrawdownState(StrEnum):
    NORMAL = "NORMAL"
    STOP_NEW = "STOP_NEW"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class DrawdownAuditEvent:
    action: str
    drawdown: Decimal
    confirmation_id: str | None = None


class DrawdownMonitor:
    def __init__(
        self,
        *,
        initial_equity: Decimal = Decimal("20000"),
        warning_threshold: Decimal = Decimal("0.06"),
        stop_threshold: Decimal = Decimal("0.08"),
    ) -> None:
        if not Decimal("0") < warning_threshold < stop_threshold < Decimal("1"):
            raise ValueError("drawdown thresholds must be ordered between zero and one")
        self.high_water_mark = initial_equity
        self.warning_threshold = warning_threshold
        self.stop_threshold = stop_threshold
        self.state = DrawdownState.NORMAL
        self._drawdown = Decimal("0")
        self._confirmations: list[str] = []
        self.audit_events: list[DrawdownAuditEvent] = []

    @property
    def drawdown(self) -> Decimal:
        return self._drawdown

    def observe(self, equity: Decimal) -> DrawdownState:
        if equity > self.high_water_mark:
            self.high_water_mark = equity
        current_drawdown = (self.high_water_mark - equity) / self.high_water_mark
        self._drawdown = current_drawdown
        if self.state == DrawdownState.REVIEW_REQUIRED:
            return self.state
        if current_drawdown >= self.stop_threshold:
            self.audit_events.append(DrawdownAuditEvent("DRAWDOWN_REVIEW_REQUIRED", current_drawdown))
            self.state = DrawdownState.REVIEW_REQUIRED
        elif self.state == DrawdownState.STOP_NEW:
            return self.state
        elif current_drawdown >= self.warning_threshold:
            if self.state == DrawdownState.NORMAL:
                self.audit_events.append(DrawdownAuditEvent("DRAWDOWN_STOP_NEW", current_drawdown))
            self.state = DrawdownState.STOP_NEW
        return self.state

    def confirm_recovery(
        self,
        confirmation_id: str,
        *,
        authorized: bool = False,
        checklist_complete: bool = False,
        equity: Decimal | None = None,
    ) -> bool:
        recovery_allowed = (
            authorized
            and checklist_complete
            and equity is not None
            and equity > self.high_water_mark * (Decimal("1") - self.warning_threshold)
        )
        if (
            self.state != DrawdownState.REVIEW_REQUIRED
            or confirmation_id in self._confirmations
            or not recovery_allowed
        ):
            return False
        self._confirmations.append(confirmation_id)
        if len(self._confirmations) < 2:
            return False
        self.state = DrawdownState.NORMAL
        self.audit_events.append(DrawdownAuditEvent("DRAWDOWN_RECOVERY_CONFIRMED", Decimal("0"), confirmation_id))
        self._confirmations.clear()
        return True
