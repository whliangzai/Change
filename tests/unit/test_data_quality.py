from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.data.importer import AuthorizedFileImporter, canonical_content_hash
from app.domain.data.quality import DataQualityError, QualityGate


def test_quality_gate_blocks_duplicate_security_date_and_reports_field() -> None:
    complete = {
        "symbol": "600000.SH",
        "trade_date": date(2024, 1, 2),
        "raw_open": "10",
        "raw_high": "10",
        "raw_low": "10",
        "raw_close": "10",
        "adjusted_open": "10",
        "adjusted_high": "10",
        "adjusted_low": "10",
        "adjusted_close": "10",
        "volume": "2500000",
        "amount": "25000000",
        "adjust_factor": "1",
    }
    rows = [complete, {**complete, "raw_close": None}]

    with pytest.raises(DataQualityError) as captured:
        QualityGate().validate_daily_bars(rows)

    assert captured.value.blocking is True
    assert {issue.field for issue in captured.value.issues} >= {"raw_close", "security/date"}
    assert all(issue.symbol == "600000.SH" for issue in captured.value.issues)


def test_quality_gate_rejects_zero_price() -> None:
    row = {
        "symbol": "600000.SH",
        "trade_date": date(2024, 1, 2),
        "raw_open": "0",
        "raw_high": "10",
        "raw_low": "10",
        "raw_close": "10",
        "adjusted_open": "10",
        "adjusted_high": "10",
        "adjusted_low": "10",
        "adjusted_close": "10",
        "volume": "0",
        "amount": "0",
        "adjust_factor": "1",
    }

    with pytest.raises(DataQualityError) as captured:
        QualityGate().validate_daily_bars([row])

    assert any(issue.field == "raw_open" for issue in captured.value.issues)


def test_importer_hash_is_canonical_across_column_order_and_decimal_format(tmp_path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("symbol,trade_date,amount\n600000.SH,2024-01-02,10.00\n", encoding="utf-8")
    second.write_text("amount,trade_date,symbol\n10.000,2024-01-02,600000.SH\n", encoding="utf-8")

    importer = AuthorizedFileImporter()
    assert importer.import_file(first).content_hash == importer.import_file(second).content_hash
    assert canonical_content_hash(
        [{"trade_date": date(2024, 1, 2), "amount": Decimal("10.00"), "empty": None}]
    ) == canonical_content_hash([{"empty": None, "amount": 10.0, "trade_date": "2024-01-02"}])


def test_provenance_reports_timezone_issues_without_comparing_naive_and_aware() -> None:
    with pytest.raises(DataQualityError) as captured:
        QualityGate().validate_batch_provenance(
            as_of_date=date(2024, 1, 2),
            available_at=datetime(2024, 1, 2),
            information_cutoff_at=datetime(2024, 1, 2, tzinfo=UTC),
        )

    assert {issue.field for issue in captured.value.issues} == {"available_at"}
