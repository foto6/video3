# Experiment Integrity v1

## Purpose

Wave 8 adds a pre-inference integrity gate around the deterministic Wave 7 experiment protocol. Integrity is evaluated before treatment-effect interpretation. An invalid result keeps the raw sequential report inspectable but blocks treatment-effect conclusions.

Frozen boundaries remain unchanged:
- CreatorFeedback contract_version "1.0"
- growth.feedback_batch.v1
- growth.creator_seed.v1
- historical analytics remain observational/non-causal

The integrity artifact version is experiment_integrity.v1.

## Exact bindings

Every integrity result binds:
- exact plan file SHA-256;
- semantic plan digest;
- deterministic randomization SHA-256 over unit/strata/assigned-arm rows;
- exact case corpus SHA-256;
- exact sequential-report SHA-256.

The top-level canonical integrity report also binds the full integrity corpus SHA-256.

## Integrity checks

The gate evaluates:
- synthetic A/A null calibration;
- sample-ratio mismatch against planned allocation;
- duplicate assignment units and cross-arm contamination;
- wrong deterministic assignment;
- missingness/dropout imbalance across arms;
- event-time leakage before experiment start or at/after stop;
- pre-randomization strata/covariate share imbalance;
- sequential peek and stopping-policy conformance;
- guardrail-data field coverage.
## Status semantics

Machine-readable status is one of:
- valid
- warning
- invalid

Each result carries separate invalid and warning reason arrays.

invalid always sets treatment_effect_interpretation_allowed=false. Raw diagnostics and the sequential report remain inspectable.

warning preserves the evidence for inspection while surfacing a non-fatal integrity concern. The layer never auto-publishes and never mutates providers or Creator state.

## A/A calibration

The canonical Monte Carlo calibration uses:
- seed 820260927;
- 1000 deterministic synthetic null simulations;
- null success probability 0.10;
- the existing Wave 7 Bonferroni fixed-maximum-look protocol;
- family-wise alpha 0.05;
- looks at 400 / 800 / 1200 / 1600.

Observed false positives: 30 / 1000.
False-positive rate: 0.03.
Wilson 95% interval: [0.0210936, 0.04250368].

The upper interval remains below alpha 0.05, so the calibration status is valid. This is a synthetic null calibration result, not a business or historical causal claim.
## Allocation and contamination

SRM uses the plan's allocation weights and maximum relative-deviation threshold. Counts are evaluated before treatment-effect interpretation.

Assignment integrity examines raw evidence before the strict Wave 7 evidence parser:
- exact same-observation replay rows are diagnosable;
- a stable unit appearing under more than one distinct observation id is duplicated assignment;
- a unit present in both arms is cross-arm contamination;
- any arm that disagrees with deterministic plan assignment is invalid.

This pre-parser layer is intentional: contamination must remain visible even when the strict experiment parser would reject it.

## Missingness and covariates

Missingness is reported per arm with:
- total rows;
- missing rows;
- arm-specific missing rate;
- maximum missing rate;
- cross-arm missing-rate gap.

The plan maximum missing-rate limit remains authoritative. Differential dropout gap above 0.15 is invalid; 0.08-0.15 is warning.

Pre-randomization covariate diagnostics use the declared stratification keys. For every strata level, the gate compares arm shares. Maximum absolute share gap above 0.20 is invalid; 0.10-0.20 is warning.
## Event-time and sequential conformance

Experiment observations must satisfy:
start_at <= observed_at < stop_at

Any pre-start, post-stop, or malformed experiment timestamp invalidates integrity.

Sequential history must use only declared look numbers and exact planned look sample sizes. The gate rejects:
- undeclared look numbers;
- undeclared peek sample sizes;
- analysis_complete before the final look without a multiplicity-adjusted boundary crossing;
- continuation past the final planned look.

This check is independent of the raw treatment-effect report, so an invalid early-stop report remains available for inspection without being treated as valid experiment inference.

## Guardrail coverage

Every raw observation is checked for the fields required by integrity guardrails:
- missing
- distribution_value
- recommendation_token

Coverage below 0.90 is invalid; partial coverage below 1.0 is warning.
## Synthetic integrity corpus

Canonical cases:
- valid_null_aa: valid A/A null evidence;
- sample_ratio_mismatch: allocation distortion;
- cross_arm_contamination: repeated stable unit in the opposite arm;
- differential_missingness: treatment-arm dropout imbalance;
- late_event_leakage: observations moved after the experiment stop;
- early_stopping_misuse: analysis_complete at look 1 without a boundary.

Canonical status counts:
- valid: 1
- warning: 0
- invalid: 5

Every invalid case sets treatment_effect_interpretation_allowed=false.

Property tests prove:
- equivalent assignment ordering is permutation invariant;
- stronger SRM cannot improve integrity;
- duplicated/cross-arm assignment cannot disappear after replay.

## Sequential report integration

build_sequential_report now accepts an optional integrity_reference. When omitted, Wave 7 core output is unchanged. Wave 8 publishes a separate integrated fixture carrying:
- integrity version;
- integrity report SHA-256;
- plan digest;
- valid-null integrity status.

The original Wave 7 sequential report file remains byte-identical.
## Canonical artifacts

- fixtures/experiment_integrity_calibration_v1.json
- fixtures/experiment_integrity_calibration_v1.md
- fixtures/experiment_integrity_corpus_v1.json
- fixtures/experiment_integrity_report_v1.json
- fixtures/experiment_integrity_report_v1.md
- fixtures/experiment_sequential_report_with_integrity_v1.json

Hashes:
- calibration: 3b216e8e611c39794ad533a36b4ed42b208b82db7ce413020b0479766c2c7b7f
- integrity corpus: e19d67804b58eaa428a00bfa706282d45a9b011d77bf1132df969f822e4ca684
- integrity report: 8dd771136474b183c0aed58bc5ec7f28ae24d2b60b4f1fd2e5331fcbc46c9e70
- integrated sequential report: 16246cff259a76aeb3b15da9fb2a20502a95884100787e3366800b02c5e54c7f

## Provider boundary

Metricool and vidIQ remain read-only. Account mutation remains blocked. auto_publish=false and external_mutation=false throughout integrity/report artifacts.

## Validation

Focused:
python -m unittest discover -s tests -p "test_experiment_integrity_wave8.py" -v

Full:
python -m unittest discover -s tests -v
