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
