"""Measured daily-end benchmark; no success is pre-recorded in this test."""

import csv
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from tests.support.daily_flow_contract import create_runner, request


def _write_benchmark_dataset(source: Path, rows: int = 200_000) -> None:
    source.mkdir(parents=True)
    bars = source / "bars.csv"
    with bars.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "trade_date",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "available_at",
                "status",
                "limit_up",
                "limit_down",
                "adjustment_factor",
                "industry",
            ]
        )
        available_at = datetime(2024, 1, 5, 18, tzinfo=UTC).isoformat()
        for index in range(rows):
            symbol = f"{index % 1000:06d}.SZ"
            writer.writerow(
                [
                    "2024-01-05",
                    symbol,
                    "10.00",
                    "10.20",
                    "9.90",
                    "10.10",
                    1000 + index,
                    available_at,
                    "ACTIVE",
                    "11.11",
                    "9.09",
                    "1.000000",
                    "TEST",
                ]
            )


def test_daily_end_reports_elapsed_time_and_peak_memory_for_200k_rows(tmp_path: Path, capsys) -> None:
    dataset = tmp_path / "benchmark-dataset"
    _write_benchmark_dataset(dataset)
    runner = create_runner(dataset)

    tracemalloc.start()
    started = perf_counter()
    runner.run(request())
    elapsed = perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"daily_budget rows=200000 elapsed_seconds={elapsed:.3f} peak_bytes={peak_bytes}")
    captured = capsys.readouterr().out
    assert "rows=200000" in captured
    assert "elapsed_seconds=" in captured
    assert "peak_bytes=" in captured
