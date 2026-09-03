from datetime import date

import pytest

from app.domain.data.quality import DataQualityError, QualityGate


def test_quality_gate_blocks_duplicate_security_date_and_reports_field() -> None:
    complete = {
        "symbol": "600000.SH", "trade_date": date(2024, 1, 2),
        "raw_open": "10", "raw_high": "10", "raw_low": "10", "raw_close": "10",
        "adjusted_open": "10", "adjusted_high": "10", "adjusted_low": "10", "adjusted_close": "10",
        "volume": "2500000", "amount": "25000000", "adjust_factor": "1",
    }
    rows = [complete, {**complete, "raw_close": None}]

    with pytest.raises(DataQualityError) as captured:
        QualityGate().validate_daily_bars(rows)

    assert captured.value.blocking is True
    assert {issue.field for issue in captured.value.issues} >= {"raw_close", "security/date"}
    assert all(issue.symbol == "600000.SH" for issue in captured.value.issues)
