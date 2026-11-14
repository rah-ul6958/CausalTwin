"""Train (or load from cache) the models the API, benchmark and tests share.

Training data: the Phase 2 validation dataset (one continuous simulator run cycling
normal and stressed scenarios). The cache is keyed by a fingerprint of every source file
that affects training, so editing the simulator, engine or baseline retrains it.
"""

import hashlib
import pickle
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from causaltwin.config import (
    BACKEND_DIR,
    MODEL_CACHE_PATH,
    VALIDATION_CHUNK_STEPS,
    VALIDATION_SCENARIO_CYCLE,
    VALIDATION_SEED,
    VALIDATION_TRAINING_STEPS,
)
from causaltwin.engine.twin import CausalTwinEngine
from causaltwin.ingestion import align_events
from causaltwin.intelligence.baseline import BaselineModel
from causaltwin.simulator.scenarios import run_scenario_cycle
from causaltwin.utils.activity_log import get_activity_logger
from causaltwin.utils.timing import timed_step

FINGERPRINT_SOURCES = ("config.py", "simulator", "engine", "ingestion", "intelligence/baseline.py")


@dataclass
class TrainedModels:
    """The fitted causal engine and baseline, plus their training data."""

    engine: CausalTwinEngine
    baseline: BaselineModel
    training_data: pd.DataFrame
    fingerprint: str
    training_seconds: float
    details: dict[str, Any] = field(default_factory=dict)


def code_fingerprint() -> str:
    """Hash of every source file that affects the trained models."""
    package = BACKEND_DIR / "causaltwin"
    digest = hashlib.sha256()
    for source in FINGERPRINT_SOURCES:
        path = package / source
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file in files:
            digest.update(file.read_bytes())
    return digest.hexdigest()[:16]


def train_models(use_cache: bool = True, cache_path: Path = MODEL_CACHE_PATH) -> TrainedModels:
    """Load the trained models from the cache, or train and cache them."""
    fingerprint = code_fingerprint()
    if use_cache and cache_path.exists():
        with timed_step("load_models", "Loaded trained models from cache") as timer:
            with cache_path.open("rb") as handle:
                cached: TrainedModels = pickle.load(handle)
            timer.details.update({"fingerprint_match": cached.fingerprint == fingerprint})
        if cached.fingerprint == fingerprint:
            cached.engine.logger = get_activity_logger()
            return cached
    warnings.filterwarnings("ignore")
    with timed_step("train_models", "Trained causal engine and baseline") as timer:
        start_time = time.perf_counter()
        simulation = run_scenario_cycle(
            VALIDATION_SCENARIO_CYCLE, VALIDATION_CHUNK_STEPS, VALIDATION_TRAINING_STEPS, VALIDATION_SEED
        )
        training_data = align_events(simulation.sensors, simulation.events)
        engine = CausalTwinEngine()
        engine.warm_up()
        engine.fit(training_data)
        baseline = BaselineModel().fit(training_data)
        elapsed = time.perf_counter() - start_time
        timer.details.update({"rows": len(training_data), "failures": int(training_data["failure"].sum())})
    models = TrainedModels(
        engine=engine,
        baseline=baseline,
        training_data=training_data,
        fingerprint=fingerprint,
        training_seconds=elapsed,
        details={"failures": int(training_data["failure"].sum()), "rows": len(training_data)},
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as handle:
        pickle.dump(models, handle)
    return models
