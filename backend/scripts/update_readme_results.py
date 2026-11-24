"""Refresh the generated result tables in README.md and the docs from the result files.

Every number in these tables is read from a file that code produced:
    data/benchmark_results.json   scripts/run_benchmark.py --drift
    data/drift_results.json       scripts/run_benchmark.py --drift
    data/validation_results.json  scripts/run_validation.py
    data/timings.json             scripts/measure_timings.py

Usage (from backend/):
    python scripts/update_readme_results.py           # rewrite the generated blocks
    python scripts/update_readme_results.py --check   # exit 1 if any block is out of date

A generated block sits between two markers in a Markdown file:
    <!-- results:benchmark:start -->  ...  <!-- results:benchmark:end -->
"""

import argparse
import json
import re
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from causaltwin.config import (  # noqa: E402
    AI4I_RESULTS_PATH,
    BENCHMARK_RESULTS_PATH,
    DATA_DIR,
    DRIFT_REDISCOVERY_WINDOW,
    DRIFT_RESULTS_PATH,
    SECONDS_PER_STEP,
    VALIDATION_RESULTS_PATH,
)

TIMINGS_PATH = DATA_DIR / "timings.json"
LOAD_TEST_PATH = DATA_DIR / "load_test.json"
TARGET_FILES = [
    REPOSITORY_DIR / "README.md",
    REPOSITORY_DIR / "docs" / "SLIDES_OUTLINE.md",
    REPOSITORY_DIR / "docs" / "JUDGE_QA.md",
    REPOSITORY_DIR / "docs" / "FINAL_AUDIT.md",
    REPOSITORY_DIR / "docs" / "DEMO_SCRIPT.md",
]
BLOCK_PATTERN = re.compile(r"(<!-- results:([a-z0-9_]+):start -->\n)(.*?)(<!-- results:\2:end -->)", re.DOTALL)


def _percent(value: float, digits: int = 0) -> str:
    """0.123 -> "12%" (or "12.3%" with one digit)."""
    return f"{value * 100:.{digits}f}%"


def _load(path: Path) -> dict[str, Any]:
    """Read a JSON result file, with a clear message if it is missing."""
    if not path.exists():
        raise SystemExit(f"Missing {path.relative_to(BACKEND_DIR)}; run the script that produces it first.")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _count(rate: float, total: int) -> int:
    """Number of cases behind a rate."""
    return round(rate * total)


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------
def _baseline_order(summary: dict[str, Any]) -> list[str]:
    """Baseline method names, the main (strongest) one first, then the others from strongest to weakest."""
    main = summary["main_baseline"]
    return [main] + [method for method in reversed(list(summary["baseline_labels"])) if method != main]


def _false_reassurance_risks(benchmark: dict[str, Any], method: str) -> list[float]:
    """True no-action risks of the cases where a baseline chose to do nothing although the risk was high."""
    return [
        case["true_risk_no_action"] for case in benchmark["cases"] if case["baselines"][method]["false_reassurance"]
    ]


def _risk_range(risks: list[float], spoken: bool = False) -> str:
    """[0.162, 0.247] -> "16% to 25%" (one value: "16%"); spoken: "16 to 25 percent"."""
    low, high = _percent(min(risks)), _percent(max(risks))
    if spoken:
        return f"{low[:-1]} percent" if low == high else f"{low[:-1]} to {high[:-1]} percent"
    return low if low == high else f"{low} to {high}"


def _short(label: str) -> str:
    """ "B3: predict-then-optimize" -> "B3"."""
    return label.split(":")[0]


def _best_baseline_success(summary: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The baseline that reached the target most often (method name and summary)."""
    method = max(summary["baselines"], key=lambda name: summary["baselines"][name]["success_rate"])
    return method, summary["baselines"][method]


def _causaltwin_flags_text(causaltwin: dict[str, Any]) -> str:
    """How often CausalTwin itself said no action at high risk, or made things worse."""
    reassured, worse = causaltwin["n_false_reassurance"], causaltwin["n_worse_than_no_action"]
    if reassured == 0 and worse == 0:
        return "CausalTwin did neither in any case"
    return f"CausalTwin said no action at high risk in {reassured} and made things worse in {worse}"


def headline_block(results: dict[str, dict[str, Any]]) -> str:
    """Three one-line headline numbers."""
    benchmark = results["benchmark"]
    summary = benchmark["summary"]
    total = benchmark["n_cases"]
    target = _percent(benchmark["settings"]["target_risk"])
    main = summary["main_baseline"]
    strongest = summary["baselines"][main]
    reassured = _false_reassurance_risks(benchmark, main)
    reassurance_text = (
        f"said no action was needed in **{len(reassured)} of {total}** cases whose true risk was "
        f"{_risk_range(reassured)}"
        if reassured
        else "never said no action was needed while the true risk was above the target"
    )
    ct = summary["causaltwin"]
    best_method, best = _best_baseline_success(summary)
    best_text = (
        ""
        if best_method == main
        else f"; no predictive baseline reached the target in more than {_percent(best['success_rate'])}"
    )
    faults = results["drift"]["faults"]
    delays = [run["detection_delay_steps"] for run in faults if run["detection_delay_steps"] is not None]
    median_delay = statistics.median(delays)
    confirmed = sum(1 for run in results["drift"]["faults"] if run["status"] == "confirmed")
    return (
        f"- **{_percent(ct['success_rate'])}** of {total} simulated high-risk cases brought to "
        f"{target} true failure risk or less by CausalTwin, against "
        f"**{_percent(strongest['success_rate'])}** for the strongest predictive baseline (XGBoost "
        f"predict-then-optimize: the same candidate actions, scored by a failure predictor{best_text}).\n"
        f"- That baseline {reassurance_text}, and made the machine riskier than doing nothing in "
        f"**{strongest['n_worse_than_no_action']}**; {_causaltwin_flags_text(ct)}.\n"
        f"- **{confirmed} of {len(results['drift']['faults'])}** injected physical faults were detected and the "
        f"causal graph rewired; median detection delay **{median_delay:g} steps "
        f"({median_delay * SECONDS_PER_STEP:g} s of machine time)**.\n"
    )


def quote_block(results: dict[str, dict[str, Any]]) -> str:
    """Sentences a presenter can say out loud, with the numbers filled in from the results."""
    benchmark = results["benchmark"]
    summary = benchmark["summary"]
    total = benchmark["n_cases"]
    main = summary["main_baseline"]
    strongest = summary["baselines"][main]
    ours = summary["causaltwin"]["n_success"]
    theirs = strongest["n_success"]
    reassured = _false_reassurance_risks(benchmark, main)
    recovery = results["validation"]["graph_recovery_with_failure"]
    faults = results["drift"]["faults"]
    confirmed = sum(1 for run in faults if run["status"] == "confirmed")
    false_links = recovery["n_found_edges"] - recovery["true_positives"]
    delays = [
        run["detection_delay_steps"] * SECONDS_PER_STEP for run in faults if run["detection_delay_steps"] is not None
    ]
    reassurance_text = (
        f"in {len(reassured)} of the {total} it said no action was needed while the true risk was "
        f"{_risk_range(reassured, spoken=True)}"
        if reassured
        else "it never said no action was needed while the risk was high"
    )
    first = (
        f"We tested this on {total} high-risk moments in the simulator. CausalTwin's fix brought the true risk "
        f"under the target in {ours} of {total}. The most thorough predictive baseline we built, XGBoost scoring "
        f"the very same candidate actions, managed {theirs} of {total}: {reassurance_text}, and in "
        f"{strongest['n_worse_than_no_action']} its change made the machine riskier than doing nothing. It cannot "
        "know that changing a lever also changes the temperatures it reads."
    )
    second = (
        f"The discovered graph found {recovery['true_positives']} of the {recovery['n_true_edges']} true "
        f"cause-effect links with {false_links} false ones, and the twin detected {confirmed} of {len(faults)} "
        f"injected machine faults within {min(delays):g} to {max(delays):g} seconds of machine time."
    )
    return f'> "{first}"\n>\n> "{second}"\n'


def benchmark_block(results: dict[str, dict[str, Any]]) -> str:
    """The Correlation Trap benchmark: both methods, every summary metric, wins and losses.

    Since Phase 7: CausalTwin against all three baselines, the main (strongest) baseline first.
    """
    benchmark = results["benchmark"]
    summary = benchmark["summary"]
    total = benchmark["n_cases"]
    settings = benchmark["settings"]
    labels = summary["baseline_labels"]
    order = _baseline_order(summary)
    columns = [summary["causaltwin"]] + [summary["baselines"][method] for method in order]

    def count_and_rate(method_summary: dict[str, Any]) -> str:
        return f"{method_summary['n_success']} / {total} ({_percent(method_summary['success_rate'])})"

    rows: list[tuple[str, Callable[[dict[str, Any]], str]]] = [
        (f"Reached the {_percent(settings['target_risk'])} target (true risk)", count_and_rate),
        ("Mean true risk after the action", lambda s: _percent(s["mean_true_risk_after"], 1)),
        ("Mean risk reduction", lambda s: f"{s['mean_risk_reduction'] * 100:.1f} points"),
        (
            f"Failure rate within {settings['horizon_steps']} steps (simulated futures)",
            lambda s: _percent(s["mean_failure_rate_after"], 1),
        ),
        ("Mean cost (illustrative units)", lambda s: f"{s['mean_cost']:.1f}"),
        ("Made things worse than doing nothing", lambda s: str(s["n_worse_than_no_action"])),
        ("Said no action while the true risk was above the target", lambda s: str(s["n_false_reassurance"])),
    ]
    header = ["Metric", "CausalTwin"] + [labels[method] for method in order]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for name, render in rows:
        cells = [render(column) for column in columns]
        cells[0] = f"**{cells[0]}**"
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    main = summary["main_baseline"]
    wins = summary["wins"]
    worse = summary["cases_where_causaltwin_is_worse"]
    misses = [case for case in benchmark["cases"] if not case["causaltwin"]["success"]]
    miss_text = ", ".join(
        f"case {case['case_id']} ({case['scenario']}, true risk after "
        f"{_percent(case['causaltwin']['true_risk_after'], 1)})"
        for case in misses
    )
    wins_by_baseline = summary["wins_by_baseline"]
    head_to_head = "; ".join(
        f"against {_short(labels[method])}: better in {wins_by_baseline[method].get('causaltwin', 0)}, tied in "
        f"{wins_by_baseline[method].get('tie', 0)}, worse in {wins_by_baseline[method].get('baseline', 0)}"
        for method in order[1:]
    )
    lines += [
        "",
        "B1 maps the top SHAP feature to an engineer's action (vibration becomes damping, ambient temperature means "
        "no action). B2 takes the top SHAP feature among the four levers and tool wear. B3, the main comparison, "
        "scores CausalTwin's own candidate actions (same grid, costs, target and planning margin) with the XGBoost "
        "model, which can only change a lever's value in its input row.",
        "",
        f"No action: mean true risk {_percent(summary['mean_true_risk_no_action'], 1)}. "
        f"Head to head against {_short(labels[main])}: CausalTwin better in {wins.get('causaltwin', 0)}, tied in "
        f"{wins.get('tie', 0)}, worse in {len(worse)} case(s){'' if not worse else ': ' + ', '.join(map(str, worse))}"
        f"; {head_to_head}.",
        f"CausalTwin missed the target in {len(misses)} case(s){': ' + miss_text if misses else ''}.",
    ]
    for method in order:
        method_summary = summary["baselines"][method]
        made_worse = method_summary["cases_worse_than_no_action"]
        reassured = method_summary["cases_false_reassurance"]
        if made_worse or reassured:
            parts = []
            if made_worse:
                parts.append(f"made things worse than doing nothing in case(s) {', '.join(map(str, made_worse))}")
            if reassured:
                parts.append(f"said no action while the risk was high in case(s) {', '.join(map(str, reassured))}")
            lines.append(f"{labels[method]} {'; '.join(parts)}.")
    lines += [
        "",
        "| Scenario | Cases | CausalTwin reached target | "
        + " | ".join(f"{labels[method]} reached target" for method in order)
        + " |",
        "|" + "---|" * (3 + len(order)),
    ]
    for scenario, row in benchmark["per_scenario"].items():
        lines.append(
            f"| {scenario} | {row['n']} | {_percent(row['causaltwin_success'])} | "
            + " | ".join(_percent(row["baseline_success"][method]) for method in order)
            + " |"
        )
    for method, description in (("naive_shap", "top SHAP feature"), ("actionable_shap", "top actionable SHAP feature")):
        blamed = summary["baselines"][method]["blamed"]
        text = ", ".join(f"{name} {count}" for name, count in sorted(blamed.items(), key=lambda item: -item[1]))
        lines += ["", f"What {labels[method]} blamed ({description}): {text}."]
    calibration = benchmark.get("calibration") or {}
    if calibration:
        lines += [
            "",
            "Calibration: the twin's predicted risk against the simulator's true risk in the same cases.",
            "",
            "| Twin prediction | Mean predicted | Mean true | Mean absolute error | Truth inside the 90% interval "
            "| Correlation |",
            "|---|---|---|---|---|---|",
        ]
        for key, label in (("no_action", "Risk with no action"), ("after_action", "Risk after its own action")):
            row = calibration.get(key) or {}
            if row:
                lines.append(
                    f"| {label} | {_percent(row['mean_predicted'], 1)} | {_percent(row['mean_true'], 1)} | "
                    f"{row['mean_absolute_error'] * 100:.1f} points | {_percent(row['interval_coverage'])} | "
                    f"{row['correlation']:.2f} |"
                )
        lines.append("")
    lines.append(
        f"Settings: {settings['situations_per_scenario']} situations per scenario, {settings['seeds_per_situation']} "
        f"seeds each, {settings['horizon_steps']}-step horizon; benchmark run took "
        f"{benchmark['timing_seconds']['total']:.0f} s."
    )
    return "\n".join(lines) + "\n"


def straw_man_block(results: dict[str, dict[str, Any]]) -> str:
    """Answer to "Is your baseline a straw man?": every baseline's numbers, the strongest one explained."""
    benchmark = results["benchmark"]
    summary = benchmark["summary"]
    total = benchmark["n_cases"]
    labels = summary["baseline_labels"]
    order = _baseline_order(summary)
    main = order[0]
    ct = summary["causaltwin"]
    lines = [
        "| Method | Reached the target | Mean true risk after | Worse than doing nothing | No action while risk was "
        "high |",
        "|---|---|---|---|---|",
        f"| CausalTwin | {ct['n_success']} / {total} | {_percent(ct['mean_true_risk_after'], 1)} | "
        f"{ct['n_worse_than_no_action']} | {ct['n_false_reassurance']} |",
    ]
    for method in order:
        row = summary["baselines"][method]
        lines.append(
            f"| {labels[method]} | {row['n_success']} / {total} | {_percent(row['mean_true_risk_after'], 1)} | "
            f"{row['n_worse_than_no_action']} | {row['n_false_reassurance']} |"
        )
    strongest = summary["baselines"][main]
    reassured = _false_reassurance_risks(benchmark, main)
    wins = summary["wins"]
    lines += [
        "",
        f"No action at all leaves a mean true risk of {_percent(summary['mean_true_risk_no_action'], 1)}. "
        f"Head to head with {_short(labels[main])}, CausalTwin left a lower true risk in "
        f"{wins.get('causaltwin', 0)} case(s), tied in {wins.get('tie', 0)} and was worse in "
        f"{wins.get('baseline', 0)}.",
    ]
    best_method, best = _best_baseline_success(summary)
    if best_method != main:
        lines.append(
            f"{_short(labels[best_method])} reached the target more often than {_short(labels[main])} "
            f"({best['n_success']} against {strongest['n_success']}), but {_short(labels[main])} is the main "
            f"comparison because it is the more capable method (it searches every action CausalTwin searches) and "
            f"it left the lower mean true risk ({_percent(strongest['mean_true_risk_after'], 1)} against "
            f"{_percent(best['mean_true_risk_after'], 1)}) and failure rate "
            f"({_percent(strongest['mean_failure_rate_after'], 1)} against "
            f"{_percent(best['mean_failure_rate_after'], 1)})."
        )
    if reassured:
        lines.append(
            f"{_short(labels[main])} said no action was needed in {len(reassured)} case(s) whose true risk was "
            f"{_risk_range(reassured)}, and its change made things worse than doing nothing in "
            f"{strongest['n_worse_than_no_action']}."
        )
    cases = benchmark["cases"]
    lost = [case for case in cases if case["winner"] == "baseline"]
    if lost:
        both_reached = [case for case in lost if case["causaltwin"]["success"] and case["baselines"][main]["success"]]
        cheaper = [case for case in lost if case["causaltwin"]["cost"] < case["baselines"][main]["cost"]]
        lines.append(
            f"In the {len(lost)} case(s) where {_short(labels[main])} left a lower true risk, both methods reached the "
            f"target in {len(both_reached)} and CausalTwin's action cost less in {len(cheaper)}."
        )
    ties = [case for case in cases if case["winner"] == "tie"]
    if ties:
        both_tool = [
            case
            for case in ties
            if "tool change" in case["causaltwin"]["action"] and case["baselines"][main]["blamed"] == "tool_wear"
        ]
        lines.append(
            f"In {len(both_tool)} of the {len(ties)} tie(s) both methods changed the tool: tool wear is one of the "
            "predictor's inputs, so for a tool change the edited input row matches what really happens."
        )
    return "\n".join(lines) + "\n"


def graph_block(results: dict[str, dict[str, Any]]) -> str:
    """Graph recovery against the simulator's true graph."""
    validation = results["validation"]
    with_failure = validation["graph_recovery_with_failure"]
    without_failure = validation["graph_recovery_without_failure"]
    metrics: list[tuple[str, Callable[[dict[str, Any]], str]]] = [
        ("True edges", lambda m: str(m["n_true_edges"])),
        ("Edges found (correct)", lambda m: f"{m['n_found_edges']} ({m['true_positives']})"),
        ("Precision", lambda m: f"{m['precision']:.2f}"),
        ("Recall", lambda m: f"{m['recall']:.2f}"),
        ("F1", lambda m: f"{m['f1']:.2f}"),
        ("Lag accuracy (correct edges)", lambda m: f"{m['lag_accuracy']:.2f}"),
        ("Structural Hamming distance", lambda m: str(m["structural_hamming_distance"])),
    ]
    lines = ["| Metric | With the failure edges | Between sensors and levers only |", "|---|---|---|"]
    lines += [f"| {name} | {read(with_failure)} | {read(without_failure)} |" for name, read in metrics]
    missed = ", ".join(with_failure["missed_edges"]) or "none"
    extra = ", ".join(with_failure["extra_edges"]) or "none"
    trap = validation["trap_check"]
    lines += [
        "",
        f"Missed: {missed}. Extra (false) edges: {extra}.",
        f"Trap check: discovered failure causes {', '.join(trap['discovered_failure_causes'])}; vibration "
        f"{'is' if trap['vibration_is_discovered_cause'] else 'is not'} a discovered cause (failure-screen "
        f"p = {trap['vibration_failure_screen_p_value']:.2f}) although its correlation with failure is "
        f"{trap['vibration_failure_correlation']:+.2f}; predicted risk change when vibration is halved: "
        f"{trap['predicted_risk_change_when_vibration_halved']:.3f}.",
    ]
    return "\n".join(lines) + "\n"


def intervention_block(results: dict[str, dict[str, Any]]) -> str:
    """What-if accuracy against paired forks of the real simulator, plus equation fit."""
    accuracy = results["validation"]["intervention_accuracy"]
    bearing_correct = round(accuracy["bearing_temp_direction_accuracy"] * accuracy["bearing_temp_direction_cases"])
    risk_correct = round(accuracy["risk_direction_accuracy"] * accuracy["risk_direction_cases"])
    lines = [
        f"{accuracy['n_cases']} random lever changes, {accuracy['seeds_per_case']} seeds each, "
        f"{accuracy['horizon_steps']}-step horizon, judged against the real simulator:",
        "",
        "| Prediction | Result |",
        "|---|---|",
        f"| Bearing temperature: direction of change correct | {bearing_correct} / "
        f"{accuracy['bearing_temp_direction_cases']} |",
        f"| Bearing temperature: mean absolute error at the horizon end | {accuracy['bearing_temp_mae_end']:.2f} deg C "
        f"(true changes average {accuracy['bearing_temp_mean_abs_true_change']:.2f} deg C) |",
        f"| Failure risk: direction of change correct | {risk_correct} / {accuracy['risk_direction_cases']} |",
        f"| Failure risk: mean absolute error | {accuracy['risk_mae'] * 100:.2f} points "
        f"(true changes average {accuracy['risk_mean_abs_true_change'] * 100:.2f} points) |",
        "",
        "| Fitted equation | Held-out accuracy |",
        "|---|---|",
    ]
    for card in results["validation"]["model_cards"]:
        if card["auc_holdout"] is not None:
            score = f"AUC {card['auc_holdout']:.3f}"
        else:
            score = f"R2 {card['r_squared_holdout']:.3f}"
        lines.append(f"| {card['variable']} ({card['kind']}) | {score} |")
    return "\n".join(lines) + "\n"


def drift_block(results: dict[str, dict[str, Any]]) -> str:
    """Drift detection delay and rewiring per injected fault, plus false alarms."""
    drift = results["drift"]
    fault_steps = sorted({run["fault_step"] for run in drift["faults"]})
    lines = [
        f"Each fault is injected at step {', '.join(f'{step:,}' for step in fault_steps)} of an otherwise normal run "
        f"(1 step = {SECONDS_PER_STEP:g} s).",
        "",
        "| Fault | Seed | Detected after | Graph rewired after | Status |",
        "|---|---|---|---|---|",
    ]
    for run in drift["faults"]:
        delay = run["detection_delay_steps"]
        detected = "not detected" if delay is None else f"{delay} steps ({delay * SECONDS_PER_STEP:g} s)"
        rewired = "-" if run["rewire_delay_steps"] is None else f"{run['rewire_delay_steps']} steps"
        lines.append(f"| {run['fault']} | {run['seed']} | {detected} | {rewired} | {run['status']} |")
    suspected = sum(run["suspected_alarms"] for run in drift["false_alarms"])
    wrong = sum(run["wrongly_confirmed_changes"] for run in drift["false_alarms"])
    steps = sum(run["n_steps"] for run in drift["false_alarms"])
    lines += [
        "",
        f"Fault-free runs: {steps:,} steps, {suspected} suspected alarms, {wrong} wrongly confirmed structural "
        f"changes. Rewiring waits for {DRIFT_REDISCOVERY_WINDOW} post-change steps of data before re-discovery.",
    ]
    return "\n".join(lines) + "\n"


def timings_block(results: dict[str, dict[str, Any]]) -> str:
    """Measured timings against the performance budgets."""
    timings = results["timings"]
    lines = ["| Operation | Median | Slowest run | Budget | Within budget |", "|---|---|---|---|---|"]
    for item in timings["operations"]:
        lines.append(
            f"| {item['operation']} | {_milliseconds(item['median_ms'])} | {_milliseconds(item['max_ms'])} | "
            f"{_milliseconds(item['budget_ms'])} | {'yes' if item['within_budget'] else '**no**'} |"
        )
    machine = timings["machine"]
    lines += [
        "",
        f"Measured {timings['measured_at'][:10]} on {machine['system']} ({machine['logical_cpus']} logical CPUs, "
        f"Python {machine['python']}) by `scripts/measure_timings.py`. {timings['note']}",
    ]
    return "\n".join(lines) + "\n"


def ai4i_block(results: dict[str, dict[str, Any]]) -> str:
    """AI4I 2020: recommendations judged by the dataset's documented failure rules."""
    ai4i = results["ai4i"]
    summary = ai4i["summary"]
    total = ai4i["n_scored"]
    names = {"HDF": "Heat dissipation", "PWF": "Power", "OSF": "Overstrain", "TWF": "Tool wear"}

    def share(method: str) -> str:
        rate = summary[method]["success_rate"]
        return f"{_count(rate, total)} / {total} ({_percent(rate)})"

    lines = [
        "| Failed rows whose failure the action removes | CausalTwin | XGBoost + SHAP |",
        "|---|---|---|",
        f"| All scored rows | **{share('causaltwin')}** | **{share('baseline')}** |",
    ]
    for mode, name in names.items():
        ours, theirs = summary["causaltwin"][mode], summary["baseline"][mode]
        if ours["n"]:
            lines.append(
                f"| {name} failures ({ours['n']} rows) | {_percent(ours['success_rate'])} | "
                f"{_percent(theirs['success_rate'])} |"
            )
    ours_cost, theirs_cost = summary["causaltwin"]["mean_cost"], summary["baseline"]["mean_cost"]
    lines.append(f"| Mean cost (illustrative) | {ours_cost:.1f} | {theirs_cost:.1f} |")
    failure = ai4i["failure_model"]
    blamed = ", ".join(f"{name} {count}" for name, count in ai4i["baseline_blamed"].items())
    lines += [
        "",
        f"{ai4i['n_rows']:,} rows, {ai4i['n_failures']} failures; {total} scored ({ai4i['n_random_only']} failures "
        "without a preventable mode are not scored).",
        f"Discovered failure causes: {', '.join(ai4i['discovered_failure_causes'])}. CausalTwin's failure model: "
        f"`{failure['equation']}` (held-out AUC {failure['auc_holdout']:.3f}). Baseline XGBoost held-out AUC "
        f"{ai4i['baseline_auc']:.3f}; it blamed {blamed}.",
    ]
    return "\n".join(lines) + "\n"


def load_block(results: dict[str, dict[str, Any]]) -> str:
    """Many simultaneous viewers against one backend process."""
    load = results["load"]
    tabs = load.get("session_ids", False)
    who = (
        "each like a browser tab with its own session id"
        if tabs
        else "without session ids, so all sharing one simulation"
    )
    limit = f", allowing {load['max_sessions']} private simulations" if tabs else ""
    lines = [
        f"{load['viewers']} simulated viewers for {load['seconds']:.0f} s, {who} (live stream open, status polled "
        "every 3 s, occasional what-ifs and recommendations), against one backend process on the development "
        f"laptop{limit}:",
        "",
        "| Request | Count | Median | 95th percentile | Outcomes |",
        "|---|---|---|---|---|",
    ]
    for name, row in load["requests"].items():
        outcomes = ", ".join(f"{status}: {count}" for status, count in sorted(row["statuses"].items()))
        lines.append(
            f"| {name} | {row['count']} | {_milliseconds(row['median_ms'])} | {_milliseconds(row['p95_ms'])} | "
            f"{outcomes} |"
        )
    stream, loop = load["websocket"], load["live_loop"]
    target = f"{loop['target_steps_per_second']:g}"
    if tabs:
        pace = load["simulations"]
        private = pace["private_steps_per_second"]
        private_range = f"{min(private):g} to {max(private):g}" if private else "an unmeasured rate"
        simulations = (
            f"{pace['private_viewers']} viewers got a private simulation, which ran at {private_range} steps per "
            f"second; the other {pace['shared_viewers']} shared one simulation, which ran at "
            f"{pace['shared_steps_per_second']} steps per second (target {target} each): the simulations share the "
            "CPU with each other and with the heavy requests."
        )
    else:
        simulations = (
            f"The live simulation ran at {loop['steps_per_second']} steps per second against a target of {target}: "
            "under this load the CPU is shared with the heavy requests."
        )
    lines += [
        "",
        f"503 means \"busy, retry\": at most two heavy computations run at once by design. Live stream: "
        f"{stream['messages_per_viewer_per_second']} messages per viewer per second, {stream['failed_connections']} "
        f"failed connections. {simulations}",
    ]
    return "\n".join(lines) + "\n"


def _milliseconds(value: float) -> str:
    """1234.5 -> "1.23 s", 45.6 -> "46 ms"."""
    return f"{value / 1000:.2f} s" if value >= 1000 else f"{value:.0f} ms"


BLOCKS: dict[str, Callable[[dict[str, dict[str, Any]]], str]] = {
    "headline": headline_block,
    "quote": quote_block,
    "benchmark": benchmark_block,
    "straw_man": straw_man_block,
    "graph": graph_block,
    "intervention": intervention_block,
    "drift": drift_block,
    "timings": timings_block,
    "ai4i": ai4i_block,
    "load": load_block,
}


# ---------------------------------------------------------------------------
# File updates
# ---------------------------------------------------------------------------
def render_all() -> dict[str, str]:
    """Every block's Markdown, from the current result files."""
    results = {
        "benchmark": _load(BENCHMARK_RESULTS_PATH),
        "drift": _load(DRIFT_RESULTS_PATH),
        "validation": _load(VALIDATION_RESULTS_PATH),
        "timings": _load(TIMINGS_PATH),
        "ai4i": _load(AI4I_RESULTS_PATH),
        "load": _load(LOAD_TEST_PATH),
    }
    return {name: render(results) for name, render in BLOCKS.items()}


def refresh_text(text: str, blocks: dict[str, str]) -> str:
    """Replace the body of every marked block in a Markdown text."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(2)
        if name not in blocks:
            raise SystemExit(f"Unknown results block '{name}'. Known: {sorted(blocks)}")
        return match.group(1) + blocks[name] + match.group(4)

    return BLOCK_PATTERN.sub(replace, text)


def main() -> None:
    """Rewrite (or, with --check, verify) the generated blocks in every target file."""
    parser = argparse.ArgumentParser(description="Refresh generated result tables in the docs.")
    parser.add_argument("--check", action="store_true", help="exit 1 if a block is out of date; change nothing")
    arguments = parser.parse_args()
    blocks = render_all()
    stale = []
    for path in TARGET_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        updated = refresh_text(text, blocks)
        if updated != text:
            stale.append(path.relative_to(REPOSITORY_DIR).as_posix())
            if not arguments.check:
                path.write_bytes(updated.encode("utf-8"))
    if arguments.check:
        if stale:
            raise SystemExit(f"Out of date (run scripts/update_readme_results.py): {', '.join(stale)}")
        print("All generated result blocks are up to date.")
    else:
        print("Updated: " + (", ".join(stale) or "nothing to change"))


if __name__ == "__main__":
    main()
