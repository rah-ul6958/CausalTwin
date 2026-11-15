"""Live data sources for the live loop: the simulator, a recorded log, or a real machine (MTConnect).

Every source has the same small interface (`DataSource`): `step()` returns the next row of the
model variables plus any events, and `describe()` says what the data is. The live loop only ever
talks to this interface, so a real machine can replace the simulator.
"""

from causaltwin.connectors.base import DataSource, SourceInfo, empty_row
from causaltwin.connectors.mtconnect import MTConnectSource, parse_mtconnect_current
from causaltwin.connectors.replay import ReplaySource

__all__ = ["DataSource", "MTConnectSource", "ReplaySource", "SourceInfo", "empty_row", "parse_mtconnect_current"]
