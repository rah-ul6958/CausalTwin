"""Container health check: exit 0 when GET /health answers "ok" on the configured port."""

import json
import os
import sys
import urllib.request

HEALTH_TIMEOUT_SECONDS = 4


def main() -> int:
    """Return the process exit code (0 healthy, 1 not)."""
    port = os.environ.get("PORT", "8000")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=HEALTH_TIMEOUT_SECONDS) as response:
            return 0 if json.load(response).get("status") == "ok" else 1
    except (OSError, ValueError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
