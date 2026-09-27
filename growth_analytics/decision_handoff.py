from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .experiment_audit import (
    AUDIT_BUNDLE_VERSION,
    CLASSIFICATIONS,
    canonical_json,
    sha256_json,
)

DECISION_HANDOFF_VERSION = "growth.decision_handoff.v1"
DECISION_HANDOFF_LEDGER_VERSION = "growth.decision_handoff_ledger.v1"

_CLASSIFICATION_STRENGTH = {
    "invalid_integrity": 0,
    "exploratory_only": 1,
    "insufficient_evidence": 2,
    "confirmatory_not_supported": 3,
    "confirmatory_supported": 4,
}


class DecisionHandoffError(ValueError):
    pass


class DecisionHandoffConflictError(DecisionHandoffError):
    pass


def classification_strength(classification: str) -> int:
    try:
        return _CLASSIFICATION_STRENGTH[classification]
    except KeyError as exc:
        raise DecisionHandoffError(
            f"unknown decision classification: {classification}"
        ) from exc


def _require_mapping(
    value: Any,
    name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DecisionHandoffError(f"{name} must be an object")
    return value


def _require_digest(
    value: Any,
    name: str,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise DecisionHandoffError(
            f"{name} must be lowercase SHA-256 hex"
        )
    return value


def _audit_material(bundle: Mapping[str, Any]) -> dict[str, Any]:
    material = dict(bundle)
    material.pop("bundle_digest", None)
    return material


def _verify_audit_bundle_shape(
    bundle: Mapping[str, Any],
) -> None:
    required = {
        "audit_bundle_version",
        "subject",
        "classification",
        "classification_reason",
        "registry",
        "assignment",
        "event_window_corpus",
        "integrity",
        "sequential",
        "multiplicity_family",
        "primary_metric_definition",
        "raw_evidence",
        "source_references",
        "auto_publish",
        "external_mutation",
        "bundle_digest",
    }
    if set(bundle) != required:
        raise DecisionHandoffError(
            "audit bundle fields must match experiment_audit_bundle.v1 exactly"
        )
    if bundle["audit_bundle_version"] != AUDIT_BUNDLE_VERSION:
        raise DecisionHandoffError(
            "unsupported audit bundle version"
        )
    classification = bundle["classification"]
    if classification not in CLASSIFICATIONS:
        raise DecisionHandoffError(
            "unsupported audit classification"
        )
    digest = _require_digest(
        bundle["bundle_digest"],
        "audit bundle digest",
    )
    if sha256_json(_audit_material(bundle)) != digest:
        raise DecisionHandoffError(
            "audit bundle digest mismatch"
        )
    if (
        bundle["auto_publish"] is not False
        or bundle["external_mutation"] is not False
    ):
        raise DecisionHandoffError(
            "audit bundle must remain read-only"
        )


def registry_hypothesis_id(
    bundle: Mapping[str, Any],
) -> str:
    registry = _require_mapping(
        bundle.get("registry"),
        "registry",
    )
    lineage = registry.get("lineage")
    if not isinstance(lineage, list) or not lineage:
        raise DecisionHandoffError(
            "registry lineage missing from audit"
        )
    latest = _require_mapping(
        lineage[-1],
        "latest registry lineage entry",
    )
    subject = _require_mapping(
        bundle.get("subject"),
        "subject",
    )
    material = {
        "experiment_id": subject.get("experiment_id"),
        "revision": latest.get("revision"),
        "freeze_hash": latest.get("freeze_hash"),
        "hypothesis": latest.get("hypothesis"),
        "primary_metrics": latest.get("primary_metrics"),
    }
    return sha256_json(material)


def _extract_comparison(
    bundle: Mapping[str, Any],
) -> Mapping[str, Any]:
    sequential = _require_mapping(
        bundle.get("sequential"),
        "sequential",
    )
    comparison = sequential.get("comparison")
    if not isinstance(comparison, Mapping):
        raw = _require_mapping(
            bundle.get("raw_evidence"),
            "raw_evidence",
        )
        comparison = raw.get("comparison")
    if not isinstance(comparison, Mapping):
        raise DecisionHandoffError(
            "primary metric comparison missing"
        )
    required = {
        "effect",
        "standard_error",
        "confidence_lower",
        "confidence_upper",
        "control_variant",
        "treatment_variant",
        "control_n",
        "treatment_n",
        "control_rate",
        "treatment_rate",
    }
    if not required.issubset(comparison):
        raise DecisionHandoffError(
            "primary metric effect/uncertainty fields missing"
        )
    return comparison


def _extract_guardrails(
    sequential_result: Mapping[str, Any],
) -> dict[str, Any]:
    guardrails = _require_mapping(
        sequential_result.get("guardrails"),
        "sequential guardrails",
    )
    required = {
        "breaches",
        "distribution_shift",
        "missing_rate",
        "recommendation_churn",
    }
    if set(guardrails) != required:
        raise DecisionHandoffError(
            "guardrail fields must match the sequential v1 surface"
        )
    breaches = guardrails["breaches"]
    if (
        not isinstance(breaches, list)
        or any(
            not isinstance(item, str) or not item
            for item in breaches
        )
    ):
        raise DecisionHandoffError(
            "guardrail breaches must be strings"
        )
    normalized = {
        "breaches": sorted(set(breaches)),
        "distribution_shift":
            guardrails["distribution_shift"],
        "missing_rate": guardrails["missing_rate"],
        "recommendation_churn":
            guardrails["recommendation_churn"],
    }
    normalized["status"] = (
        "regression"
        if normalized["breaches"]
        else "pass"
    )
    return normalized


def _conservative_classification(
    audit_classification: str,
    *,
    integrity_status: str,
    guardrail_status: str,
) -> str:
    if integrity_status == "invalid":
        return "invalid_integrity"
    if integrity_status == "warning":
        warning_classification = "insufficient_evidence"
        if (
            classification_strength(audit_classification)
            > classification_strength(warning_classification)
        ):
            return warning_classification
        return audit_classification
    if audit_classification == "confirmatory_supported":
        if guardrail_status != "pass":
            return "confirmatory_not_supported"
    return audit_classification


def _recommendation(
    classification: str,
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    effect = comparison["effect"]
    preferred_variant = None
    if classification == "confirmatory_supported":
        preferred_variant = (
            comparison["treatment_variant"]
            if effect >= 0
            else comparison["control_variant"]
        )
        mode = "confirmatory"
        text = (
            "Preregistered randomized evidence supports Creator review "
            "of the preferred experiment variant. This handoff does not "
            "authorize release or publishing."
        )
        confirmatory = True
    elif classification == "exploratory_only":
        mode = "exploratory"
        text = (
            "Exploratory evidence may inform future hypothesis design only; "
            "it is not a confirmatory recommendation."
        )
        confirmatory = False
    elif classification == "insufficient_evidence":
        mode = "insufficient"
        text = (
            "Evidence is insufficient for a confirmatory Creator "
            "recommendation."
        )
        confirmatory = False
    elif classification == "invalid_integrity":
        mode = "blocked"
        text = (
            "Experiment integrity is invalid; confirmatory recommendation "
            "is blocked while raw metrics remain available for inspection."
        )
        confirmatory = False
    else:
        mode = "blocked"
        text = (
            "Multiplicity-adjusted confirmatory evidence does not support "
            "a Creator recommendation for the tested change."
        )
        confirmatory = False
    return {
        "mode": mode,
        "confirmatory": confirmatory,
        "text": text,
        "data": {
            "preferred_variant": preferred_variant,
            "effect_estimate": effect,
            "source_classification": classification,
        },
    }


def _handoff_id(
    *,
    audit_bundle_digest: str,
    experiment_id: str,
    registry_revision: int,
    primary_metric: str,
) -> str:
    digest = sha256_json({
        "audit_bundle_digest": audit_bundle_digest,
        "experiment_id": experiment_id,
        "registry_revision": registry_revision,
        "primary_metric": primary_metric,
    })
    return f"gdh1:{digest}"


def build_decision_handoff(
    audit_bundle: Mapping[str, Any],
    sequential_result: Mapping[str, Any],
) -> dict[str, Any]:
    _verify_audit_bundle_shape(audit_bundle)

    subject = _require_mapping(
        audit_bundle["subject"],
        "subject",
    )
    registry = _require_mapping(
        audit_bundle["registry"],
        "registry",
    )
    lineage = registry.get("lineage")
    if not isinstance(lineage, list) or not lineage:
        raise DecisionHandoffError(
            "registry lineage missing"
        )
    latest = _require_mapping(
        lineage[-1],
        "latest registry entry",
    )
    if latest.get("revision") != subject.get("registry_revision"):
        raise DecisionHandoffError(
            "registry revision conflicts with audit subject"
        )
    if latest.get("freeze_hash") != registry.get(
        "latest_freeze_hash"
    ):
        raise DecisionHandoffError(
            "registry freeze conflicts with audit lineage"
        )

    sequential = _require_mapping(
        audit_bundle["sequential"],
        "sequential audit component",
    )
    sequential_digest = sha256_json(sequential_result)
    if sequential_digest != sequential.get("result_digest"):
        raise DecisionHandoffError(
            "sequential source does not match audited result digest"
        )

    comparison = _extract_comparison(audit_bundle)
    guardrails = _extract_guardrails(sequential_result)

    integrity = _require_mapping(
        audit_bundle["integrity"],
        "integrity",
    )
    integrity_status = integrity.get("status")
    if integrity_status not in {"valid", "warning", "invalid"}:
        raise DecisionHandoffError(
            "invalid integrity status"
        )

    audit_classification = audit_bundle["classification"]
    classification = _conservative_classification(
        audit_classification,
        integrity_status=integrity_status,
        guardrail_status=guardrails["status"],
    )
    if (
        classification_strength(classification)
        > classification_strength(audit_classification)
    ):
        raise DecisionHandoffError(
            "handoff classification cannot exceed audit classification"
        )

    family = _require_mapping(
        audit_bundle["multiplicity_family"],
        "multiplicity family",
    )
    confirmatory = family.get("confirmatory")
    if (
        confirmatory is not None
        and not isinstance(confirmatory, Mapping)
    ):
        raise DecisionHandoffError(
            "multiplicity confirmatory row invalid"
        )

    metric_name = subject.get("primary_metric")
    if not isinstance(metric_name, str) or not metric_name:
        raise DecisionHandoffError(
            "audit primary metric missing"
        )
    experiment_id = subject.get("experiment_id")
    revision = subject.get("registry_revision")
    if (
        not isinstance(experiment_id, str)
        or not experiment_id
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        raise DecisionHandoffError(
            "audit experiment identity invalid"
        )

    audit_digest = _require_digest(
        audit_bundle["bundle_digest"],
        "audit bundle digest",
    )
    hypothesis = latest.get("hypothesis")
    if not isinstance(hypothesis, str) or not hypothesis:
        raise DecisionHandoffError(
            "registry hypothesis missing"
        )
    freeze_hash = _require_digest(
        latest.get("freeze_hash"),
        "registry freeze hash",
    )
    hypothesis_id = registry_hypothesis_id(
        audit_bundle
    )

    raw_p = (
        confirmatory.get("raw_two_sided_p_value")
        if confirmatory is not None
        else None
    )
    sequential_p = (
        confirmatory.get("sequential_valid_p_value")
        if confirmatory is not None
        else None
    )
    adjusted_p = (
        confirmatory.get("family_adjusted_p_value")
        if confirmatory is not None
        else None
    )
    family_decision = (
        bool(confirmatory.get("decision"))
        if confirmatory is not None
        else False
    )
    family_reason = (
        str(confirmatory.get("decision_reason"))
        if confirmatory is not None
        else "no_confirmatory_family_row"
    )

    event_corpus = _require_mapping(
        audit_bundle["event_window_corpus"],
        "event window corpus",
    )
    window = _require_mapping(
        event_corpus.get("window"),
        "event window",
    )
    sample_counts = sequential.get("sample_counts")
    if not isinstance(sample_counts, Mapping):
        raise DecisionHandoffError(
            "sample counts missing from audit"
        )

    recommendation = _recommendation(
        classification,
        comparison,
    )
    if (
        classification
        in {"invalid_integrity", "exploratory_only"}
        and recommendation["confirmatory"]
    ):
        raise DecisionHandoffError(
            "blocked evidence cannot emit confirmatory recommendation"
        )

    handoff_id = _handoff_id(
        audit_bundle_digest=audit_digest,
        experiment_id=experiment_id,
        registry_revision=revision,
        primary_metric=metric_name,
    )
    handoff = {
        "handoff_version": DECISION_HANDOFF_VERSION,
        "handoff_id": handoff_id,
        "audit_bundle_digest": audit_digest,
        "classification": classification,
        "audit_classification": audit_classification,
        "registry": {
            "experiment_id": experiment_id,
            "revision": revision,
            "freeze_hash": freeze_hash,
            "hypothesis_id": hypothesis_id,
            "hypothesis": hypothesis,
        },
        "primary_metric": {
            "name": metric_name,
            "effect_estimate": comparison["effect"],
            "standard_error": comparison["standard_error"],
            "confidence_lower": comparison["confidence_lower"],
            "confidence_upper": comparison["confidence_upper"],
            "control_variant": comparison["control_variant"],
            "treatment_variant": comparison["treatment_variant"],
            "control_n": comparison["control_n"],
            "treatment_n": comparison["treatment_n"],
            "control_rate": comparison["control_rate"],
            "treatment_rate": comparison["treatment_rate"],
        },
        "guardrails": guardrails,
        "multiplicity": {
            "family_id": family.get("family_id"),
            "method": _require_mapping(
                family.get("multiplicity"),
                "multiplicity metadata",
            ).get("method"),
            "family_alpha": _require_mapping(
                family.get("multiplicity"),
                "multiplicity metadata",
            ).get("family_alpha"),
            "decision": family_decision,
            "decision_reason": family_reason,
            "raw_two_sided_p_value": raw_p,
            "sequential_valid_p_value": sequential_p,
            "family_adjusted_p_value": adjusted_p,
        },
        "integrity": {
            "status": integrity_status,
            "reasons": json.loads(canonical_json(
                integrity.get("reasons", {})
            )),
            "result_digest": _require_digest(
                integrity.get("result_digest"),
                "integrity result digest",
            ),
        },
        "provenance": {
            "sample_counts": json.loads(
                canonical_json(dict(sample_counts))
            ),
            "window": {
                "start": window.get("start"),
                "stop": window.get("stop"),
            },
            "event_window_corpus_digest": _require_digest(
                event_corpus.get("corpus_digest"),
                "event window corpus digest",
            ),
            "randomization_digest": _require_digest(
                _require_mapping(
                    audit_bundle["assignment"],
                    "assignment",
                ).get("randomization_digest"),
                "randomization digest",
            ),
            "sequential_result_digest": _require_digest(
                sequential.get("result_digest"),
                "sequential result digest",
            ),
            "family_report_digest": _require_digest(
                family.get("report_digest"),
                "family report digest",
            ),
        },
        "raw_metrics": {
            "primary_comparison": json.loads(
                canonical_json(dict(comparison))
            ),
            "guardrails": json.loads(
                canonical_json({
                    key: value
                    for key, value in guardrails.items()
                    if key != "status"
                })
            ),
            "family_confirmatory": (
                json.loads(canonical_json(dict(confirmatory)))
                if confirmatory is not None
                else None
            ),
            "preserved_when_blocked": True,
        },
        "recommendation": recommendation,
        "authority": {
            "auto_publish": False,
            "external_mutation": False,
            "release_authorized": False,
            "publish_authorized": False,
            "requires_creator_release_authorization": True,
            "notice": (
                "This analytics handoff is advisory evidence only and "
                "never authorizes Creator release or publishing by itself."
            ),
        },
        "creator_feedback_contract_version": "1.0",
    }
    handoff["handoff_digest"] = sha256_json(
        handoff
    )
    return json.loads(canonical_json(handoff))


def parse_decision_handoff(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "handoff_version",
        "handoff_id",
        "audit_bundle_digest",
        "classification",
        "audit_classification",
        "registry",
        "primary_metric",
        "guardrails",
        "multiplicity",
        "integrity",
        "provenance",
        "raw_metrics",
        "recommendation",
        "authority",
        "creator_feedback_contract_version",
        "handoff_digest",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != expected
    ):
        raise DecisionHandoffError(
            "decision handoff fields must match v1 exactly"
        )
    if payload["handoff_version"] != DECISION_HANDOFF_VERSION:
        raise DecisionHandoffError(
            "unsupported decision handoff version"
        )
    classification = payload["classification"]
    audit_classification = payload["audit_classification"]
    if (
        classification not in CLASSIFICATIONS
        or audit_classification not in CLASSIFICATIONS
    ):
        raise DecisionHandoffError(
            "unsupported decision classification"
        )
    if (
        classification_strength(classification)
        > classification_strength(audit_classification)
    ):
        raise DecisionHandoffError(
            "handoff classification exceeds audit classification"
        )
    audit_digest = _require_digest(
        payload["audit_bundle_digest"],
        "audit bundle digest",
    )
    registry = _require_mapping(
        payload["registry"],
        "registry",
    )
    primary = _require_mapping(
        payload["primary_metric"],
        "primary metric",
    )
    expected_id = _handoff_id(
        audit_bundle_digest=audit_digest,
        experiment_id=registry.get("experiment_id"),
        registry_revision=registry.get("revision"),
        primary_metric=primary.get("name"),
    )
    if payload["handoff_id"] != expected_id:
        raise DecisionHandoffError(
            "decision handoff id mismatch"
        )

    nested_exact = {
        "registry": {
            "experiment_id", "revision", "freeze_hash",
            "hypothesis_id", "hypothesis",
        },
        "primary_metric": {
            "name", "effect_estimate", "standard_error",
            "confidence_lower", "confidence_upper",
            "control_variant", "treatment_variant",
            "control_n", "treatment_n",
            "control_rate", "treatment_rate",
        },
        "guardrails": {
            "breaches", "distribution_shift", "missing_rate",
            "recommendation_churn", "status",
        },
        "multiplicity": {
            "family_id", "method", "family_alpha", "decision",
            "decision_reason", "raw_two_sided_p_value",
            "sequential_valid_p_value", "family_adjusted_p_value",
        },
        "integrity": {"status", "reasons", "result_digest"},
        "provenance": {
            "sample_counts", "window", "event_window_corpus_digest",
            "randomization_digest", "sequential_result_digest",
            "family_report_digest",
        },
        "raw_metrics": {
            "primary_comparison", "guardrails",
            "family_confirmatory", "preserved_when_blocked",
        },
        "recommendation": {"mode", "confirmatory", "text", "data"},
        "authority": {
            "auto_publish", "external_mutation", "release_authorized",
            "publish_authorized", "requires_creator_release_authorization",
            "notice",
        },
    }
    for name, fields in nested_exact.items():
        value = _require_mapping(payload[name], name)
        if set(value) != fields:
            raise DecisionHandoffError(
                f"{name} fields must match decision handoff v1 exactly"
            )

    recommendation = _require_mapping(
        payload["recommendation"],
        "recommendation",
    )
    data = _require_mapping(
        recommendation.get("data"),
        "recommendation data",
    )
    if set(data) != {
        "preferred_variant", "effect_estimate", "source_classification"
    }:
        raise DecisionHandoffError(
            "recommendation data fields must match v1 exactly"
        )
    expected_confirmatory = (
        classification == "confirmatory_supported"
    )
    if recommendation.get("confirmatory") is not expected_confirmatory:
        raise DecisionHandoffError(
            "recommendation authority conflicts with classification"
        )
    if (
        classification
        in {"invalid_integrity", "exploratory_only"}
        and recommendation.get("confirmatory") is not False
    ):
        raise DecisionHandoffError(
            "blocked classification emitted confirmatory recommendation"
        )

    authority = _require_mapping(
        payload["authority"],
        "authority",
    )
    required_authority = {
        "auto_publish": False,
        "external_mutation": False,
        "release_authorized": False,
        "publish_authorized": False,
        "requires_creator_release_authorization": True,
    }
    for key, expected_value in required_authority.items():
        if authority.get(key) is not expected_value:
            raise DecisionHandoffError(
                f"decision authority field {key} is invalid"
            )
    if payload["creator_feedback_contract_version"] != "1.0":
        raise DecisionHandoffError(
            "CreatorFeedback contract version changed"
        )

    provided_digest = _require_digest(
        payload["handoff_digest"],
        "handoff digest",
    )
    material = dict(payload)
    material.pop("handoff_digest")
    if sha256_json(material) != provided_digest:
        raise DecisionHandoffError(
            "decision handoff digest mismatch"
        )
    return json.loads(canonical_json(dict(payload)))


def decision_handoff_json(
    payload: Mapping[str, Any],
) -> str:
    return canonical_json(
        parse_decision_handoff(payload)
    )


class DecisionHandoffLedger:
    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)
        self._records: dict[str, str] = {}
        self._sequence = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(
                encoding="utf-8"
            ).splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DecisionHandoffConflictError(
                    f"invalid handoff ledger JSON line {line_number}"
                ) from exc
            expected = {
                "ledger_version",
                "sequence",
                "handoff_id",
                "handoff_digest",
                "audit_bundle_digest",
            }
            if (
                not isinstance(row, Mapping)
                or set(row) != expected
            ):
                raise DecisionHandoffConflictError(
                    "handoff ledger fields invalid"
                )
            if (
                row["ledger_version"]
                != DECISION_HANDOFF_LEDGER_VERSION
            ):
                raise DecisionHandoffConflictError(
                    "unsupported handoff ledger version"
                )
            if row["sequence"] != self._sequence + 1:
                raise DecisionHandoffConflictError(
                    "handoff ledger sequence not contiguous"
                )
            handoff_id = row["handoff_id"]
            digest = row["handoff_digest"]
            existing = self._records.get(handoff_id)
            if existing is not None:
                if existing == digest:
                    raise DecisionHandoffConflictError(
                        "duplicate durable handoff row"
                    )
                raise DecisionHandoffConflictError(
                    "conflicting durable handoff identity"
                )
            self._records[handoff_id] = digest
            self._sequence += 1

    def record(
        self,
        handoff: Mapping[str, Any],
    ) -> str:
        parsed = parse_decision_handoff(handoff)
        handoff_id = parsed["handoff_id"]
        digest = parsed["handoff_digest"]
        existing = self._records.get(handoff_id)
        if existing is not None:
            if existing == digest:
                return "duplicate"
            raise DecisionHandoffConflictError(
                "handoff identity conflicts with durable record"
            )

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        row = {
            "ledger_version":
                DECISION_HANDOFF_LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "handoff_id": handoff_id,
            "handoff_digest": digest,
            "audit_bundle_digest":
                parsed["audit_bundle_digest"],
        }
        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._records[handoff_id] = digest
        self._sequence += 1
        return "recorded"

    @property
    def row_count(self) -> int:
        return self._sequence
