from decimal import Decimal

from app.infrastructure.akshare import compare_ohlcv


def test_akshare_differences_are_warnings_and_do_not_mutate_primary() -> None:
    primary = [
        {
            "symbol": "000001.SZ",
            "trade_date": "2025-01-02",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "volume": 100,
            "amount": 1000,
        }
    ]
    warnings = compare_ohlcv(primary, [{**primary[0], "close": 11}], price_tolerance=Decimal("0"))
    assert warnings[0]["kind"] == "AKSHARE_DIFFERENCE"
    assert primary[0]["close"] == 10
