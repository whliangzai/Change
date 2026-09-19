from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import DependencyError
from app.infrastructure.tushare.http_client import TushareDataError, TushareHttpClient


def _client(handler):
    return TushareHttpClient(
        "private-token",
        rate_limit_per_minute=10_000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_tushare_client_maps_records_and_does_not_put_token_in_preflight() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["token"] == "private-token"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": [
                        "ts_code",
                        "trade_date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "vol",
                        "amount",
                    ],
                    "items": [["000001.SZ", "20250102", 10, 11, 9, 10, 100, 1000]],
                },
            },
        )

    client = _client(handler)
    rows = client.fetch_dataset(
        "daily_bars", ["000001.SZ"], start_date="2025-01-02", end_date="2025-01-02"
    )
    assert rows[0]["ts_code"] == "000001.SZ"
    assert "private-token" not in json.dumps(client.preflight())


def test_tushare_client_uses_documented_multi_symbol_filter_for_index_master() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        assert body["api_name"] == "index_basic"
        assert body["params"] == {"symbol": "000001,000300"}
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": ["ts_code", "list_date", "market"],
                    "items": [
                        ["000001.SH", "19910715", "SSE"],
                        ["000300.SH", "20050408", "SSE"],
                    ],
                },
            },
        )

    rows = _client(handler).fetch_dataset("index_master", ["000300.SH", "000001.SH"])

    assert {row["ts_code"] for row in rows} == {"000001.SH", "000300.SH"}


def test_tushare_client_classifies_permission_and_empty_data() -> None:
    permission = _client(
        lambda _request: httpx.Response(200, json={"code": -2001, "msg": "权限不足"})
    )
    with pytest.raises(DependencyError, match="permission"):
        permission.fetch_dataset("daily_bars", ["000001.SZ"])
    empty = _client(
        lambda _request: httpx.Response(200, json={"code": 0, "data": {"fields": [], "items": []}})
    )
    with pytest.raises(TushareDataError, match="no data"):
        empty.fetch_dataset("daily_bars", ["000001.SZ"])
    assert empty.fetch_dataset("security_master") == []
