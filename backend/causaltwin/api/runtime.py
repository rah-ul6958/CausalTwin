"""Runtime settings read from environment variables at start-up (see backend/.env.example)."""

import os
from dataclasses import dataclass

from causaltwin.config import (
    ALLOW_BENCHMARK_RUN_ENV,
    AUTOSTART_ENV,
    CORS_ORIGIN_REGEX_ENV,
    CORS_ORIGINS_ENV,
    DEFAULT_CORS_ORIGINS,
    DISCOVERY_JOBS_ENV,
    DISCOVERY_N_JOBS,
)


def _flag(name: str, default: bool) -> bool:
    """A yes/no environment variable: "0", "false", "no" and "off" mean no."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _positive_int(name: str, default: int) -> int:
    """A positive integer environment variable; a missing or invalid value gives the default."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        number = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a whole number, got {value!r}") from error
    if number < 1:
        raise ValueError(f"{name} must be at least 1, got {number}")
    return number


@dataclass(frozen=True)
class RuntimeSettings:
    """Deployment settings that may differ between a laptop and a server."""

    cors_origins: tuple[str, ...]
    cors_origin_regex: str | None
    autostart: bool
    discovery_jobs: int
    allow_benchmark_run: bool

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
        )
