"""Single source of truth for every constant used by CausalTwin.

All physical constants, units, and ranges below are ILLUSTRATIVE and chosen to be
physically plausible for a CNC milling spindle. They are NOT taken from any real
machine datasheet and do not claim compliance with any industry standard.
"""

from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
SAMPLES_DIR = DATA_DIR / "samples"
TRUE_GRAPH_PATH = DATA_DIR / "true_graph.json"

# ---------------------------------------------------------------------------
# Time and reproducibility
# ---------------------------------------------------------------------------
SECONDS_PER_STEP = 10  # 1 simulation step = 10 seconds of machine time
STEPS_PER_DAY = 24 * 3600 // SECONDS_PER_STEP  # 8640 steps
STEPS_PER_SHIFT = 8 * 3600 // SECONDS_PER_STEP  # 2880 steps (8 hour shift)
SETPOINT_EDGE_MARGIN = 2.0  # new setpoints stay this many adjustment spreads inside the lever range
SIMULATION_START_TIME = "2026-01-05 06:00:00"
DEFAULT_SEED = 42
DEFAULT_N_STEPS = 20_000
RANDOM_BLOCK_SIZE = 16_384  # how many random numbers are pre-drawn at once (speed)

# ---------------------------------------------------------------------------
# Variables (illustrative units and normal operating ranges)
# ---------------------------------------------------------------------------
LEVER_NAMES: tuple[str, ...] = ("load_pct", "spindle_speed", "coolant_flow", "lube_rate")
ENVIRONMENT_NAMES: tuple[str, ...] = ("ambient_temp",)
INTERNAL_SENSOR_NAMES: tuple[str, ...] = (
    "motor_current",
    "coolant_temp",
    "bearing_temp",
    "vibration",
    "tool_wear",
)
OUTCOME_NAME = "failure"
MEASURED_VARIABLES: tuple[str, ...] = ENVIRONMENT_NAMES + LEVER_NAMES + INTERNAL_SENSOR_NAMES

VARIABLE_INFO: dict[str, dict[str, object]] = {
    "ambient_temp": {"type": "environment", "unit": "deg C", "normal_range": (22.0, 38.0)},
    "load_pct": {"type": "control", "unit": "%", "normal_range": (40.0, 100.0)},
    "spindle_speed": {"type": "control", "unit": "rpm", "normal_range": (1500.0, 4500.0)},
    "coolant_flow": {"type": "control", "unit": "L/min", "normal_range": (8.0, 30.0)},
    "lube_rate": {"type": "control", "unit": "relative units", "normal_range": (0.5, 1.5)},
    "motor_current": {"type": "sensor", "unit": "A", "normal_range": None},
    "coolant_temp": {"type": "sensor", "unit": "deg C", "normal_range": None},
    "bearing_temp": {"type": "sensor", "unit": "deg C", "normal_range": (40.0, 65.0)},
    "vibration": {"type": "sensor", "unit": "mm/s RMS", "normal_range": None},
    "tool_wear": {"type": "sensor", "unit": "minutes of cutting", "normal_range": None},
    "failure": {"type": "outcome", "unit": "0/1", "normal_range": None},
}
BEARING_ALARM_TEMP = 80.0  # deg C, alarm zone starts here (illustrative)

# Physically sensible bounds used by tests and by the simulator's clipping.
PHYSICAL_BOUNDS: dict[str, tuple[float, float]] = {
    "ambient_temp": (0.0, 60.0),
    "load_pct": (0.0, 100.0),
    "spindle_speed": (0.0, 4500.0),
    "coolant_flow": (0.0, 30.0),
    "lube_rate": (0.0, 1.5),
    "motor_current": (0.0, 80.0),
    "coolant_temp": (0.0, 90.0),
    "bearing_temp": (0.0, 150.0),
    "vibration": (0.0, 30.0),
    "tool_wear": (0.0, 2000.0),
}

# ---------------------------------------------------------------------------
# Events and faults
# ---------------------------------------------------------------------------
EVENT_TYPES: tuple[str, ...] = (
    "maintenance",
    "tool_change",
    "setpoint_change",
    "shift_change",
    "failure",
    "fault_injected",
    "fault_cleared",
)
FAULT_NAMES: tuple[str, ...] = ("coolant_filter_clog", "lube_leak", "bearing_damage")

# ---------------------------------------------------------------------------
# Ingestion and data quality
# ---------------------------------------------------------------------------
COLUMN_ROLES: tuple[str, ...] = ("control", "environment", "sensor", "outcome", "ignore")
TIME_COLUMN_CANDIDATES: tuple[str, ...] = ("timestamp", "time", "datetime", "date")
FLATLINE_MIN_RUN = 30  # this many identical consecutive values counts as a flatline
STEPS_SINCE_MAINTENANCE_COLUMN = "steps_since_maintenance"
EVENT_INDICATOR_PREFIX = "event_"
# Rows flagged by these events are regime breaks (for example a tool reset), not dynamics.
REGIME_BREAK_EVENTS: tuple[str, ...] = ("tool_change", "maintenance")
MACHINE_RUNNING_COLUMN = "machine_running"

# ---------------------------------------------------------------------------
# Causal engine (Phase 2)
# ---------------------------------------------------------------------------
# Discovery
DISCOVERY_WINDOW_STEPS = 3000  # PCMCI runs on the most recent window
TAU_MAX = 3  # longest lag considered, in steps
DISCOVERY_ALPHA = 0.01  # significance level for keeping an edge
DISCOVERY_N_JOBS = 5  # parallel worker processes for PCMCI (one per target variable); 1 = sequential
# Failure model (binary, rare): uses a longer history than the PCMCI window, because a
# 3,000-step window holds too few failures (measured in the Phase 1 report).
FAILURE_HISTORY_MAX_STEPS = 100_000
FAILURE_MIN_EVENTS = 15  # below this the failure model is not fitted (reported, not guessed)
# Only measured sensors are screened as direct failure causes: levers and environment are
# assumed to act on failure through sensors. Fewer parameters keep the test powered.
FAILURE_CANDIDATE_ROLES: tuple[str, ...] = ("sensor",)
# Lags of a failure cause whose log-likelihoods differ by less than this are statistically
# indistinguishable (half the 95% chi-square(1) critical value); the shortest is used.
FAILURE_LAG_TIE_TOLERANCE = 1.92
# Correlation graph
CORRELATION_THRESHOLD = 0.3  # |lagged Pearson r| needed for a correlation edge between sensors
FAILURE_CORRELATION_HORIZON = 30  # failure correlations use "failure within the next N steps"
FAILURE_CORRELATION_THRESHOLD = 0.1
# Edge confidence (moving-block bootstrap)
BOOTSTRAP_BLOCK_LENGTH = 50
BOOTSTRAP_RESAMPLES = 20
BOOTSTRAP_MIN_CONFIDENCE = 0.6  # below this an edge is marked "uncertain"
BOOTSTRAP_SEED = 7
BOOTSTRAP_THREADS = 4  # threads for the failure-model resamples (measured fastest on an 8-core laptop)
# Causal model fitting
RIDGE_PENALTY = 1e-3
HOLDOUT_FRACTION = 0.2  # final share of the window kept out for R squared
RESIDUAL_SAMPLE_LIMIT = 5000  # residuals kept per equation for Monte Carlo noise
# What-if engine
WHATIF_HORIZON = 30  # steps (5 minutes)
WHATIF_ROLLOUTS = 200
WHATIF_SEED = 11
WHATIF_INTERVAL_PERCENTILES = (5.0, 95.0)  # 90% interval
# Non-intervened exogenous variables: "random_walk" (historical one-step changes) or "constant".
# Measured on the 30 benchmark situations: "constant" predicts no-action risk well (mean 0.297 vs true 0.304,
# correlation 0.95); "random_walk" was biased low (0.184), so constant is the default.
WHATIF_EXOGENOUS_MODE = "constant"
# Root cause
ROOT_CAUSE_RISK_THRESHOLD = 0.1  # failure probability within the horizon that counts as high
ROOT_CAUSE_MAX_PATHS = 5
ROOT_CAUSE_MAX_DEPTH = 4
INTEGRATOR_THRESHOLD = 0.98  # self-lag sum at or above this: an accumulator (no steady state)
SYMPTOM_THRESHOLD = 0.1  # |corr with failure soon| for a variable to count as a symptom
# Validation against simulator ground truth
VALIDATION_SCENARIO_CYCLE: tuple[str, ...] = ("heat_wave", "tool_wear", "normal", "overload")
VALIDATION_CHUNK_STEPS = 1500  # steps per scenario before switching to the next one
VALIDATION_TRAINING_STEPS = 60_000  # long enough for about 90 failures (needed for a powered failure model)
VALIDATION_INTERVENTIONS = 20
VALIDATION_SEEDS_PER_INTERVENTION = 8
VALIDATION_SEED = 2026
VALIDATION_RESULTS_PATH = DATA_DIR / "validation_results.json"


@dataclass(frozen=True)
class LeverSettings:
    """Operator behavior for one control lever (illustrative values)."""

    minimum: float
    maximum: float
    jitter_std: float  # size of small random operator adjustments per step
    jitter_persistence: float  # 0..1, how long an adjustment persists (AR(1) coefficient)


@dataclass(frozen=True)
class SimulatorConfig:
    """All coefficients of the simulated spindle. Every value is illustrative."""

    # --- operator behavior ---
    levers: dict[str, LeverSettings] = field(
        default_factory=lambda: {
            "load_pct": LeverSettings(40.0, 100.0, 2.0, 0.6),
            "spindle_speed": LeverSettings(1500.0, 4500.0, 60.0, 0.6),
            "coolant_flow": LeverSettings(8.0, 30.0, 0.8, 0.6),
            "lube_rate": LeverSettings(0.5, 1.5, 0.03, 0.6),
        }
    )
    setpoint_mean_interval: float = 120.0  # steps between setpoint changes (per lever, on average)
    tool_change_limit_range: tuple[float, float] = (60.0, 100.0)  # operator replaces tool at this wear

    # --- ambient environment ---
    ambient_base: float = 30.0
    ambient_daily_amplitude: float = 5.0
    ambient_drift_persistence: float = 0.999  # slow mean-reverting random walk
    ambient_drift_std: float = 0.05
    ambient_sensor_noise: float = 0.02

    # --- motor current: first-order response to mechanical load ---
    current_inertia: float = 0.5
    current_base: float = 4.0  # A, idle current
    current_per_load: float = 0.22  # A per % load
    current_per_rpm: float = 0.004  # A per rpm
    current_process_noise: float = 0.6
    # Sensor noise is kept small relative to process noise on purpose: large reading
    # noise on an autocorrelated variable makes older lags look informative and creates
    # spurious lagged edges for causal discovery (measured in the Phase 1 report).
    current_sensor_noise: float = 0.05

    # --- coolant temperature: Newton cooling toward ambient + heat rise - flow cooling ---
    coolant_inertia: float = 0.7
    coolant_heat_rise: float = 12.0  # deg C above ambient with no flow
    coolant_flow_gain: float = 0.45  # deg C removed per L/min
    coolant_process_noise: float = 0.3
    coolant_sensor_noise: float = 0.03

    # --- bearing temperature: first-order thermal dynamics ---
    bearing_inertia: float = 0.8
    bearing_base_rise: float = 0.0  # deg C
    bearing_per_amp: float = 0.7  # deg C per A of motor current
    bearing_per_rpm: float = 0.004  # deg C per rpm (friction heating)
    bearing_lube_gain: float = 11.0  # deg C removed per unit of lube rate
    bearing_process_noise: float = 0.4
    bearing_sensor_noise: float = 0.05

    # --- vibration (mm/s RMS) ---
    # Vibration summarizes speed, load and wear, the same conditions that drive failure,
    # which is what makes it a strong (but non-causal) predictor: the Correlation Trap.
    # Its process noise sets the trade-off: less noise makes the trap stronger for naive
    # ML but makes vibration harder to separate from tool_wear for causal methods.
    vibration_inertia: float = 0.4
    vibration_base: float = 0.2
    vibration_per_load: float = 0.02
    vibration_per_rpm: float = 0.00075
    vibration_per_wear: float = 0.02
    vibration_process_noise: float = 0.075
    vibration_sensor_noise: float = 0.015

    # --- tool wear (equivalent minutes of cutting) ---
    wear_reference_load: float = 70.0
    wear_reference_rpm: float = 3000.0
    wear_rpm_exponent: float = 1.3
    # Occasional micro-chipping of the cutting edge: a sudden wear jump. Besides being
    # physical, it gives wear random innovations; a purely deterministic accumulator is
    # invisible to conditional-independence tests once its own past is conditioned on.
    wear_chip_probability: float = 0.01  # per running step
    wear_chip_size: float = 8.0  # equivalent minutes added by one chip
    # Wear is read from the controller's load-weighted cutting-time counter, so the
    # reading is nearly exact. Larger noise would hide the tiny per-step increments.
    wear_sensor_noise: float = 0.005

    # --- failure hazard: sigmoid(a*(T - T0) + b*(W - W0) + c) ---
    failures_enabled: bool = True
    hazard_temp_coef: float = 0.25
    hazard_temp_threshold: float = 80.0
    hazard_wear_coef: float = 0.07
    hazard_wear_threshold: float = 120.0
    hazard_intercept: float = 2.0
    failure_downtime_steps: int = 12  # machine stopped this long after a failure

    # --- faults (change the causal structure) ---
    clog_flow_effect_factor: float = 0.2  # coolant flow keeps 20% of its cooling effect
    lube_leak_effective_rate: float = 0.3  # lube reaching the bearing during a leak (constant)
    bearing_damage_heat_factor: float = 1.6  # extra heating per amp with a damaged bearing


DEFAULT_CONFIG = SimulatorConfig()

# Scenario seeds (each scenario is reproducible).
SCENARIO_SEEDS: dict[str, int] = {
    "normal": 101,
    "overload": 202,
    "cooling_failure": 303,
    "heat_wave": 404,
    "tool_wear": 505,
}


# ---------------------------------------------------------------------------
# Intelligence layer (Phase 3). All costs are ILLUSTRATIVE "cost units", not money.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LeverCost:
    """Cost model for changing one control lever (illustrative)."""

    max_change_pct: float  # largest change allowed in one action, percent of the current value
    cost_per_pct: float  # cost units per percent of change
    side_effect_increase: str  # what an increase costs the shop
    side_effect_decrease: str  # what a decrease costs the shop


LEVER_COSTS: dict[str, LeverCost] = {
    "coolant_flow": LeverCost(30.0, 0.05, "pump energy up {pct:.0f}%", "less cooling margin"),
    "lube_rate": LeverCost(30.0, 0.08, "lubricant use up {pct:.0f}%", "less lubrication margin"),
    "spindle_speed": LeverCost(30.0, 0.5, "faster cycle, more heat", "cycle time up about {pct:.0f}%"),
    "load_pct": LeverCost(30.0, 1.0, "more output per pass, more heat", "throughput down {pct:.0f}%"),
}
# Maintenance actions the optimizer may also consider (do-interventions on a sensor state).
TOOL_CHANGE_COST = 8.0  # cost units (new tool plus a short stop)
TOOL_CHANGE_SIDE_EFFECT = "machine paused briefly for a tool change"
VIBRATION_DAMPING_COST = 3.0  # cost units for the baseline's "damp vibration" action

# Optimizer
OPTIMIZER_TARGET_RISK = 0.10  # failure probability within the horizon to get below
OPTIMIZER_SINGLE_STEP_PCT = 2.0  # single-lever grid: -30% .. +30% in 2% steps
OPTIMIZER_PAIR_STEP_PCT = 5.0  # lever-pair grid: 5% steps
OPTIMIZER_SEARCH_ROLLOUTS = 100  # rollouts per candidate during the search
OPTIMIZER_FINAL_ROLLOUTS = 200  # rollouts for re-evaluating the top candidates
OPTIMIZER_TOP_K = 5
OPTIMIZER_SEED = 21
OPTIMIZER_FINAL_SEED = 22  # finalists are re-checked with independent noise (avoids the winner's curse)
# The search aims below target * margin: picking the cheapest of ~900 noisy estimates that pass a
# threshold favours optimistic estimates; the margin absorbs that (success is still judged at the target).
OPTIMIZER_PLANNING_MARGIN = 0.8
INEFFECTIVE_RISK_REDUCTION = 0.01  # best risk reduction below this: the lever is "ineffective"

# Baseline (standard ML, for the Correlation Trap)
BASELINE_HORIZON = 30  # predict failure within the next N steps
BASELINE_N_ESTIMATORS = 200
BASELINE_MAX_DEPTH = 3
BASELINE_LEARNING_RATE = 0.1
BASELINE_SEED = 0
BASELINE_TEST_FRACTION = 0.2  # chronological final share used for accuracy metrics
BASELINE_DEFAULT_MAGNITUDE_PCT = 20.0  # used when CausalTwin recommends no lever change

# Benchmark
BENCHMARK_SITUATIONS_PER_SCENARIO = 6  # 5 scenarios x 6 = 30 situations
BENCHMARK_SEED_BASE = 7000
BENCHMARK_SEEDS_PER_SITUATION = 8  # simulator futures averaged per situation
BENCHMARK_HAZARD_SCREEN = 0.002  # cheap screen on the instantaneous true hazard
BENCHMARK_MIN_TRUE_RISK = 0.15  # a situation is "high risk" if true risk within the horizon >= this
BENCHMARK_MIN_GAP_STEPS = 400  # spacing between situations taken from one run
BENCHMARK_MAX_STEPS_PER_RUN = 20_000
BENCHMARK_RESULTS_PATH = DATA_DIR / "benchmark_results.json"

# Drift detection (CUSUM on standardized one-step-ahead errors)
DRIFT_CUSUM_K = 0.5  # allowance, in standard deviations
DRIFT_CUSUM_H = 10.0  # alarm threshold
DRIFT_Z_CLIP = 3.0  # winsorize errors so single outliers (for example tool chips) cannot trigger alone
DRIFT_REDISCOVERY_WINDOW = 600  # post-change steps collected before re-discovery
DRIFT_BOOTSTRAP_RESAMPLES = 20
DRIFT_MIN_RELATIVE_CHANGE = 0.3  # coefficient change must also be at least 30% to be reported
DRIFT_FAULT_STEP = 3000  # drift tests: fault injected after this many normal steps

# Live loop and API
LIVE_SEED = 4242
LIVE_STEPS_PER_SECOND = 5.0
LIVE_RISK_ROLLOUTS = 50  # rollouts for the per-step twin risk estimate
LIVE_ROOT_CAUSE_EVERY = 50  # steps between root-cause refreshes
LIVE_TRUTH_EVERY = 10  # steps between ground-truth risk computations (validation channel)
LIVE_TRUTH_SEEDS = 4
LIVE_BUFFER_ROWS = 5000
LIVE_RISK_HISTORY_POINTS = 600  # recent risk estimates kept for the frontend risk chart
LIVE_MAX_FRAMES_PER_SECOND = 10.0
LIVE_LOG_FLUSH_SECONDS = 0.5
WEBSOCKET_QUEUE_SIZE = 500
DEMO_SEED = 777
MODEL_CACHE_PATH = DATA_DIR / "model_cache.pkl"
CORS_ORIGINS_ENV = "CAUSALTWIN_CORS_ORIGINS"  # comma-separated list
AUTOSTART_ENV = "CAUSALTWIN_AUTOSTART"  # "0" keeps the live loop paused at start-up

# Performance budgets from CLAUDE.md section 4, keyed by activity-log step name (milliseconds).
# The console's pipeline tracker compares measured durations against these.
PERFORMANCE_BUDGETS_MS: dict[str, float] = {
    "simulate": 2000.0,  # 20,000 steps
    "discovery": 3000.0,
    "fit_model": 1000.0,
    "what_if": 200.0,
    "recommend": 1000.0,
    "baseline_fit": 10000.0,
    "benchmark": 60000.0,
}
DRIFT_RESULTS_PATH = DATA_DIR / "drift_results.json"

# Activity log buffers (in memory). Routine entries are the live loop's per-step risk
# estimates; they get their own buffer so they cannot push other entries out.
ACTIVITY_LOG_MAX_ENTRIES = 5000
ACTIVITY_LOG_MAX_ROUTINE_ENTRIES = 500

# ---------------------------------------------------------------------------
# Deployment and API safety (Phase 5)
# ---------------------------------------------------------------------------
# Environment variables read at start-up (documented in backend/.env.example).
CORS_ORIGIN_REGEX_ENV = "CAUSALTWIN_CORS_ORIGIN_REGEX"  # for example https://.*\.vercel\.app
DISCOVERY_JOBS_ENV = "CAUSALTWIN_DISCOVERY_JOBS"  # discovery worker processes; 1 saves memory on small servers
ALLOW_BENCHMARK_RUN_ENV = "CAUSALTWIN_ALLOW_BENCHMARK_RUN"  # "0" disables POST /benchmark/run (public demos)
DEFAULT_CORS_ORIGINS = "http://localhost:3000"

# Heavy requests (what-if, root cause, recommendation, BYOD, multi-step) run in worker threads.
MAX_CONCURRENT_HEAVY_REQUESTS = 2  # more at once get "busy, retry" (503) instead of queueing up
HEAVY_REQUEST_TIMEOUT_SECONDS = 30.0  # then 504; the computation itself cannot be interrupted
BYOD_REQUEST_TIMEOUT_SECONDS = 120.0
SIM_STEP_MAX = 5000  # largest "step N" request

# Input limits for POST /whatif.
WHATIF_MAX_HORIZON = 120  # steps (20 minutes)
WHATIF_MIN_ROLLOUTS = 10
WHATIF_MAX_ROLLOUTS = 1000
WHATIF_MAX_INTERVENTIONS = 8
WHATIF_PERCENT_LIMITS = (-90.0, 300.0)  # allowed "percent" change of a variable
WHATIF_MAX_NOISE_SCALE = 10.0

# Bring Your Own Data upload limits.
BYOD_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
BYOD_MAX_ROWS = 50_000
BYOD_MAX_COLUMNS = 40
