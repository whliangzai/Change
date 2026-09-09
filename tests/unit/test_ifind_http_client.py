from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from app.core.errors import DependencyError
from app.infrastructure.ifind.http_client import IFindDataError, IFindHttpClient
from app.infrastructure.ifind.raw_archive import IFindRawArchive
from app.infrastructure.storage import ParquetStore


def _daily(code: str) -> dict[str, object]:
    return {
        "tradeDate": "2025-01-02",
        "thscode": code,
        "open": 10,
        "high": 11,
        "low": 9,
        "close": 10,
        "volume": 100,
        "amount": 1000,
    }


def _client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    return IFindHttpClient(
        refresh_token="refresh-secret",
        client=httpx.Client(transport=transport),
        **kwargs,
    )


def test_refreshes_access_token_and_caches_it() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("get_access_token"):
            assert "refresh-secret" in request.read().decode()
            return httpx.Response(200, json={"access_token": "access-secret", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer access-secret"
        return httpx.Response(200, json={"data": [_daily("600000.SH")]})

    client = _client(handler)
    assert client.request("daily_bars", {"thscode": ["600000.SH"]}) == [_daily("600000.SH")]
    assert client.request("daily_bars", {"thscode": ["600000.SH"]}) == [_daily("600000.SH")]
    assert calls.count("/api/v1/get_access_token") == 1


def test_retries_once_after_401_then_succeeds() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("get_access_token"):
            attempts += 1
            return httpx.Response(200, json={"access_token": f"access-{attempts}", "expires_in": 3600})
        if attempts == 1:
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"data": [_daily("600000.SH")]})

    client = _client(handler)
    assert client.request("daily_bars", {"thscode": ["600000.SH"]})
    assert attempts == 2


def test_second_401_is_dependency_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("get_access_token"):
            return httpx.Response(200, json={"access_token": "access-secret", "expires_in": 3600})
        return httpx.Response(401, json={"message": "expired"})

    with pytest.raises(DependencyError):
        _client(handler).request("daily_bars", {"thscode": ["600000.SH"]})


def test_non_official_host_is_rejected() -> None:
    with pytest.raises(ValueError, match="quantapi.51ifind.com"):
        IFindHttpClient("refresh-secret", base_url="https://example.test")


def test_chunking_respects_max_codes_and_bounded_workers() -> None:
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("get_access_token"):
            return httpx.Response(200, json={"access_token": "access-secret", "expires_in": 3600})
        body = json.loads(request.read())
        seen.append(body["thscode"])
        return httpx.Response(200, json={"data": [_daily(code) for code in body["thscode"]]})

    client = _client(handler, max_codes_per_request=2, max_concurrency=2)
    rows = client.fetch_daily_bars(["A", "B", "C", "D", "E"], date(2025, 1, 2), date(2025, 1, 2))
    assert len(rows) == 5
    assert sorted(len(chunk) for chunk in seen) == [1, 2, 2]


def test_timeout_and_server_error_are_dependency_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("get_access_token"):
            return httpx.Response(200, json={"access_token": "access-secret", "expires_in": 3600})
        return httpx.Response(503, json={"message": "down"})

    with pytest.raises(DependencyError):
        _client(handler).request("daily_bars", {"thscode": ["600000.SH"]})


def test_rate_limit_and_timeout_are_dependency_errors() -> None:
    def rate_limited(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("get_access_token"):
            return httpx.Response(200, json={"access_token": "access-secret", "expires_in": 3600})
        return httpx.Response(429, json={"message": "slow down"})

    with pytest.raises(DependencyError):
        _client(rate_limited).request("daily_bars", {"thscode": ["600000.SH"]})

    def timed_out(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("get_access_token"):
            return httpx.Response(200, json={"access_token": "access-secret", "expires_in": 3600})
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(DependencyError):
        _client(timed_out).request("daily_bars", {"thscode": ["600000.SH"]})


def test_empty_or_malformed_data_is_non_retryable_data_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("get_access_token"):
            return httpx.Response(200, json={"access_token": "access-secret", "expires_in": 3600})
        return httpx.Response(200, json={"data": []})

    with pytest.raises(IFindDataError):
        _client(handler).request("daily_bars", {"thscode": ["600000.SH"]})


def test_raw_archive_redacts_credentials_and_records_hash(tmp_path) -> None:
    archive = IFindRawArchive(tmp_path / "data", secret_values=["refresh-secret", "access-secret"])
    manifest = archive.archive(
        "daily_bars",
        {"thscode": ["600000.SH"], "refresh_token": "refresh-secret"},
        200,
        {"data": [{"close": 10}], "access_token": "access-secret"},
        mapping_version="v1",
    )
    raw = ParquetStore(tmp_path / "data").read_records(manifest.manifest_path)[0]
    assert "refresh-secret" not in json.dumps(raw)
    assert "access-secret" not in json.dumps(raw)
    assert raw["response_hash"]
    assert raw["mapping_version"] == "v1"
