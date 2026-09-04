from datetime import date
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from scripts import restore_check
from scripts.backup import create_backup
from scripts.restore_check import restore_readiness


def test_restore_readiness_reports_unverified_runtime_components(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "artifact.txt").write_text("immutable", encoding="utf-8")
    manifest = create_backup(source, tmp_path / "backups", backup_id="test-backup")

    readiness = restore_readiness(
        tmp_path / "backups" / manifest.backup_id / "backup-manifest.json"
    )

    assert readiness["file_hashes"] == "VERIFIED"
    assert readiness["database_migrations"] == "NOT_CONFIGURED"
    assert readiness["ledger_reconciliation"] == "NOT_CHECKED"
    assert readiness["run_reproducibility"] == "NOT_CHECKED"
    assert readiness["ready"] is False


def test_restore_readiness_verifies_nonnegative_reconciled_snapshots(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "artifact.txt").write_text("immutable", encoding="utf-8")
    manifest = create_backup(source, tmp_path / "backups", backup_id="ledger-backup")
    database_url = f"sqlite:///{tmp_path / 'ledger.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            models.PortfolioSnapshot(
                id=uuid4(),
                run_id=uuid4(),
                account_id=uuid4(),
                trade_date=date(2026, 9, 3),
                cash="19000.00",
                market_value="1000.00",
                equity="20000.00",
                high_watermark="20000.00",
                drawdown="0.00",
                risk_state="NORMAL",
            )
        )
        session.commit()
    monkeypatch.setattr(restore_check, "_database_migrations", lambda _: "VERIFIED")

    readiness = restore_readiness(
        tmp_path / "backups" / manifest.backup_id / "backup-manifest.json", database_url
    )

    assert readiness["ledger_reconciliation"] == "VERIFIED"
    assert readiness["ready"] is False
