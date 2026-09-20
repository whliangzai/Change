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


def test_quality_gate_rejects_missing_per_bar_availability_time() -> None:
    row = {
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

    with pytest.raises(DataQualityError) as captured:
        QualityGate().validate_daily_bars([row])

    assert captured.value.issues[0].field == "available_at"


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


def test_causal_provenance_requires_data_to_be_available_by_the_cutoff(tmp_path) -> None:
    available_at = datetime(2024, 1, 2, 18, 30, tzinfo=UTC)
    cutoff = datetime(2024, 1, 2, 19, 0, tzinfo=UTC)

    QualityGate().validate_batch_provenance(
        as_of_date=date(2024, 1, 2),
        available_at=available_at,
        information_cutoff_at=cutoff,
    )
    source = tmp_path / "causal.csv"
    source.write_text("symbol,trade_date\n600000.SH,2024-01-02\n", encoding="utf-8")
    AuthorizedFileImporter().import_file(
        source,
        available_at=available_at,
        information_cutoff_at=cutoff,
    )

    earlier_cutoff = datetime(2024, 1, 2, 18, 0, tzinfo=UTC)
    with pytest.raises(DataQualityError, match="not available by information cutoff"):
        QualityGate().validate_batch_provenance(
            as_of_date=date(2024, 1, 2),
            available_at=available_at,
            information_cutoff_at=earlier_cutoff,
        )
    with pytest.raises(ValueError, match="not available by information cutoff"):
        AuthorizedFileImporter().import_file(
            source,
            available_at=available_at,
            information_cutoff_at=earlier_cutoff,
        )


def test_quality_gate_rejects_an_empty_daily_bar_dataset() -> None:
    with pytest.raises(DataQualityError) as captured:
        QualityGate().validate_daily_bars([])

    assert captured.value.issues[0].field == "dataset"
