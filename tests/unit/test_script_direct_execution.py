from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def _direct_script_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name == "DATABASE_URL" or name.startswith(("BACKTEST_", "DAILY_")):
            environment.pop(name)
    return environment


def test_daily_script_runs_directly_from_repository_root() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/run_daily.py", "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_direct_script_environment(),
    )

    assert completed.returncode == 0, completed.stderr
    assert "dry-run: daily report" in completed.stdout
    assert "ModuleNotFoundError" not in completed.stderr


def test_backtest_script_reaches_configuration_gate_when_run_directly(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'inherited-parent.db'}")
    monkeypatch.setenv("BACKTEST_OWNER_ID", str(uuid4()))
    monkeypatch.setenv("BACKTEST_DATA_BATCH_ID", str(uuid4()))
    monkeypatch.setenv("BACKTEST_STRATEGY_VERSION_ID", str(uuid4()))
    monkeypatch.setenv("BACKTEST_START_DATE", "2026-09-01")
    monkeypatch.setenv("BACKTEST_TRAIN_END", "2026-09-01")
    monkeypatch.setenv("BACKTEST_VALID_END", "2026-09-02")
    monkeypatch.setenv("BACKTEST_OOS_START", "2026-09-03")
    monkeypatch.setenv("BACKTEST_END_DATE", "2026-09-03")
    completed = subprocess.run(
        [sys.executable, "scripts/run_backtest.py", "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_direct_script_environment(),
    )

    assert completed.returncode == 2, completed.stderr
    assert "BACKTEST_OWNER_ID" in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr


def test_readme_documents_direct_commands_and_synthetic_sample() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "python scripts/run_daily.py" in readme
    assert "python scripts/run_backtest.py" in readme
    assert "tests/fixtures/authorized_simulated_daily_bars.csv" in readme
    assert "PostgreSQL" in readme
    assert "Redis/RQ" in readme
