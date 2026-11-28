"""Run the AI4I 2020 check and write data/ai4i_results.json (about a minute).

    python scripts/run_ai4i.py

Dataset: AI4I 2020 Predictive Maintenance Dataset (UCI, CC BY 4.0, DOI 10.24432/C5HS5C), in
data/external/. See causaltwin/intelligence/ai4i.py for what is compared and how it is judged.
"""

import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from causaltwin.config import AI4I_RESULTS_PATH  # noqa: E402
from causaltwin.intelligence.ai4i import MODES, run_ai4i_check  # noqa: E402


def main() -> None:
    """Run, print a summary, and save the results."""
    warnings.filterwarnings("ignore")
    results = run_ai4i_check()
    AI4I_RESULTS_PATH.write_bytes((json.dumps(results, indent=2, default=float) + "\n").encode("utf-8"))
    print(f"Scored {results['n_scored']} failed rows in {results['seconds']:.1f} s -> {AI4I_RESULTS_PATH}")
    print("Discovered failure causes:", ", ".join(results["discovered_failure_causes"]))
    for method in results["summary"]:
        summary = results["summary"][method]
        modes = ", ".join(f"{mode} {summary[mode]['success_rate']:.0%}" for mode in MODES if summary[mode]["n"])
        print(f"  {method:27s} success {summary['success_rate']:.0%} ({modes})")


if __name__ == "__main__":
    main()
