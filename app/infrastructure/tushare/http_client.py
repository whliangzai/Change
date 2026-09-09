"""Bounded, retrying HTTP client for the Tushare Pro API."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Lock
from typing import Any

import httpx

from app.core.errors import DependencyError
from app.infrastructure.providers import RawResponseArchive
from app.infrastructure.tushare.contracts import DEFAULT_TUSHARE_CONTRACT, TushareContractError


class TushareDataError(ValueError):
    """A non-retryable provider response/contract error."""


class TushareHttpClient:
    API_URL = "https://api.tushare.pro"

    def __init__(
        self,
        token: str,
        *,
        base_url: str = API_URL,
        timeout_seconds: float = 20,
        max_codes_per_request: int = 200,
        max_concurrency: int = 2,
        rate_limit_per_minute: int = 120,
        raw_archive: RawResponseArchive | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not token:
            raise ValueError("Tushare token is required")
        if base_url.rstrip("/") != self.API_URL:
            raise ValueError("Tushare base URL must be https://api.tushare.pro")
        if (
            timeout_seconds <= 0
            or not 1 <= max_codes_per_request <= 5000
            or not 1 <= max_concurrency <= 32
            or rate_limit_per_minute < 1
        ):
            raise ValueError("invalid Tushare timeout, batching, concurrency, or rate limit")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_codes = max_codes_per_request
        self._max_concurrency = max_concurrency
        self._min_interval = 60 / rate_limit_per_minute
        self._last_request = 0.0
        self._lock = Lock()
        self._raw_archive = raw_archive
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def preflight(self) -> dict[str, Any]:
        """Return a credential-free declaration of the permissions checked by this run.

        Endpoint-level authorization is verified by the subsequent, archived requests;
        Tushare does not provide a separate permission-introspection endpoint.
        """
        return {
            "status": "configured",
            "provider": "tushare",
            "mapping_version": "v1",
            "required_datasets": sorted(DEFAULT_TUSHARE_CONTRACT.datasets),
        }

    def _throttle(self) -> None:
        with self._lock:
            delay = self._min_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()

    def _request(self, dataset: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        contract = DEFAULT_TUSHARE_CONTRACT.dataset(dataset)
        body = {
            "api_name": contract.api_name,
            "token": self._token,
            "params": dict(params),
            "fields": ",".join(contract.provider_fields),
        }
        response_body: Any = None
        response: httpx.Response | None = None
        for attempt in range(3):
            self._throttle()
            try:
                response = self._client.post(self._base_url, json=body, timeout=self._timeout)
            except httpx.TimeoutException as exc:
                if attempt == 2:
                    raise DependencyError("Tushare request timed out") from exc
                continue
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise DependencyError("Tushare request failed") from exc
                continue
            try:
                response_body = response.json()
            except ValueError:
                response_body = {"raw_body": response.text}
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise DependencyError(f"Tushare {dataset} service is unavailable")
                continue
            break
        assert response is not None
        if self._raw_archive is not None:
            self._raw_archive.archive(
                "tushare",
                dataset,
                {
                    "api_name": contract.api_name,
                    "params": params,
                    "fields": sorted(contract.provider_fields),
                },
                response.status_code,
                response_body,
                mapping_version="v1",
            )
        if response.status_code in {401, 403}:
            raise DependencyError("Tushare authentication or permission was rejected")
        if response.status_code >= 400:
            raise DependencyError(f"Tushare {dataset} request was rejected")
        if not isinstance(response_body, Mapping):
            raise TushareDataError(f"Tushare {dataset} response is malformed")
        if response_body.get("code") not in (None, 0):
            message = str(response_body.get("msg") or "provider returned an error")
            if "权限" in message or "permission" in message.lower() or "积分" in message:
                raise DependencyError("Tushare permission preflight failed")
            raise TushareDataError(f"Tushare {dataset}: {message}")
        data = response_body.get("data")
        if not isinstance(data, Mapping):
            raise TushareDataError(f"Tushare {dataset} response contains no data")
        fields, items = data.get("fields"), data.get("items")
        if not isinstance(fields, list) or not isinstance(items, list):
            raise TushareDataError(f"Tushare {dataset} response contains malformed rows")
        rows = [
            dict(zip((str(field) for field in fields), item, strict=False))
            for item in items
            if isinstance(item, list)
        ]
        if not rows and not contract.allow_empty:
            raise TushareDataError(f"Tushare {dataset} response contains no data")
        try:
            for row in rows:
                DEFAULT_TUSHARE_CONTRACT.validate_payload(dataset, row)
        except TushareContractError as exc:
            raise TushareDataError(str(exc)) from exc
        return rows

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
        ] or [[]]

        def fetch(chunk: list[str]) -> list[dict[str, Any]]:
            params = dict(extra or {})
            if chunk:
                params["ts_code"] = ",".join(chunk)
            if start_date is not None:
                params["start_date"] = str(start_date).replace("-", "")
            if end_date is not None:
                params["end_date"] = str(end_date).replace("-", "")
            return self._request(dataset, params)

        with ThreadPoolExecutor(max_workers=self._max_concurrency) as executor:
            pages = list(executor.map(fetch, chunks))
        return [row for page in pages for row in page]


__all__ = ["TushareDataError", "TushareHttpClient"]
