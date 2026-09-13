# Step 6.1 — calibration check

Fit on the validation set (n=1,552), evaluated on the untouched test set (n=2,842). Isotonic was tried first since it's the more flexible option, but its piecewise-constant fit on a validation set this size measurably hurt ranking (PR-AUC 0.6646 -> 0.6239) rather than leaving it ~unchanged as calibration should -- checked explicitly rather than assumed, and it failed the check. Sigmoid (Platt) scaling is smoother and doesn't have this failure mode on a val set this size.

| | Brier (lower is better) | PR-AUC (ranking) |
|---|---|---|
| raw XGBoost | 0.0342 | 0.6646 |
| isotonic-calibrated | 0.0338 | 0.6239 |
| sigmoid-calibrated | 0.0337 | 0.6646 |

**Chosen: sigmoid** — improves Brier (0.0342 -> 0.0337) while keeping PR-AUC within 1% of the raw model (0.6646 -> 0.6646). These calibrated probabilities, not the raw XGBoost output, are what step 6.1's revenue exposure metric multiplies against renewal value.
