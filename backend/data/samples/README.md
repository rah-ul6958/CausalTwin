# Sample data: SIMULATED

Every file in this folder is **simulated** by the CausalTwin spindle simulator
(`backend/causaltwin/simulator/`). None of it comes from a real machine. All
physical constants behind it are illustrative, not from a real machine datasheet.

Regenerate with `python scripts/generate_samples.py` (from `backend/`).

- `<scenario>_sensors.csv`: one row per 10-second step: `timestamp`, `step`, the
  ten measured variables, `failure` (0/1), and `machine_running` (0 while the machine
  is stopped after a failure).
- `<scenario>_events.csv`: the event log (`timestamp`, `step`, `event_type`,
  `details` as JSON).

Simulator ground truth (true hazard and true internal states) is deliberately not
included in these files.
