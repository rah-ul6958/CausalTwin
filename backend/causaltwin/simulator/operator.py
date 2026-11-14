"""Operator behavior: how the human operator moves the control levers.

Levers are not constant: every so often the operator picks a new setpoint, and on
top of that makes small random adjustments. This variation is what lets causal
discovery tell causes apart later.
"""

from causaltwin.config import DEFAULT_N_STEPS, LEVER_NAMES, SETPOINT_EDGE_MARGIN, SimulatorConfig
from causaltwin.simulator.random_stream import RandomStream


class OperatingProfile:
    """Describes allowed lever ranges and environment offsets over a run.

    The base profile uses the normal ranges from the config. Scenarios subclass it
    to make conditions change over time (for example load creeping up).
    """

    def __init__(self, config: SimulatorConfig, total_steps: int = DEFAULT_N_STEPS, start_step: int = 0) -> None:
        """Store the config, the planned length, and the step at which the profile starts.

        `start_step` lets a scenario be switched in mid-run: its progress is counted
        from the moment it was applied.
        """
        self.config = config
        self.total_steps = max(1, total_steps)
        self.start_step = start_step

    def progress(self, step: int) -> float:
        """Return how far through the profile we are, from 0.0 to 1.0."""
        return min(1.0, max(0.0, (step - self.start_step) / self.total_steps))

    def lever_range(self, lever: str, step: int) -> tuple[float, float]:
        """Return the (min, max) range the operator uses for a lever at this step."""
        settings = self.config.levers[lever]
        return settings.minimum, settings.maximum

    def ambient_offset(self, step: int) -> float:
        """Return an extra ambient temperature offset in deg C (0 in normal operation)."""
        return 0.0


class OperatorModel:
    """Simulates setpoint changes and small adjustments for every lever."""

    def __init__(self, config: SimulatorConfig, profile: OperatingProfile) -> None:
        """Start every lever at the middle of its range with no adjustment."""
        self.config = config
        self.profile = profile
        self.setpoints: dict[str, float] = {}
        self.adjustments: dict[str, float] = {}
        for lever in LEVER_NAMES:
            low, high = profile.lever_range(lever, 0)
            self.setpoints[lever] = (low + high) / 2.0
            self.adjustments[lever] = 0.0

    def update(self, step: int, random_stream: RandomStream) -> tuple[dict[str, float], list[tuple[str, float, float]]]:
        """Advance the operator by one step.

        Returns the commanded lever values and a list of setpoint changes as
        (lever, old_setpoint, new_setpoint). Always draws exactly 3 random numbers
        per lever so the random stream stays aligned across runs.
        """
        change_probability = 1.0 / self.config.setpoint_mean_interval
        lever_values: dict[str, float] = {}
        setpoint_changes: list[tuple[str, float, float]] = []
        for lever in LEVER_NAMES:
            settings = self.config.levers[lever]
            low, high = self.profile.lever_range(lever, step)
            change_draw = random_stream.uniform()
            value_draw = random_stream.uniform()
            adjustment_draw = random_stream.normal()

            if change_draw < change_probability:
                old_setpoint = self.setpoints[lever]
                setpoint_low, setpoint_high = self._setpoint_bounds(lever, low, high)
                self.setpoints[lever] = setpoint_low + value_draw * (setpoint_high - setpoint_low)
                setpoint_changes.append((lever, old_setpoint, self.setpoints[lever]))

            # Small operator adjustments follow an AR(1) process around the setpoint.
            self.adjustments[lever] = (
                settings.jitter_persistence * self.adjustments[lever] + settings.jitter_std * adjustment_draw
            )
            lever_values[lever] = min(high, max(low, self.setpoints[lever] + self.adjustments[lever]))
        return lever_values, setpoint_changes

    def _setpoint_bounds(self, lever: str, low: float, high: float) -> tuple[float, float]:
        """Range for new setpoints, kept away from the edges so adjustments are rarely clipped.

        Clipping at the range edge would create flat runs and a nonlinearity in the data.
        The margin is a multiple of the long-run spread of the AR(1) adjustments.
        """
        settings = self.config.levers[lever]
        adjustment_spread = settings.jitter_std / (1.0 - settings.jitter_persistence**2) ** 0.5
        margin = SETPOINT_EDGE_MARGIN * adjustment_spread
        if high - low <= 2.0 * margin:
            middle = (low + high) / 2.0
            return middle, middle
        return low + margin, high - margin
