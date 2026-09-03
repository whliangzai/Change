"""Safe backtest command shell; the engine is injected by the application runtime."""

from __future__ import annotations

import argparse
import os
from datetime import date


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue or preview a backtest")
    parser.add_argument(
        "--business-date", default=os.getenv("BUSINESS_DATE", date.today().isoformat())
    )
    parser.add_argument("--scope", default=os.getenv("BACKTEST_SCOPE", "default"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(f"dry-run: backtest {args.scope} for {args.business_date}")
        return 0
    print("backtest command requires an injected application service; no broker network is used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
