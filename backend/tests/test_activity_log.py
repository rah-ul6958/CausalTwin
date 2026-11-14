"""Tests for the activity logger: routine entries are kept apart from the main log."""

from causaltwin.utils.activity_log import ActivityLogEntry, ActivityLogger


def test_routine_entries_do_not_push_out_main_entries() -> None:
    """A flood of routine entries leaves the main buffer intact; filters return each part."""
    logger = ActivityLogger(max_entries=5, max_routine_entries=3)
    received: list[ActivityLogEntry] = []
    logger.subscribe(received.append)
    logger.log("discovery", "Discovered causal graph", duration_ms=12.0)
    for index in range(10):
        logger.log("what_if", f"Estimate {index}", duration_ms=1.0, routine=True)
    logger.log("recommend", "Searched candidates", duration_ms=40.0)

    main = logger.entries("exclude")
    routine = logger.entries("only")
    assert [entry.step_name for entry in main] == ["discovery", "recommend"]
    assert [entry.message for entry in routine] == ["Estimate 7", "Estimate 8", "Estimate 9"]
    assert all(entry.routine for entry in routine) and not any(entry.routine for entry in main)
    merged = logger.entries()
    assert len(merged) == 5 and [entry.timestamp for entry in merged] == sorted(entry.timestamp for entry in merged)
    # Subscribers (the live stream) still receive every entry.
    assert len(received) == 12
    logger.clear()
    assert logger.entries() == []
