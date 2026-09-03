from datetime import date, timedelta

from app.domain.data.universe import HistoricalUniverseSelector


def test_historical_universe_returns_eligibility_and_explicit_exclusion_reason() -> None:
    as_of = date(2024, 4, 1)
    trade_dates = [as_of - timedelta(days=offset) for offset in range(60)][::-1]
    symbols = ["GOOD.SH", "ST.SH", "SUSP.SH", "GEM.SZ", "NEW.SH", "THIN.SH"]
    securities = [
        {
            "symbol": symbol,
            "exchange": "SH" if symbol.endswith("SH") else "SZ",
            "board": "MAIN",
            "security_type": "COMMON",
            "list_date": trade_dates[0],
        }
        for symbol in symbols
    ]
    securities[3]["board"] = "GEM"
    securities[4]["list_date"] = trade_dates[-2]
    statuses = {
        "ST.SH": {"is_st": True},
        "SUSP.SH": {"is_suspended": True},
    }
    bars = [
        {
            "symbol": symbol,
            "trade_date": trade_date,
            "amount": "25000000" if symbol != "THIN.SH" else "1000000",
        }
        for symbol in symbols
        for trade_date in trade_dates
    ]

    decisions = HistoricalUniverseSelector().select(
        as_of_date=as_of,
        securities=securities,
        status_history=statuses,
        daily_bars=bars,
        open_trade_dates=trade_dates,
    )

    by_symbol = {decision.symbol: decision for decision in decisions}
    assert by_symbol["GOOD.SH"].eligible is True
    assert by_symbol["ST.SH"].reason == "st"
    assert by_symbol["SUSP.SH"].reason == "suspended"
    assert by_symbol["GEM.SZ"].reason == "non_mainboard_common_stock"
    assert by_symbol["NEW.SH"].reason == "listed_less_than_60_trading_days"
    assert by_symbol["THIN.SH"].reason == "median_amount_below_20000000"


def test_historical_universe_uses_point_in_time_board_and_available_rows() -> None:
    as_of = date(2024, 4, 1)
    trade_dates = [as_of - timedelta(days=offset) for offset in range(60)][::-1]
    securities = [
        {
            "symbol": symbol,
            "exchange": "SH",
            "board": "MAIN",
            "security_type": "COMMON",
            "list_date": trade_dates[0],
        }
        for symbol in ("BOARD.SH", "FUTURE.SH", "UNAVAILABLE.SH")
    ]
    securities[2]["available_at"] = as_of + timedelta(days=1)
    statuses = [
        {"symbol": "BOARD.SH", "effective_date": as_of, "board": "GEM"},
        {"symbol": "FUTURE.SH", "effective_date": as_of + timedelta(days=1), "is_st": True},
        {
            "symbol": "UNAVAILABLE.SH",
            "effective_date": as_of,
            "is_st": True,
            "available_at": as_of + timedelta(days=1),
        },
    ]
    bars = [
        {"symbol": symbol, "trade_date": trade_date, "amount": "25000000"}
        for symbol in ("BOARD.SH", "FUTURE.SH", "UNAVAILABLE.SH")
        for trade_date in trade_dates
    ]

    decisions = HistoricalUniverseSelector().select(
        as_of_date=as_of,
        securities=securities,
        status_history=statuses,
        daily_bars=bars,
        open_trade_dates=trade_dates,
    )

    by_symbol = {decision.symbol: decision for decision in decisions}
    assert by_symbol["BOARD.SH"].reason == "non_mainboard_common_stock"
    assert by_symbol["FUTURE.SH"].eligible is True
    assert by_symbol["UNAVAILABLE.SH"].reason == "data_not_available_at_as_of"
