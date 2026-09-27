# Deterministic Experiment Assignment & Sequential Evaluation v1

## Scope

Wave 7 adds a read-only experiment protocol without changing analytics events, CreatorFeedback 1.0, growth.feedback_batch.v1, or growth.creator_seed.v1.

The transport contracts are:
- experiment.plan.v1
- experiment.evidence.v1
- experiment.sequential_result.v1
- experiment.sequential_report.v1

No protocol function publishes content, mutates a provider account, or changes Creator delivery payload bytes.

## Plan contract

experiment.plan.v1 strictly declares:
- eligible variant ids and allocation weights summing to 1.0;
- stable assignment-unit type;
- exact stratification keys;
- experiment seed;
- start and stop timestamps;
- primary metric;
- guardrail metrics;
- family-wise alpha;
- maximum looks and exact look sample sizes;
- minimum total/per-variant samples;
- missingness, allocation, distribution-shift and recommendation-churn limits.

Unknown fields and versions fail closed.
## Deterministic assignment

Assignment is a pure hash of:
- experiment id;
- experiment seed;
- assignment-unit type;
- stable unit id;
- canonical stratification key/value mapping.

The SHA-256 value is mapped into the predeclared cumulative allocation weights. The same plan, unit and stratum always produce the same variant. No external system is called and no assignment is written to an account or publishing surface.

Observed evidence is accepted as randomized experiment evidence only when every recorded assigned_variant exactly matches this deterministic assignment.

## Allocation validation

At each sequential look, the evaluator reports:
- observed sample per variant;
- expected sample from allocation weights;
- relative deviation per variant;
- maximum relative deviation;
- sample-ratio-mismatch flag;
- per-variant minimum-sample coverage.

The canonical SRM fixture stops for guardrail with maximum relative deviation 0.57 and coverage 0.5.
## Sequential method

The predeclared method is bonferroni_fixed_max_looks_two_sided_z_v1.

For K variants and L maximum looks, the multiplicity scope is:
L * (K - 1)

Each treatment-versus-control comparison at each look uses:
family-wise alpha / multiplicity scope

The canonical two-arm plan uses alpha 0.05 and four looks, so each two-sided comparison/look uses alpha 0.0125. Bonferroni's union bound controls the family-wise error rate across all predeclared looks and treatment-control comparisons without depending on the order in which looks are replayed.

The evaluator walks looks in order and stops at the first guardrail breach or multiplicity-adjusted boundary. If no boundary is crossed, the final predeclared look completes analysis. This is a fixed maximum-look protocol; adding undeclared looks is not allowed.

The z intervals are used only for the synthetic randomized-assignment corpus in this repository. Historical analytics are not passed through this inferential path.
## Evidence classification

Only source_kind=synthetic_randomized_assignment with randomized=true and observational=false is accepted by sequential experiment evaluation.

Historical analytics must use source_kind=historical_observational, randomized=false, observational=true, and preserve the existing observational interpretation:

Observational analytics describe associations only; they do not establish causal effects.

Such historical evidence can be parsed/labeled, but evaluate_sequential rejects it as randomized experiment evidence.

## Result states

The protocol emits exactly:
- insufficient_evidence
- continue
- stop_for_guardrail
- analysis_complete

Guardrails cover:
- minimum total sample;
- minimum per-variant coverage;
- missingness;
- sample-ratio mismatch/allocation imbalance;
- distribution shift;
- recommendation churn.

auto_publish and external_mutation are always false.
## Replay and restart determinism

Observations are canonicalized by event time (observed_at) then observation id. Exact duplicate observation ids with identical payloads collapse to one record. Conflicting duplicates fail closed.

A stable assignment unit may appear only once. Wrong recorded assignments fail closed.

The late/replay fixture shuffles all null-effect observations and appends duplicate rows. After parsing it produces byte-identical canonical evidence and the same sequential result as the original null fixture. This mirrors the existing finalized-window rule that event time, not ingestion order, defines deterministic analysis.

## Synthetic corpus

Canonical files:
- fixtures/experiment_plan_v1.json
- fixtures/experiment_sequential_corpus_v1.json
- fixtures/experiment_sequential_report_v1.json
- fixtures/experiment_sequential_report_v1.md

Experiment seed: 720260927

Corpus cases:
- null_effect
- positive_synthetic_effect
- sample_ratio_mismatch
- sparse_data
- late_replay
- guardrail_breach
## Canonical report

Plan SHA-256:
6e3b42c01107bbaeab34a59dc55b2fc556714b768232a2aa46ca9ec2b016e517

Plan semantic digest:
ca76de53164fa40ac9dccc26e3d86a17ca47d9a789a8584de3bb672c9a268558

Corpus SHA-256:
cf77b5ab80e12e6d67553b2d62b87f26ab24b995fdf3b92731fbb918b08724b1

Report SHA-256:
d8b0206b1d9bfd716a0ec6cf39fec91019386e683159b4fe2c3db9daea0d2d11

Key outcomes:
- null effect: analysis_complete at look 4, n=1600, effect 0.01528748, interval [-0.02110829, 0.05168324];
- positive synthetic effect: analysis_complete at look 1, n=400, effect 0.08115203, interval [0.00199273, 0.16031133];
- SRM: stop_for_guardrail at look 1, maximum allocation deviation 0.57;
- sparse data: insufficient_evidence with 120 observations;
- late/replay: byte-identical result to null after deduplication/canonical ordering;
- guardrail breach: stop_for_guardrail on distribution shift and recommendation churn.
## Creator and provider boundaries

Experiment evidence is a separate artifact. It is never embedded into CreatorFeedback 1.0, growth.feedback_batch.v1, or growth.creator_seed.v1.

Tests load the existing canonical Growth-to-Creator payloads, run the sequential report, and require exact payload bytes afterward.

Metricool and vidIQ remain read-only. Account mutation continues to raise AccountMutationDisabled.

## Validation

Focused suite:
python -m unittest discover -s tests -p "test_experiment_protocol_wave7.py" -v

Full suite:
python -m unittest discover -s tests -v
