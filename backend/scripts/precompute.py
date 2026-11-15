"""Prepare everything the deployed app needs at start-up: trained models and cached results.

Used by the Docker build, so a fresh container starts with results ready:
    python scripts/precompute.py            # train/cache the models; compute missing result files
    python scripts/precompute.py --refresh  # recompute every result file (several minutes)

The result files are committed to the repository, so normally only the models are trained.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from causaltwin.config import (  # noqa: E402
    BENCHMARK_RESULTS_PATH,
    DRIFT_RESULTS_PATH,
    TRUE_GRAPH_PATH,
    VALIDATION_RESULTS_PATH,
)
from causaltwin.intelligence.training import train_models  # noqa: E402

# Each result file and the script that produces it (run from backend/).
PRODUCERS = [
    (TRUE_GRAPH_PATH, ["scripts/generate_samples.py"]),
    (VALIDATION_RESULTS_PATH, ["scripts/run_validation.py"]),
    (BENCHMARK_RESULTS_PATH, ["scripts/run_benchmark.py", "--drift"]),
    (DRIFT_RESULTS_PATH, ["scripts/run_benchmark.py", "--drift"]),
]


def main() -> None:
    """Train or load the models, then make sure every cached result file exists."""
    parser = argparse.ArgumentParser(description="Prepare trained models and cached results.")
    parser.add_argument("--refresh", action="store_true", help="recompute every result file")
    arguments = parser.parse_args()
    start = time.perf_counter()
    models = train_models()
    print(f"Models ready ({models.details.get('rows')} training rows) in {time.perf_counter() - start:.1f} s")
    already_run: set[str] = set()
    for path, command in PRODUCERS:
        key = " ".join(command)
        if (path.exists() and not arguments.refresh) or key in already_run:
            print(f"Using {path.name}")
            continue
        print(f"Computing {path.name} with: python {key}")
        subprocess.run([sys.executable, *command], cwd=BACKEND_DIR, check=True)
        already_run.add(key)
    missing = [path.name for path, _ in PRODUCERS if not path.exists()]
    if missing:
        raise SystemExit(f"Missing result files after precompute: {missing}")
    print(f"Precompute finished in {time.perf_counter() - start:.1f} s")


if __name__ == "__main__":
    main()
