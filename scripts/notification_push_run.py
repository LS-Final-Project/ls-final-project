#!/usr/bin/env python3
"""KAN-201 Job CLI. Operational use only; never invoke from an HTTP request."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.api.platform.notifications.push_worker import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
