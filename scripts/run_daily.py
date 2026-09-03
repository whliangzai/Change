"""Safe daily-report command shell; report generation is injected by the application runtime."""

from __future__ import annotations

import argparse
import os
from datetime import date


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue or preview a daily report")
    parser.add_argument(
        "--business-date", default=os.getenv("BUSINESS_DATE", date.today().isoformat())
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(f"dry-run: daily report for {args.business_date}")
        return 0
    print(
        "daily command requires an injected application service; manual confirmation remains required"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
