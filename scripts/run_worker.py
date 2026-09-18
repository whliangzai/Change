"""Start the RQ worker from validated environment settings."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.errors import DependencyError
from app.jobs.queue import QueueSettings


def worker_command(settings: QueueSettings) -> list[str]:
    bin_dir = Path(sys.executable).resolve().parent
    candidate = bin_dir / ("rq.exe" if os.name == "nt" else "rq")
    configured = os.environ.get("RQ_EXECUTABLE", "").strip()
    executable = configured or (str(candidate) if candidate.exists() else shutil.which("rq"))
    if executable is None:
        raise DependencyError("RQ executable is not installed")
    redis_url = f"redis://{settings.host}:{settings.port}/{settings.db}"
    if settings.password:
        redis_url = (
            f"redis://:{quote(settings.password, safe='')}@"
            f"{settings.host}:{settings.port}/{settings.db}"
        )
    command = [
        executable,
        "worker",
        "--url",
        redis_url,
    ]
    if os.name == "nt":
        command.extend(["--worker-class", "rq.worker.SimpleWorker"])
    command.append(settings.queue_name)
    return command


def main() -> int:
    command = worker_command(QueueSettings.from_env())
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DependencyError as exc:
        print(f"DEPENDENCY_UNAVAILABLE: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
