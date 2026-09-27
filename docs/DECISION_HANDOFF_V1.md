# Audit-Bound Creator Decision Handoff v1

Wave 11 adds growth.decision_handoff.v1 as a read-only analytics-to-Creator evidence handoff. It is bound to one exact experiment_audit_bundle.v1 digest and never grants release or publishing authority.

## Authority separation

Recommendation content and authority are distinct. Every handoff has auto_publish=false, external_mutation=false, release_authorized=false, publish_authorized=false, and requires_creator_release_authorization=true.

A confirmatory recommendation is emitted only when the final handoff classification is confirmatory_supported. invalid_integrity and exploratory_only can never carry a confirmatory recommendation. Integrity warnings conservatively reduce stronger audit outcomes to insufficient_evidence; guardrail regressions reduce confirmatory_supported to confirmatory_not_supported.

## Classification

The handoff uses exactly: confirmatory_supported, confirmatory_not_supported, exploratory_only, invalid_integrity, insufficient_evidence.

The handoff cannot have greater classification strength than the bound audit bundle. This property is tested across weaker evidence states.

## Bound evidence

The canonical handoff includes: exact audit bundle digest; registry experiment/revision/freeze hash; deterministic registry hypothesis id and hypothesis text; primary metric effect, standard error, confidence bounds, arm sample counts and rates; guardrail status/breaches; raw, sequential and family-adjusted multiplicity evidence; integrity status/reasons; sample counts; experiment window; randomization, event-window corpus, sequential-result and family-report digests.

Raw primary comparison, guardrail metrics and confirmatory family evidence remain present when recommendation is blocked.

Guardrails are read from the exact sequential result whose canonical result digest is already bound by the audit bundle. A different sequential source fails closed.

## Replay

growth.decision_handoff_ledger.v1 is append-only JSONL with fsync. The first handoff identity is recorded once. Exact replay is a duplicate no-op. A reused handoff identity with changed digest fails closed.

## Canonical Creator fixtures

- fixtures/creator_decision_handoff_v1/canonical_handoff.json
- fixtures/creator_decision_handoff_v1/duplicate_replay.json
- fixtures/creator_decision_handoff_v1/manifest.json

Canonical audit bundle digest: 788eca36cd305ff62209bec4ff76ce5b3531589234cc698d4c774844694ca5c3

Canonical decision classification: confirmatory_not_supported

Canonical handoff payload digest: d788cfad7ae5ad7e627fa2aadf6aaa529e7e17b8911742f859428d1e030e93c3

Canonical handoff fixture SHA-256: 59cf9e2c82d19c4b6e4c9f757a56897d368c1f482e7b6860a9ff65c8ae1ea55b

The duplicate replay fixture is byte-identical and has the same SHA-256.

## Canonical decision evidence

Primary metric synthetic_primary_success_rate has effect +0.01528748, standard error 0.01457168 and confidence interval [-0.02110829, 0.05168324]. Control n=828 and treatment n=772.

Integrity is valid. Guardrails pass with no breaches. The Holm family decision is false with family-adjusted p=1.0, so the handoff is confirmatory_not_supported and recommendation.confirmatory=false.

## Preserved boundaries

CreatorFeedback 1.0 bytes, growth.feedback_batch.v1, growth.creator_seed.v1, experiment_registry.v1, experiment_integrity.v1, experiment_audit_bundle.v1 and historical observational/non-causal interpretation are unchanged.

Metricool and vidIQ remain read-only. No provider write path is introduced.

## Validation

Focused: python -m unittest discover -s tests -p "test_decision_handoff_wave11.py" -v

Full: python -m unittest discover -s tests -v
