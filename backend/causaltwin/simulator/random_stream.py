"""Fast, seeded random numbers for the simulator.

Drawing one number at a time from numpy is slow, so we pre-draw blocks and hand
them out one by one. The simulator always consumes the same number of draws per
step, whatever interventions are active ("common random numbers"). This means two
runs with the same seed differ ONLY because of the intervention, which makes
intervention comparisons against ground truth exact.
"""

import numpy as np

from causaltwin.config import RANDOM_BLOCK_SIZE


class RandomStream:
    """Seeded source of standard-normal and uniform(0, 1) random numbers."""

    def __init__(self, seed: int, block_size: int = RANDOM_BLOCK_SIZE) -> None:
        """Create independent normal and uniform streams from one seed."""
        normal_seed, uniform_seed = np.random.SeedSequence(seed).spawn(2)
        self._normal_generator = np.random.default_rng(normal_seed)
        self._uniform_generator = np.random.default_rng(uniform_seed)
        self._block_size = block_size
        self._normals: list[float] = []
        self._uniforms: list[float] = []
        self._normal_index = 0
        self._uniform_index = 0

    def normal(self) -> float:
        """Return one standard-normal random number."""
        if self._normal_index >= len(self._normals):
            self._normals = self._normal_generator.standard_normal(self._block_size).tolist()
            self._normal_index = 0
        value = self._normals[self._normal_index]
        self._normal_index += 1
        return value

    def uniform(self) -> float:
        """Return one uniform random number in [0, 1)."""
        if self._uniform_index >= len(self._uniforms):
            self._uniforms = self._uniform_generator.random(self._block_size).tolist()
            self._uniform_index = 0
        value = self._uniforms[self._uniform_index]
        self._uniform_index += 1
        return value
