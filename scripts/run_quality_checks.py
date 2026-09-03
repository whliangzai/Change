"""Run the repository's quality checks with the shared project interpreter."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "complete_minimal_dataset"


def verify_manifest() -> None:
    manifest_path = FIXTURE_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = FIXTURE_ROOT / item["path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item["sha256"]:
            raise RuntimeError(
                f"fixture hash mismatch: {item['path']} expected={item['sha256']} actual={actual}"
            )
    print(f"fixture_manifest=verified files={len(manifest['files'])}")


def run_check(label: str, args: list[str]) -> int:
    command = [sys.executable, *args]
    print(f"\n== {label}: {' '.join(command)} ==")
    completed = subprocess.run(command, cwd=ROOT, check=False, text=True)
    print(f"{label.lower().replace(' ', '_')}_exit_code={completed.returncode}")
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pytest-args",
        nargs=argparse.REMAINDER,
        help="Arguments passed after the default pytest target list.",
    )
    args = parser.parse_args()

    try:
        verify_manifest()
    except (OSError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"fixture_manifest=BLOCKED {exc}")
        return 1

    with tempfile.TemporaryDirectory(prefix="money-quality-") as cache_dir:
        pytest_basetemp = str(Path(cache_dir) / "pytest")
        checks = [
            (
                "pytest",
                ["-m", "pytest", "tests", "--basetemp", pytest_basetemp] + (args.pytest_args or []),
            ),
            ("ruff", ["-m", "ruff", "check", "--no-cache", "app", "tests", "scripts"]),
            ("mypy", ["-m", "mypy", "--cache-dir", cache_dir, "app"]),
        ]
        exit_codes = [run_check(label, command) for label, command in checks]
    return max(exit_codes, default=0)


if __name__ == "__main__":
    raise SystemExit(main())
