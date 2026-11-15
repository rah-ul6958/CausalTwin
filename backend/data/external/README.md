# External data

## `ai4i2020.csv`: AI4I 2020 Predictive Maintenance Dataset

- Source: UCI Machine Learning Repository, https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset
  (DOI 10.24432/C5HS5C), downloaded 2026-11-15. The only change: line endings converted to LF.
- License: Creative Commons Attribution 4.0 International (CC BY 4.0).
- Author and paper: S. Matzka (2020). Explainable Artificial Intelligence for Predictive Maintenance
  Applications. *Third International Conference on Artificial Intelligence for Industries (AI4I)*, 91-95.
  doi:10.1109/AI4I49448.2020.00023.
- What it is: **synthetic** data (10,000 rows) modelled on a real milling machine, not measurements
  from one. Its documentation gives the exact rules behind each failure mode (heat dissipation,
  power, overstrain, tool wear, random), which CausalTwin uses as ground truth in
  `causaltwin/intelligence/ai4i.py` (`python scripts/run_ai4i.py`).
