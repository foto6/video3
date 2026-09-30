from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .core import AnalyticsEvent, retention_auc
from .decision_handoff import (
    DECISION_HANDOFF_VERSION,
    DecisionHandoffError,
    parse_decision_handoff,
)
from .engine import (
    CAUSALITY_NOTICE,
    TimeWindow,
    aggregate_events,
    aggregate_retention_cohort,
)
from .event_stream import analytics_event_to_dict, parse_timestamp
from .experiment import wilson_interval
from .reliability import (
    WindowFinalizationConflictError,
    canonical_event_set,
    event_set_digest,
)


SOURCE_MANIFEST_VERSION = "growth.readonly_export_manifest.v1"
SOURCE_SNAPSHOT_VERSION = "growth.decision_source_snapshot.v1"
DELIVERY_AUDIT_VERSION = "growth.decision_delivery_audit.v1"
CREATOR_DECISION_SEED_VERSION = "growth.creator_decision_seed.v1"
OUTBOX_LEDGER_VERSION = "growth.decision_delivery_outbox.v1"
CONSUMER_LEDGER_VERSION = "growth.creator_decision_consumer_ledger.v1"
SIGNATURE_ALGORITHM = "hmac-sha256"
LIVE_READ_ONLY_PROVIDERS = frozenset({"metricool", "vidiq"})


class DecisionDeliveryError(ValueError):
    pass


class SourceProvenanceError(DecisionDeliveryError):
    pass


class IncompleteExportError(SourceProvenanceError):
    pass


class StaleExperimentRevisionError(DecisionDeliveryError):
    pass


class DeliverySignatureError(DecisionDeliveryError):
    pass


class DecisionDeliveryConflictError(DecisionDeliveryError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require_digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise DecisionDeliveryError(
            f"{field} must be lowercase SHA-256 hex"
        )
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise DecisionDeliveryError(
            f"{field} must be a non-empty string"
        )
    return value


def _require_int(value: Any, field: str, *, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise DecisionDeliveryError(
            f"{field} must be an integer >= {minimum}"
        )
    return value


def _require_number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionDeliveryError(f"{field} must be numeric")
    number = float(value)
    if minimum is not None and number < minimum:
        raise DecisionDeliveryError(
            f"{field} must be >= {minimum}"
        )
    if maximum is not None and number > maximum:
        raise DecisionDeliveryError(
            f"{field} must be <= {maximum}"
        )
    return number


def _parse_export_manifest(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "manifest_version",
        "source_class",
        "experiment_id",
        "registry_revision",
        "window",
        "complete",
        "exports",
        "fixture_source_sha256",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise SourceProvenanceError(
            "read-only export manifest fields must match v1 exactly"
        )
    if payload["manifest_version"] != SOURCE_MANIFEST_VERSION:
        raise SourceProvenanceError(
            "unsupported read-only export manifest version"
        )
    source_class = payload["source_class"]
    if source_class not in {
        "provider_export",
        "synthetic_fixture",
    }:
        raise SourceProvenanceError(
            "source_class must be provider_export or synthetic_fixture"
        )
    experiment_id = _require_string(
        payload["experiment_id"],
        "experiment_id",
    )
    revision = _require_int(
        payload["registry_revision"],
        "registry_revision",
        minimum=1,
    )
    window_payload = payload["window"]
    if (
        not isinstance(window_payload, Mapping)
        or set(window_payload) != {"start", "stop"}
    ):
        raise SourceProvenanceError(
            "source window must contain start/stop exactly"
        )
    start = _require_string(window_payload["start"], "window.start")
    stop = _require_string(window_payload["stop"], "window.stop")
    if parse_timestamp(start) >= parse_timestamp(stop):
        raise SourceProvenanceError(
            "source window start must be before stop"
        )
    if payload["complete"] is not True:
        raise IncompleteExportError(
            "decision delivery requires a complete read-only export"
        )
    exports = payload["exports"]
    if not isinstance(exports, list) or not exports:
        raise IncompleteExportError(
            "decision delivery requires export receipts"
        )
    normalized_exports: list[dict[str, Any]] = []
    providers: set[str] = set()
    for raw in exports:
        fields = {
            "provider",
            "export_id",
            "captured_through",
            "complete",
        }
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise SourceProvenanceError(
                "export receipt fields must match v1 exactly"
            )
        provider = _require_string(raw["provider"], "provider")
        export_id = _require_string(raw["export_id"], "export_id")
        captured_through = _require_string(
            raw["captured_through"],
            "captured_through",
        )
        parse_timestamp(captured_through)
        if raw["complete"] is not True:
            raise IncompleteExportError(
                f"provider export {export_id} is partial"
            )
        if provider in providers:
            raise SourceProvenanceError(
                f"duplicate provider export receipt: {provider}"
            )
        providers.add(provider)
        normalized_exports.append({
            "provider": provider,
            "export_id": export_id,
            "captured_through": captured_through,
            "complete": True,
        })

    fixture_hash = payload["fixture_source_sha256"]
    if source_class == "provider_export":
        if providers - LIVE_READ_ONLY_PROVIDERS:
            raise SourceProvenanceError(
                "live provider exports must be read-only vidIQ/Metricool sources"
            )
        if fixture_hash is not None:
            raise SourceProvenanceError(
                "live provider export cannot carry fixture provenance"
            )
    else:
        if providers != {"fixture"}:
            raise SourceProvenanceError(
                "synthetic fixture source must use provider=fixture only"
            )
        _require_digest(
            fixture_hash,
            "fixture_source_sha256",
        )

    return {
        "manifest_version": SOURCE_MANIFEST_VERSION,
        "source_class": source_class,
        "experiment_id": experiment_id,
        "registry_revision": revision,
        "window": {"start": start, "stop": stop},
        "complete": True,
        "exports": sorted(
            normalized_exports,
            key=lambda row: (
                row["provider"],
                row["export_id"],
            ),
        ),
        "fixture_source_sha256": fixture_hash,
    }


def _uncertainty_not_estimable(
    metric: str,
    denominator: str,
) -> dict[str, Any]:
    return {
        "metric": metric,
        "method": "aggregate_export_not_estimable",
        "lower": None,
        "upper": None,
        "denominator": denominator,
        "reason": (
            "Read-only aggregate exports do not contain individual-level "
            "samples required for a defensible interval."
        ),
    }


def _metrics_for(
    events: tuple[AnalyticsEvent, ...],
) -> dict[str, Any]:
    metrics = aggregate_events(events)
    cohort = aggregate_retention_cohort(
        "decision-delivery",
        events,
    )
    ctr_uncertainty = wilson_interval(
        metrics.clicks,
        metrics.impressions,
    )
    return {
        "denominators": {
            "impressions": metrics.impressions,
            "views": metrics.views,
            "retention_views": cohort.total_views,
            "event_count": len(events),
        },
        "numerators": {
            "clicks": metrics.clicks,
            "watch_time_seconds": round(
                float(metrics.watch_time_seconds),
                8,
            ),
        },
        "metrics": {
            "ctr": round(metrics.ctr, 8),
            "average_watch_time_seconds": round(
                metrics.average_watch_time_seconds,
                8,
            ),
            "retention_auc": round(
                retention_auc(cohort.curve),
                8,
            ),
            "retention_curve": [
                {
                    "position": round(point.position, 8),
                    "retained": round(point.retained, 8),
                }
                for point in cohort.curve
            ],
        },
        "uncertainty": {
            "ctr": {
                "method": ctr_uncertainty.method,
                "estimate": ctr_uncertainty.estimate,
                "lower": ctr_uncertainty.lower,
                "upper": ctr_uncertainty.upper,
                "half_width": ctr_uncertainty.half_width,
                "sample_size": ctr_uncertainty.sample_size,
                "confidence": ctr_uncertainty.confidence,
                "causal": False,
                "interpretation":
                    ctr_uncertainty.interpretation,
            },
            "average_watch_time_seconds":
                _uncertainty_not_estimable(
                    "average_watch_time_seconds",
                    "views",
                ),
            "retention_auc":
                _uncertainty_not_estimable(
                    "retention_auc",
                    "retention_views",
                ),
        },
    }


def build_source_snapshot(
    *,
    events: Iterable[AnalyticsEvent],
    export_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = _parse_export_manifest(export_manifest)
    try:
        canonical = canonical_event_set(events)
    except WindowFinalizationConflictError as exc:
        raise SourceProvenanceError(
            "conflicting provider event identity"
        ) from exc

    provider_set = {event.provider for event in canonical}
    manifest_providers = {
        item["provider"] for item in manifest["exports"]
    }
    if provider_set != manifest_providers:
        raise SourceProvenanceError(
            "event providers do not match export receipts"
        )

    start = parse_timestamp(
        manifest["window"]["start"]
    )
    stop = parse_timestamp(
        manifest["window"]["stop"]
    )
    selected = tuple(
        event
        for event in canonical
        if start <= parse_timestamp(event.captured_at) < stop
    )
    if not selected:
        raise SourceProvenanceError(
            "complete export contains no in-window analytics events"
        )

    variants = sorted(
        {
            event.variant_id
            for event in selected
            if event.variant_id is not None
        }
    )
    by_variant = {}
    for variant_id in variants:
        variant_events = tuple(
            event
            for event in selected
            if event.variant_id == variant_id
        )
        by_variant[variant_id] = _metrics_for(
            variant_events
        )

    source_class = manifest["source_class"]
    snapshot = {
        "snapshot_version": SOURCE_SNAPSHOT_VERSION,
        "experiment_id": manifest["experiment_id"],
        "registry_revision":
            manifest["registry_revision"],
        "window": dict(manifest["window"]),
        "source": {
            "source_class": source_class,
            "providers": sorted(provider_set),
            "export_manifest_digest":
                sha256_json(manifest),
            "exports": list(manifest["exports"]),
            "fixture_source_sha256":
                manifest["fixture_source_sha256"],
            "live_performance_claim_allowed":
                source_class == "provider_export",
            "observational": True,
            "interpretation": CAUSALITY_NOTICE,
        },
        "event_set": {
            "event_count": len(selected),
            "event_set_digest": event_set_digest(
                selected
            ),
            "event_keys": [
                event.idempotency_key
                for event in selected
            ],
        },
        "overall": _metrics_for(selected),
        "by_variant": by_variant,
    }
    snapshot["snapshot_digest"] = sha256_json(
        snapshot
    )
    return json.loads(canonical_json(snapshot))


def _validate_source_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "snapshot_version",
        "experiment_id",
        "registry_revision",
        "window",
        "source",
        "event_set",
        "overall",
        "by_variant",
        "snapshot_digest",
    }
    if not isinstance(snapshot, Mapping) or set(snapshot) != expected:
        raise SourceProvenanceError(
            "source snapshot fields must match v1 exactly"
        )
    if snapshot["snapshot_version"] != SOURCE_SNAPSHOT_VERSION:
        raise SourceProvenanceError(
            "unsupported source snapshot version"
        )
    provided = _require_digest(
        snapshot["snapshot_digest"],
        "snapshot_digest",
    )
    material = dict(snapshot)
    material.pop("snapshot_digest")
    if sha256_json(material) != provided:
        raise SourceProvenanceError(
            "source snapshot digest mismatch"
        )
    source = snapshot["source"]
    if not isinstance(source, Mapping):
        raise SourceProvenanceError(
            "source snapshot provenance missing"
        )
    source_class = source.get("source_class")
    live = source.get(
        "live_performance_claim_allowed"
    )
    if source_class == "synthetic_fixture":
        if live is not False:
            raise SourceProvenanceError(
                "synthetic fixture cannot claim live performance"
            )
        _require_digest(
            source.get("fixture_source_sha256"),
            "fixture_source_sha256",
        )
    elif source_class == "provider_export":
        if live is not True:
            raise SourceProvenanceError(
                "complete provider export must declare live performance scope"
            )
        providers = source.get("providers")
        if (
            not isinstance(providers, list)
            or not providers
            or set(providers) - LIVE_READ_ONLY_PROVIDERS
        ):
            raise SourceProvenanceError(
                "live snapshot providers are invalid"
            )
        if source.get("fixture_source_sha256") is not None:
            raise SourceProvenanceError(
                "live snapshot carries fixture provenance"
            )
    else:
        raise SourceProvenanceError(
            "unsupported source_class"
        )
    return json.loads(canonical_json(dict(snapshot)))


def build_signed_delivery_audit(
    *,
    decision_handoff: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    producer_sha: str,
    signing_key: bytes,
    key_id: str,
) -> dict[str, Any]:
    try:
        handoff = parse_decision_handoff(
            decision_handoff
        )
    except DecisionHandoffError as exc:
        raise DecisionDeliveryError(
            "invalid growth.decision_handoff.v1"
        ) from exc
    snapshot = _validate_source_snapshot(
        source_snapshot
    )
    _require_digest(producer_sha, "producer_sha")
    _require_string(key_id, "key_id")
    if (
        not isinstance(signing_key, bytes)
        or not signing_key
    ):
        raise DecisionDeliveryError(
            "signing_key must be non-empty bytes"
        )

    registry = handoff["registry"]
    if snapshot["experiment_id"] != registry["experiment_id"]:
        raise SourceProvenanceError(
            "source experiment_id does not match decision handoff"
        )
    if (
        snapshot["registry_revision"]
        != registry["revision"]
    ):
        raise StaleExperimentRevisionError(
            "source snapshot registry revision is stale"
        )
    if snapshot["window"] != handoff["provenance"]["window"]:
        raise SourceProvenanceError(
            "source snapshot window does not match audited decision window"
        )

    source_class = snapshot["source"]["source_class"]
    if source_class == "synthetic_fixture":
        if not key_id.startswith("fixture:"):
            raise SourceProvenanceError(
                "synthetic fixture must use fixture-only signing key id"
            )
    else:
        if key_id.startswith("fixture:"):
            raise SourceProvenanceError(
                "live provider export cannot use fixture signing key id"
            )

    material = {
        "delivery_audit_version": DELIVERY_AUDIT_VERSION,
        "producer": {
            "repository": "foto6/video3",
            "producer_sha": producer_sha,
        },
        "decision": {
            "handoff_version": handoff["handoff_version"],
            "handoff_id": handoff["handoff_id"],
            "handoff_digest": handoff["handoff_digest"],
            "audit_bundle_digest":
                handoff["audit_bundle_digest"],
            "classification": handoff["classification"],
            "registry_revision":
                handoff["registry"]["revision"],
            "registry_freeze_hash":
                handoff["registry"]["freeze_hash"],
        },
        "source_snapshot": snapshot,
        "safeguards": {
            "integrity":
                json.loads(canonical_json(
                    handoff["integrity"]
                )),
            "multiplicity":
                json.loads(canonical_json(
                    handoff["multiplicity"]
                )),
            "guardrails":
                json.loads(canonical_json(
                    handoff["guardrails"]
                )),
            "auto_publish": False,
            "external_mutation": False,
            "release_authorized": False,
            "publish_authorized": False,
        },
    }
    content_digest = sha256_json(material)
    signed_material = {
        **material,
        "content_digest": content_digest,
    }
    signature = hmac.new(
        signing_key,
        canonical_json(signed_material).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    bundle = {
        **signed_material,
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "key_id": key_id,
            "value": signature,
        },
    }
    bundle["bundle_digest"] = sha256_json(bundle)
    return json.loads(canonical_json(bundle))


def verify_signed_delivery_audit(
    payload: Mapping[str, Any],
    *,
    verification_keys: Mapping[str, bytes],
) -> dict[str, Any]:
    expected = {
        "delivery_audit_version",
        "producer",
        "decision",
        "source_snapshot",
        "safeguards",
        "content_digest",
        "signature",
        "bundle_digest",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DeliverySignatureError(
            "signed delivery audit fields must match v1 exactly"
        )
    if payload["delivery_audit_version"] != DELIVERY_AUDIT_VERSION:
        raise DeliverySignatureError(
            "unsupported delivery audit version"
        )
    bundle_digest = _require_digest(
        payload["bundle_digest"],
        "bundle_digest",
    )
    outer = dict(payload)
    outer.pop("bundle_digest")
    if sha256_json(outer) != bundle_digest:
        raise DeliverySignatureError(
            "delivery audit bundle digest mismatch"
        )

    signature = payload["signature"]
    if (
        not isinstance(signature, Mapping)
        or set(signature)
        != {"algorithm", "key_id", "value"}
    ):
        raise DeliverySignatureError(
            "delivery audit signature fields invalid"
        )
    if signature["algorithm"] != SIGNATURE_ALGORITHM:
        raise DeliverySignatureError(
            "unsupported delivery audit signature algorithm"
        )
    key_id = _require_string(
        signature["key_id"],
        "signature.key_id",
    )
    value = _require_digest(
        signature["value"],
        "signature.value",
    )
    key = verification_keys.get(key_id)
    if not isinstance(key, bytes) or not key:
        raise DeliverySignatureError(
            "untrusted delivery audit signing key"
        )

    signed_material = dict(payload)
    signed_material.pop("signature")
    signed_material.pop("bundle_digest")
    content_digest = _require_digest(
        signed_material["content_digest"],
        "content_digest",
    )
    material = dict(signed_material)
    material.pop("content_digest")
    if sha256_json(material) != content_digest:
        raise DeliverySignatureError(
            "delivery audit content digest mismatch"
        )
    expected_signature = hmac.new(
        key,
        canonical_json(signed_material).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(
        expected_signature,
        value,
    ):
        raise DeliverySignatureError(
            "delivery audit signature mismatch"
        )

    snapshot = _validate_source_snapshot(
        payload["source_snapshot"]
    )
    source_class = snapshot["source"]["source_class"]
    if source_class == "synthetic_fixture":
        if not key_id.startswith("fixture:"):
            raise DeliverySignatureError(
                "fixture audit signed by non-fixture key"
            )
    elif key_id.startswith("fixture:"):
        raise DeliverySignatureError(
            "live audit signed by fixture-only key"
        )

    safeguards = payload["safeguards"]
    if (
        not isinstance(safeguards, Mapping)
        or safeguards.get("auto_publish") is not False
        or safeguards.get("external_mutation") is not False
        or safeguards.get("release_authorized") is not False
        or safeguards.get("publish_authorized") is not False
    ):
        raise DeliverySignatureError(
            "delivery audit authority boundary is invalid"
        )
    return json.loads(canonical_json(dict(payload)))


def build_creator_decision_seed(
    delivery_audit: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(delivery_audit, Mapping):
        raise DecisionDeliveryError(
            "delivery audit must be an object"
        )
    decision = delivery_audit.get("decision")
    snapshot = delivery_audit.get("source_snapshot")
    signature = delivery_audit.get("signature")
    if (
        not isinstance(decision, Mapping)
        or not isinstance(snapshot, Mapping)
        or not isinstance(signature, Mapping)
    ):
        raise DecisionDeliveryError(
            "delivery audit components missing"
        )
    audit_digest = _require_digest(
        delivery_audit.get("bundle_digest"),
        "delivery audit bundle_digest",
    )
    content_digest = _require_digest(
        delivery_audit.get("content_digest"),
        "delivery audit content_digest",
    )
    delivery_material = {
        "delivery_audit_bundle_digest": audit_digest,
        "decision_handoff_digest":
            decision.get("handoff_digest"),
        "experiment_id": snapshot.get("experiment_id"),
        "registry_revision":
            snapshot.get("registry_revision"),
    }
    delivery_id = "gcd1:" + sha256_json(
        delivery_material
    )
    seed = {
        "contract_version": CREATOR_DECISION_SEED_VERSION,
        "delivery_id": delivery_id,
        "idempotency_key": delivery_id,
        "experiment_id": snapshot["experiment_id"],
        "registry_revision":
            snapshot["registry_revision"],
        "decision_classification":
            decision["classification"],
        "decision_handoff_digest":
            decision["handoff_digest"],
        "experiment_audit_bundle_digest":
            decision["audit_bundle_digest"],
        "delivery_audit_bundle_digest":
            audit_digest,
        "delivery_audit_content_digest":
            content_digest,
        "source_class":
            snapshot["source"]["source_class"],
        "live_performance_claim_allowed":
            snapshot["source"][
                "live_performance_claim_allowed"
            ],
        "signature": json.loads(
            canonical_json(dict(signature))
        ),
        "payload": json.loads(
            canonical_json(dict(delivery_audit))
        ),
        "authority": {
            "auto_publish": False,
            "external_mutation": False,
            "release_authorized": False,
            "publish_authorized": False,
            "requires_creator_release_authorization": True,
        },
    }
    seed["seed_digest"] = sha256_json(seed)
    return json.loads(canonical_json(seed))


def _validate_seed_shape(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "delivery_id",
        "idempotency_key",
        "experiment_id",
        "registry_revision",
        "decision_classification",
        "decision_handoff_digest",
        "experiment_audit_bundle_digest",
        "delivery_audit_bundle_digest",
        "delivery_audit_content_digest",
        "source_class",
        "live_performance_claim_allowed",
        "signature",
        "payload",
        "authority",
        "seed_digest",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise DecisionDeliveryError(
            "Creator decision seed fields must match v1 exactly"
        )
    if payload["contract_version"] != CREATOR_DECISION_SEED_VERSION:
        raise DecisionDeliveryError(
            "unsupported Creator decision seed contract version"
        )
    delivery_id = _require_string(
        payload["delivery_id"],
        "delivery_id",
    )
    if payload["idempotency_key"] != delivery_id:
        raise DecisionDeliveryError(
            "idempotency_key must equal delivery_id"
        )
    revision = _require_int(
        payload["registry_revision"],
        "registry_revision",
        minimum=1,
    )
    _require_string(
        payload["experiment_id"],
        "experiment_id",
    )
    for field in (
        "decision_handoff_digest",
        "experiment_audit_bundle_digest",
        "delivery_audit_bundle_digest",
        "delivery_audit_content_digest",
    ):
        _require_digest(payload[field], field)

    provided = _require_digest(
        payload["seed_digest"],
        "seed_digest",
    )
    material = dict(payload)
    material.pop("seed_digest")
    if sha256_json(material) != provided:
        raise DecisionDeliveryError(
            "Creator decision seed digest mismatch"
        )

    audit = payload["payload"]
    if not isinstance(audit, Mapping):
        raise DecisionDeliveryError(
            "Creator decision seed payload missing"
        )
    if audit.get("bundle_digest") != payload[
        "delivery_audit_bundle_digest"
    ]:
        raise DecisionDeliveryError(
            "seed delivery audit digest mismatch"
        )
    if audit.get("content_digest") != payload[
        "delivery_audit_content_digest"
    ]:
        raise DecisionDeliveryError(
            "seed delivery audit content digest mismatch"
        )
    decision = audit.get("decision")
    snapshot = audit.get("source_snapshot")
    if (
        not isinstance(decision, Mapping)
        or not isinstance(snapshot, Mapping)
    ):
        raise DecisionDeliveryError(
            "seed delivery audit components missing"
        )
    if decision.get("handoff_digest") != payload[
        "decision_handoff_digest"
    ]:
        raise DecisionDeliveryError(
            "seed decision handoff digest mismatch"
        )
    if decision.get("audit_bundle_digest") != payload[
        "experiment_audit_bundle_digest"
    ]:
        raise DecisionDeliveryError(
            "seed experiment audit digest mismatch"
        )
    if snapshot.get("experiment_id") != payload[
        "experiment_id"
    ]:
        raise DecisionDeliveryError(
            "seed experiment identity mismatch"
        )
    if snapshot.get("registry_revision") != revision:
        raise DecisionDeliveryError(
            "seed registry revision mismatch"
        )
    if snapshot.get("source", {}).get(
        "source_class"
    ) != payload["source_class"]:
        raise DecisionDeliveryError(
            "seed source_class mismatch"
        )
    if snapshot.get("source", {}).get(
        "live_performance_claim_allowed"
    ) is not payload[
        "live_performance_claim_allowed"
    ]:
        raise DecisionDeliveryError(
            "seed live performance scope mismatch"
        )

    expected_id = "gcd1:" + sha256_json({
        "delivery_audit_bundle_digest":
            payload["delivery_audit_bundle_digest"],
        "decision_handoff_digest":
            payload["decision_handoff_digest"],
        "experiment_id": payload["experiment_id"],
        "registry_revision": revision,
    })
    if delivery_id != expected_id:
        raise DecisionDeliveryError(
            "Creator decision delivery_id mismatch"
        )

    authority = payload["authority"]
    if (
        not isinstance(authority, Mapping)
        or authority.get("auto_publish") is not False
        or authority.get("external_mutation") is not False
        or authority.get("release_authorized") is not False
        or authority.get("publish_authorized") is not False
        or authority.get(
            "requires_creator_release_authorization"
        ) is not True
    ):
        raise DecisionDeliveryError(
            "Creator decision seed authority is invalid"
        )
    return json.loads(canonical_json(dict(payload)))


def validate_creator_decision_seed(
    payload: Mapping[str, Any],
    *,
    verification_keys: Mapping[str, bytes],
    expected_registry_revision: int,
    allow_synthetic_fixture: bool = False,
) -> dict[str, Any]:
    seed = _validate_seed_shape(payload)
    if seed["registry_revision"] != expected_registry_revision:
        raise StaleExperimentRevisionError(
            "Creator expected a different experiment registry revision"
        )
    audit = verify_signed_delivery_audit(
        seed["payload"],
        verification_keys=verification_keys,
    )
    if (
        audit["bundle_digest"]
        != seed["delivery_audit_bundle_digest"]
    ):
        raise DecisionDeliveryError(
            "verified delivery audit digest changed"
        )
    if seed["source_class"] == "synthetic_fixture":
        if seed["live_performance_claim_allowed"] is not False:
            raise SourceProvenanceError(
                "synthetic fixture cannot claim live performance"
            )
        if not allow_synthetic_fixture:
            raise SourceProvenanceError(
                "synthetic fixture decision seed is conformance-only"
            )
    elif seed["source_class"] == "provider_export":
        if seed["live_performance_claim_allowed"] is not True:
            raise SourceProvenanceError(
                "live provider decision seed lacks live source scope"
            )
    else:
        raise SourceProvenanceError(
            "unsupported Creator seed source_class"
        )
    return seed


class DecisionDeliveryOutbox:
    """Durable prepare/ack outbox for one logical Growth -> Creator seed."""

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)
        self._prepared: dict[str, dict[str, Any]] = {}
        self._acked: set[str] = set()
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
                raise DecisionDeliveryConflictError(
                    f"invalid outbox JSON on line {line_number}"
                ) from exc
            expected = {
                "ledger_version",
                "sequence",
                "event_type",
                "delivery_id",
                "seed_digest",
                "seed",
            }
            if (
                not isinstance(row, Mapping)
                or set(row) != expected
            ):
                raise DecisionDeliveryConflictError(
                    "outbox row fields are invalid"
                )
            if row["ledger_version"] != OUTBOX_LEDGER_VERSION:
                raise DecisionDeliveryConflictError(
                    "unsupported outbox version"
                )
            if row["sequence"] != self._sequence + 1:
                raise DecisionDeliveryConflictError(
                    "outbox sequence is not contiguous"
                )
            delivery_id = row["delivery_id"]
            seed_digest = row["seed_digest"]
            if row["event_type"] == "prepare":
                if row["seed"] is None:
                    raise DecisionDeliveryConflictError(
                        "prepare row must contain seed"
                    )
                parsed = _validate_seed_shape(row["seed"])
                if (
                    parsed["delivery_id"] != delivery_id
                    or parsed["seed_digest"] != seed_digest
                ):
                    raise DecisionDeliveryConflictError(
                        "outbox prepare binding mismatch"
                    )
                previous = self._prepared.get(delivery_id)
                if previous is not None:
                    raise DecisionDeliveryConflictError(
                        "duplicate durable prepare row"
                    )
                self._prepared[delivery_id] = parsed
            elif row["event_type"] == "ack":
                if row["seed"] is not None:
                    raise DecisionDeliveryConflictError(
                        "ack row cannot contain seed"
                    )
                prepared = self._prepared.get(delivery_id)
                if prepared is None:
                    raise DecisionDeliveryConflictError(
                        "ack without durable prepare"
                    )
                if prepared["seed_digest"] != seed_digest:
                    raise DecisionDeliveryConflictError(
                        "ack seed digest mismatch"
                    )
                if delivery_id in self._acked:
                    raise DecisionDeliveryConflictError(
                        "duplicate durable ack row"
                    )
                self._acked.add(delivery_id)
            else:
                raise DecisionDeliveryConflictError(
                    "unknown outbox event_type"
                )
            self._sequence += 1

    def _append(
        self,
        row: Mapping[str, Any],
    ) -> None:
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence += 1

    def prepare(
        self,
        seed: Mapping[str, Any],
        *,
        expected_registry_revision: int,
    ) -> str:
        parsed = _validate_seed_shape(seed)
        if parsed["registry_revision"] != expected_registry_revision:
            raise StaleExperimentRevisionError(
                "cannot prepare stale experiment revision"
            )
        delivery_id = parsed["delivery_id"]
        existing = self._prepared.get(delivery_id)
        if existing is not None:
            if existing["seed_digest"] != parsed["seed_digest"]:
                raise DecisionDeliveryConflictError(
                    "delivery identity changed payload"
                )
            return "duplicate"
        row = {
            "ledger_version": OUTBOX_LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "event_type": "prepare",
            "delivery_id": delivery_id,
            "seed_digest": parsed["seed_digest"],
            "seed": parsed,
        }
        self._append(row)
        self._prepared[delivery_id] = parsed
        return "prepared"

    def acknowledge(
        self,
        delivery_id: str,
        seed_digest: str,
    ) -> str:
        prepared = self._prepared.get(delivery_id)
        if prepared is None:
            raise DecisionDeliveryConflictError(
                "cannot acknowledge unknown delivery"
            )
        if prepared["seed_digest"] != seed_digest:
            raise DecisionDeliveryConflictError(
                "acknowledgement seed digest mismatch"
            )
        if delivery_id in self._acked:
            return "duplicate"
        row = {
            "ledger_version": OUTBOX_LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "event_type": "ack",
            "delivery_id": delivery_id,
            "seed_digest": seed_digest,
            "seed": None,
        }
        self._append(row)
        self._acked.add(delivery_id)
        return "acknowledged"

    def pending(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            self._prepared[delivery_id]
            for delivery_id in sorted(self._prepared)
            if delivery_id not in self._acked
        )

    @property
    def logical_delivery_count(self) -> int:
        return len(self._prepared)


class CreatorDecisionConsumerLedger:
    """Reference fail-closed Creator consumer for the versioned seed contract."""

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)
        self._rows: dict[str, dict[str, Any]] = {}
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
                raise DecisionDeliveryConflictError(
                    f"invalid consumer ledger JSON on line {line_number}"
                ) from exc
            expected = {
                "ledger_version",
                "sequence",
                "delivery_id",
                "seed_digest",
                "delivery_audit_bundle_digest",
                "status",
            }
            if (
                not isinstance(row, Mapping)
                or set(row) != expected
            ):
                raise DecisionDeliveryConflictError(
                    "consumer ledger row fields invalid"
                )
            if row["ledger_version"] != CONSUMER_LEDGER_VERSION:
                raise DecisionDeliveryConflictError(
                    "unsupported consumer ledger version"
                )
            if row["sequence"] != len(self._rows) + 1:
                raise DecisionDeliveryConflictError(
                    "consumer ledger sequence is not contiguous"
                )
            if row["status"] != "accepted":
                raise DecisionDeliveryConflictError(
                    "consumer ledger status invalid"
                )
            delivery_id = row["delivery_id"]
            if delivery_id in self._rows:
                raise DecisionDeliveryConflictError(
                    "duplicate durable consumer delivery"
                )
            self._rows[delivery_id] = dict(row)

    def consume(
        self,
        seed: Mapping[str, Any],
        *,
        verification_keys: Mapping[str, bytes],
        expected_registry_revision: int,
        allow_synthetic_fixture: bool = False,
    ) -> str:
        parsed = validate_creator_decision_seed(
            seed,
            verification_keys=verification_keys,
            expected_registry_revision=
                expected_registry_revision,
            allow_synthetic_fixture=
                allow_synthetic_fixture,
        )
        delivery_id = parsed["delivery_id"]
        existing = self._rows.get(delivery_id)
        if existing is not None:
            if (
                existing["seed_digest"]
                != parsed["seed_digest"]
                or existing[
                    "delivery_audit_bundle_digest"
                ]
                != parsed[
                    "delivery_audit_bundle_digest"
                ]
            ):
                raise DecisionDeliveryConflictError(
                    "Creator delivery identity changed"
                )
            return "duplicate"

        row = {
            "ledger_version": CONSUMER_LEDGER_VERSION,
            "sequence": len(self._rows) + 1,
            "delivery_id": delivery_id,
            "seed_digest": parsed["seed_digest"],
            "delivery_audit_bundle_digest":
                parsed["delivery_audit_bundle_digest"],
            "status": "accepted",
        }
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._rows[delivery_id] = row
        return "accepted"

    @property
    def accepted_count(self) -> int:
        return len(self._rows)
