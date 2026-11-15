"""Runtime settings read from environment variables at start-up (see backend/.env.example)."""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from causaltwin.config import (
    ALLOW_BENCHMARK_RUN_ENV,
    AUTOSTART_ENV,
    CORS_ORIGIN_REGEX_ENV,
    CORS_ORIGINS_ENV,
    DATA_SOURCE_ENV,
    DATA_SOURCES,
    DEFAULT_CORS_ORIGINS,
    DEFAULT_MAX_SESSIONS,
    DISCOVERY_JOBS_ENV,
    DISCOVERY_N_JOBS,
    MAX_SESSIONS_ENV,
    MTCONNECT_URL_ENV,
    REPLAY_PATH_ENV,
    REPLAY_SIMULATED_ENV,
    TAG_MAP_ENV,
)
from causaltwin.connectors import DataSource, MTConnectSource, ReplaySource


def _flag(name: str, default: bool) -> bool:
    """A yes/no environment variable: "0", "false", "no" and "off" mean no."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _whole_number(name: str, default: int, minimum: int) -> int:
    """A whole-number environment variable; missing gives the default, invalid or too small an error."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        number = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a whole number, got {value!r}") from error
    if number < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {number}")
    return number


def _positive_int(name: str, default: int) -> int:
    """A positive integer environment variable; a missing value gives the default."""
    return _whole_number(name, default, minimum=1)


def _choice(name: str, allowed: tuple[str, ...]) -> str:
    """One of the allowed values (the first is the default)."""
    value = os.environ.get(name, "").strip().lower() or allowed[0]
    if value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}, got {value!r}")
    return value


def _tag_map() -> dict[str, str]:
    """The JSON tag map, {model variable: data item id or CSV column}; empty if not set."""
    text = os.environ.get(TAG_MAP_ENV, "").strip()
    if not text:
        return {}
    mapping = json.loads(text)
    if not isinstance(mapping, dict) or not all(isinstance(v, str) for v in mapping.values()):
        raise ValueError(f"{TAG_MAP_ENV} must be a JSON object of strings")
    return {str(key): str(value) for key, value in mapping.items()}


@dataclass(frozen=True)
class RuntimeSettings:
    """Deployment settings that may differ between a laptop and a server."""

    cors_origins: tuple[str, ...]
    cors_origin_regex: str | None
    autostart: bool
    discovery_jobs: int
    allow_benchmark_run: bool
    data_source: str = "simulator"
    replay_path: str | None = None
    replay_simulated: bool = True
    mtconnect_url: str | None = None
    tag_map: dict[str, str] = field(default_factory=dict)
    max_sessions: int = DEFAULT_MAX_SESSIONS  # private live simulations at once (0: every viewer shares one)

    def source_factory(self) -> Callable[[], DataSource] | None:
        """How the live loop gets its data; None means the built-in simulator."""
        if self.data_source == "replay":
            if not self.replay_path:
                raise ValueError(f"{DATA_SOURCE_ENV}=replay needs {REPLAY_PATH_ENV}")
            path, mapping, simulated = Path(self.replay_path), dict(self.tag_map), self.replay_simulated
            return lambda: ReplaySource(path, mapping, simulated_data=simulated)
        if self.data_source == "mtconnect":
            if not self.mtconnect_url or not self.tag_map:
                raise ValueError(f"{DATA_SOURCE_ENV}=mtconnect needs {MTCONNECT_URL_ENV} and {TAG_MAP_ENV}")
            url, tags = self.mtconnect_url, dict(self.tag_map)
            return lambda: MTConnectSource(url, tags)
        return None

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        """Read every setting from the environment, with laptop-friendly defaults."""
        origins = os.environ.get(CORS_ORIGINS_ENV, DEFAULT_CORS_ORIGINS)
        regex = os.environ.get(CORS_ORIGIN_REGEX_ENV, "").strip()
        return cls(
            cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
            cors_origin_regex=regex or None,
            autostart=_flag(AUTOSTART_ENV, True),
            discovery_jobs=_positive_int(DISCOVERY_JOBS_ENV, DISCOVERY_N_JOBS),
            allow_benchmark_run=_flag(ALLOW_BENCHMARK_RUN_ENV, True),
            data_source=_choice(DATA_SOURCE_ENV, DATA_SOURCES),
            replay_path=os.environ.get(REPLAY_PATH_ENV) or None,
            replay_simulated=_flag(REPLAY_SIMULATED_ENV, True),
            mtconnect_url=os.environ.get(MTCONNECT_URL_ENV) or None,
            tag_map=_tag_map(),
            max_sessions=_whole_number(MAX_SESSIONS_ENV, DEFAULT_MAX_SESSIONS, minimum=0),
        )
