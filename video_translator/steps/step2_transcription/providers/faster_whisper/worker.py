"""Compatibility entry point for the Step 1.5 Whisper worker."""

from .stable_worker import main, run

__all__ = ["main", "run"]

if __name__ == "__main__":
    raise SystemExit(main())
