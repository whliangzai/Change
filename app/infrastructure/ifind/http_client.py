"""Bounded HTTP adapter for the official iFinD Quant API."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.errors import DependencyError
from app.infrastructure.ifind.contracts import DEFAULT_IFIND_CONTRACT, IFindContractError
from app.infrastructure.ifind.raw_archive import IFindRawArchive


class IFindDataError(ValueError):
    """Raised for a successful but empty or malformed supplier response."""


class IFindHttpClient:
    """Synchronous iFinD client with in-memory access-token caching.

    The transport is injectable so recordings and failure paths can be tested without
    reaching the supplier. Credentials are never included in exception messages.
    """

    TOKEN_PATH = "/api/v1/get_access_token"
    OFFICIAL_HOST = "quantapi.51ifind.com"

    def __init__(
        self,
        refresh_token: str,
        *,
        base_url: str = "https://quantapi.51ifind.com",
        timeout: float = 30.0,
        max_codes_per_request: int = 200,
        max_concurrency: int = 2,
        client: httpx.Client | None = None,
        raw_archive: IFindRawArchive | None = None,
    ) -> None:
        if not refresh_token or not refresh_token.strip():
            raise ValueError("refresh_token is required")
        parsed = urlparse(base_url.rstrip("/"))
        if parsed.scheme != "https" or parsed.hostname != self.OFFICIAL_HOST:
            raise ValueError(f"base_url must use https://{self.OFFICIAL_HOST}")
        if max_codes_per_request < 1 or max_codes_per_request > 200:
            raise ValueError("max_codes_per_request must be between 1 and 200")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self._refresh_token = refresh_token.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_codes = max_codes_per_request
        self._max_concurrency = max_concurrency
        self._client = client or httpx.Client(timeout=timeout)
        self._raw_archive = raw_archive
        self._token: str | None = None
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> IFindHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _access_token(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            if (
                self._token is not None
                and not force_refresh
                and time.monotonic() < self._token_expiry
            ):
                return self._token
            try:
                response = self._client.post(
                    f"{self._base_url}{self.TOKEN_PATH}",
                    json={"refresh_token": self._refresh_token},
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                raise DependencyError("iFinD token request timed out") from exc
            except httpx.HTTPError as exc:
                raise DependencyError("iFinD token request failed") from exc
            if response.status_code >= 500 or response.status_code == 429:
                raise DependencyError("iFinD token service is unavailable")
            if response.status_code >= 400:
                raise DependencyError("iFinD authentication failed")
            try:
                body = response.json()
                token = body.get("access_token") or body.get("data", {}).get("access_token")
            except (ValueError, AttributeError, TypeError) as exc:
                raise IFindDataError("iFinD token response is malformed") from exc
            if not isinstance(token, str) or not token:
                raise IFindDataError("iFinD token response has no access token")
            expires_in = body.get("expires_in", 300) if isinstance(body, dict) else 300
            try:
                ttl = max(1.0, float(expires_in) - 30.0)
            except (TypeError, ValueError):
                ttl = 300.0
            self._token = token
            self._token_expiry = time.monotonic() + ttl
            return token

    def _send(self, endpoint: str, payload: Mapping[str, Any], token: str) -> httpx.Response:
        try:
            return self._client.post(
                f"{self._base_url}{endpoint}",
                json=dict(payload),
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise DependencyError("iFinD request timed out") from exc
        except httpx.HTTPError as exc:
            raise DependencyError("iFinD request failed") from exc

    def request(self, dataset: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Fetch one supplier page and return normalized record-shaped dictionaries."""
        contract = DEFAULT_IFIND_CONTRACT.dataset(dataset)
        token = self._access_token()
        response = self._send(contract.endpoint, payload, token)
        if response.status_code == 401:
            self._token = None
            token = self._access_token(force_refresh=True)
            response = self._send(contract.endpoint, payload, token)
            if response.status_code == 401:
                raise DependencyError("iFinD authentication was rejected")
        try:
            response_body = response.json()
        except ValueError:
            response_body = {"raw_body": response.text}
        if self._raw_archive is not None:
            self._raw_archive.archive(
                dataset,
                payload,
                response.status_code,
                response_body,
                mapping_version="v1",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise DependencyError(f"iFinD {dataset} service is unavailable")
        if response.status_code >= 400:
            raise DependencyError(f"iFinD {dataset} request was rejected")
        body = response_body
        rows = body.get("data") if isinstance(body, dict) else None
        if isinstance(rows, dict):
            rows = rows.get("items") or rows.get("tables")
        if rows is None and isinstance(body, dict):
            rows = body.get("tables")
        if not isinstance(rows, list) or not rows:
            raise IFindDataError(f"iFinD {dataset} response contains no data")
        if not all(isinstance(row, dict) for row in rows):
            raise IFindDataError(f"iFinD {dataset} response has malformed rows")
        try:
            for row in rows:
                DEFAULT_IFIND_CONTRACT.validate_payload(dataset, row)
        except IFindContractError as exc:
            raise IFindDataError(str(exc)) from exc
        return [dict(row) for row in rows]

    def fetch_dataset(
        self,
        dataset: str,
        codes: Iterable[str] | None = None,
        *,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        code_list = list(codes or [])
        chunks = [
            code_list[i : i + self._max_codes] for i in range(0, len(code_list), self._max_codes)
        ]
        if not chunks:
            chunks = [[]]

        def fetch(chunk: list[str]) -> list[dict[str, Any]]:
            payload: dict[str, Any] = dict(extra or {})
            if chunk:
                payload["thscode"] = chunk
            if start_date is not None:
                payload["startdate"] = (
                    start_date.isoformat() if isinstance(start_date, date) else start_date
                )
            if end_date is not None:
                payload["enddate"] = (
                    end_date.isoformat() if isinstance(end_date, date) else end_date
                )
            return self.request(dataset, payload)

        with ThreadPoolExecutor(max_workers=self._max_concurrency) as executor:
            pages = list(executor.map(fetch, chunks))
        return [row for page in pages for row in page]

    def fetch_daily_bars(
        self, codes: Iterable[str], start_date: date | str, end_date: date | str
    ) -> list[dict[str, Any]]:
        return self.fetch_dataset("daily_bars", codes, start_date=start_date, end_date=end_date)

    def fetch_adjustment_factors(
        self, codes: Iterable[str], start_date: date | str, end_date: date | str
    ) -> list[dict[str, Any]]:
        return self.fetch_dataset(
            "adjustment_factors", codes, start_date=start_date, end_date=end_date
        )

    def fetch_trading_calendar(
        self, start_date: date | str, end_date: date | str
    ) -> list[dict[str, Any]]:
        return self.fetch_dataset("trading_calendar", start_date=start_date, end_date=end_date)


__all__ = ["IFindDataError", "IFindHttpClient"]
