"""AKShare validation-only adapter."""

from app.infrastructure.akshare.validation import AKShareValidationClient, compare_ohlcv

__all__ = ["AKShareValidationClient", "compare_ohlcv"]
