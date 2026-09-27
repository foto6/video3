# Experiment Family Multiplicity Report v1

- Seed: 920260927
- Method: holm_bonferroni_v1
- Registry corpus SHA-256: 3e0399e44f3aa0ceb52e2cb64872bb2fb4f17cb75468a48e48aaa196a24a0cfd
- Family report SHA-256: 02f3d76bf6a1892e8cf82b6147c803c1b710c87644ab3f5a8703d90935286186
- Registry ledger SHA-256: ffe4e13bca2530989592464305aea01a08d6585684b2e7235c43eb2ec40c42ac
- Governance fixture SHA-256: 6e27e7a4392583750ad5adc403dd84a9678d2565b2b3d8845833160b6a726304

- Valid preregistered family: planned=1, decisions=0
- Many-null family: planned=40, raw p<0.05=4, adjusted decisions=0
- Metric-swap family: exploratory metrics=1, confirmatory decisions=0
- Sequential-family decisions: 1
- Integrity-invalid blocked: 1

Sequential-family detail:
- wave9-seq-family-1: raw=0.004, sequential=0.016, Holm=0.048, decision=True, reason=holm_adjusted_at_or_below_family_alpha
- wave9-seq-family-2: raw=0.008, sequential=0.032, Holm=0.064, decision=False, reason=holm_adjusted_above_family_alpha
- wave9-seq-family-3: raw=0.02, sequential=0.08, Holm=0.08, decision=False, reason=holm_adjusted_above_family_alpha

- Post-hoc metric posthoc_metric_after_results: raw=1e-06, sequential=4e-06, label=exploratory, decision=False, reason=metric_not_preregistered_primary

- CreatorFeedback contract_version: 1.0 (unchanged)
- Historical analytics remain observational and outside randomized inference
- auto_publish=false
- external_mutation=false
