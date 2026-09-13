# Step 5.2 — sequence model comparison

Same time-based test set as `models/train.py` (Jan-Feb 2017, n=2,842, base rate 6.1%). All three configurations see the exact same 39,454 labeled snapshots split the same way; B and C never see engineered features beyond what the GRU learns from the raw 90x7 tensor.

| Config | PR-AUC | ROC-AUC | Precision@5% | Recall@5% | Brier |
|---|---|---|---|---|---|
| A: tabular -> XGBoost | 0.6523 | 0.9320 | 0.669 | 0.546 | 0.0351 |
| B: sequence -> GRU | 0.5429 | 0.8901 | 0.556 | 0.454 | 0.1053 |
| C: sequence + tabular (joint) | 0.6242 | 0.9245 | 0.662 | 0.540 | 0.0966 |

## Interpretation

**Winner on PR-AUC: A: tabular -> XGBoost** (A=0.6523, B=0.5429, C=0.6242).
The engineered-feature XGBoost model was not beaten by either sequence configuration. This is the expected outcome for a tabular churn problem, not a failed experiment: the windowed aggregates in `fct_features` (7/30/90-day totals, active-day counts, activity ratios) already summarize the same 90 days the GRU sees, and gradient-boosted trees are typically better than a small recurrent encoder at exploiting a moderate number of informative, already-well-engineered numeric features on a dataset this size (~34k training rows). The result suggests the signal in this data lives in activity *level* and *recency* (which the aggregates capture directly) rather than in fine-grained daily pattern (bursty vs. steady, gradual decline vs. cliff) that only a sequence model could see. Config C outperforming Config B (concatenating tabular features helps the joint model) further supports this: once the model has access to the aggregates, the marginal value of the raw sequence on top of them is small. Operationally: the added complexity of training and serving a GRU (a second training pipeline, a sequence-tensor build step, GPU/CPU inference on raw daily logs instead of a single feature row) is not worth it here — Config A stays the production model.