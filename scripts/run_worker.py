"""Start the RQ worker from validated environment settings."""

from __future__ import annotations

import shutil
import subprocess
import sys

from app.core.errors import DependencyError
from app.jobs.queue import QueueSettings


def worker_command(settings: QueueSettings) -> list[str]:
    executable = shutil.which("rq")
    if executable is None:
        raise DependencyError("RQ executable is not installed")
    return [
        executable,
        "worker",
        "--url",
        f"redis://{settings.host}:{settings.port}/{settings.db}",
        settings.queue_name,
    ]


def main() -> int:
    command = worker_command(QueueSettings.from_env())
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DependencyError as exc:
        print(f"DEPENDENCY_UNAVAILABLE: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
