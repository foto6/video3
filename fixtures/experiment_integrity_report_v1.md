# Experiment Integrity Report v1

- Seed: 820260927
- Plan SHA-256: 6e3b42c01107bbaeab34a59dc55b2fc556714b768232a2aa46ca9ec2b016e517
- Plan digest: ca76de53164fa40ac9dccc26e3d86a17ca47d9a789a8584de3bb672c9a268558
- Integrity corpus SHA-256: e19d67804b58eaa428a00bfa706282d45a9b011d77bf1132df969f822e4ca684
- Integrity report SHA-256: 8dd771136474b183c0aed58bc5ec7f28ae24d2b60b4f1fd2e5331fcbc46c9e70
- A/A calibration SHA-256: 3b216e8e611c39794ad533a36b4ed42b208b82db7ce413020b0479766c2c7b7f
- Sequential report with integrity SHA-256: 16246cff259a76aeb3b15da9fb2a20502a95884100787e3366800b02c5e54c7f
- Status counts: {'invalid': 5, 'valid': 1, 'warning': 0}

- cross_arm_contamination: status=invalid, samples=401, invalid=['assignment_integrity'], warning=[], treatment_effect_interpretation_allowed=False
- differential_missingness: status=invalid, samples=400, invalid=['missingness_dropout_imbalance'], warning=[], treatment_effect_interpretation_allowed=False
- early_stopping_misuse: status=invalid, samples=400, invalid=['sequential_peek_stopping_conformance'], warning=[], treatment_effect_interpretation_allowed=False
- late_event_leakage: status=invalid, samples=400, invalid=['event_time_window_leakage'], warning=[], treatment_effect_interpretation_allowed=False
- sample_ratio_mismatch: status=invalid, samples=800, invalid=['pre_randomization_covariate_imbalance', 'sample_ratio_mismatch'], warning=[], treatment_effect_interpretation_allowed=False
- valid_null_aa: status=valid, samples=1600, invalid=[], warning=[], treatment_effect_interpretation_allowed=True

- CreatorFeedback contract_version: 1.0 (unchanged)
- Historical analytics remain observational
- auto_publish=false
- external_mutation=false
