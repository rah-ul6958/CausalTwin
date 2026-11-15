"""Robust statistics shared by discovery and fitting: rare jumps a variable's causes cannot explain.

Example: tool micro-chipping adds 8 minutes of wear in about 1% of steps, while load and speed
change the per-step wear by tenths of a minute. Ordinary least squares lets those few jumps
dominate the test and the fit, so the real causes look insignificant. Leaving the jump rows out
of the test and the fit (but keeping them in the noise) recovers the causes.
"""

import numpy as np

from causaltwin.config import ROBUST_MAX_OUTLIER_SHARE, ROBUST_OUTLIER_Z


def robust_outliers(residuals: np.ndarray) -> np.ndarray:
    """Residuals beyond ROBUST_OUTLIER_Z robust standard deviations, if they are rare; else none.

    The robust standard deviation is 1.4826 x the median absolute deviation, which the outliers
    themselves hardly affect. With Gaussian noise practically nothing lies beyond 6 of them, so
    well-behaved variables are left untouched.
    """
    centre = float(np.median(residuals))
    scale = 1.4826 * float(np.median(np.abs(residuals - centre)))
    if scale <= 0.0:
        return np.zeros(residuals.size, dtype=bool)
    flagged = np.abs(residuals - centre) > ROBUST_OUTLIER_Z * scale
    if flagged.mean() > ROBUST_MAX_OUTLIER_SHARE:
        return np.zeros(residuals.size, dtype=bool)
    return flagged
