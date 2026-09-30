from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .decision_handoff import (
    DECISION_HANDOFF_VERSION,
    DecisionHandoffError,
    parse_decision_handoff,
)
from .event_stream import parse_timestamp
from .experiment import wilson_interval


PUBLISH_RESULT_VERSION = "growth.shortform_publish_result.v1"
PLATFORM_METRICS_VERSION = "growth.shortform_platform_metrics.v1"
METRIC_SNAPSHOT_VERSION = "growth.shortform_metric_snapshot.v1"
NEXT_CYCLE_SEED_VERSION = "growth.reels_next_cycle_seed.v1"
INGEST_LEDGER_VERSION = "growth.reels_feedback_ledger.v1"
OUTBOX_LEDGER_VERSION = "growth.reels_next_cycle_outbox.v1"
CONSUMER_LEDGER_VERSION = "growth.reels_creator_consumer_ledger.v1"

PLATFORMS = frozenset({
    "instagram_reels",
    "tiktok",
    "youtube_shorts",
})
SOURCE_CLASSES = frozenset({
    "platform_export",
    "synthetic_fixture",
})
OPTIONAL_METRICS = frozenset({
    "impressions",
    "views",
    "watch_time_seconds",
    "average_watch_duration_seconds",
    "completed_views",
    "completion_rate",
    "retention_points",
    "retention_denominator_views",
    "likes",
    "comments",
    "shares",
    "saves",
    "follows",
    "link_clicks",
})


class AutonomousReelsError(ValueError):
    pass


class PublishResultError(AutonomousReelsError):
    pass


class PlatformMetricsError(AutonomousReelsError):
    pass


class IncompleteMetricsError(PlatformMetricsError):
    pass


class StaleCycleRevisionError(AutonomousReelsError):
    pass


class ReelsDeliveryConflictError(AutonomousReelsError):
    pass


class SyntheticEvidenceRejected(AutonomousReelsError):
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


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise AutonomousReelsError(
            f"{field} must be lowercase SHA-256 hex"
        )
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AutonomousReelsError(
            f"{field} must be a non-empty string"
        )
    return value


def _integer(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise AutonomousReelsError(
            f"{field} must be an integer >= {minimum}"
        )
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AutonomousReelsError(
            f"{field} must be numeric"
        )
    number = float(value)
    if minimum is not None and number < minimum:
        raise AutonomousReelsError(
            f"{field} must be >= {minimum}"
        )
    if maximum is not None and number > maximum:
        raise AutonomousReelsError(
            f"{field} must be <= {maximum}"
        )
    return number


def _window(payload: Any) -> dict[str, str]:
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"start", "end"}
    ):
        raise AutonomousReelsError(
            "window must contain start/end exactly"
        )
    start = _string(payload["start"], "window.start")
    end = _string(payload["end"], "window.end")
    if parse_timestamp(start) >= parse_timestamp(end):
        raise AutonomousReelsError(
            "window.start must be before window.end"
        )
    return {"start": start, "end": end}


def build_publish_result(
    *,
    source_class: str,
    platform: str,
    account_id: str,
    post_id: str,
    published_at: str,
    captured_at: str,
    cycle_revision: int,
    creative_artifact_id: str,
    creative_artifact_digest: str,
    media_artifact_id: str,
    media_artifact_digest: str,
    media_render_fingerprint: str,
    media_duration_seconds: float,
    provider_receipt_digest: str | None,
    fixture_source_sha256: str | None,
) -> dict[str, Any]:
    if source_class not in SOURCE_CLASSES:
        raise PublishResultError("unsupported publish source_class")
    if platform not in PLATFORMS:
        raise PublishResultError("unsupported short-form platform")
    for value, field in (
        (account_id, "account_id"),
        (post_id, "post_id"),
        (creative_artifact_id, "creative_artifact_id"),
        (media_artifact_id, "media_artifact_id"),
        (media_render_fingerprint, "media_render_fingerprint"),
    ):
        _string(value, field)
    _digest(creative_artifact_digest, "creative_artifact_digest")
    _digest(media_artifact_digest, "media_artifact_digest")
    _number(
        media_duration_seconds,
        "media_duration_seconds",
        minimum=0.000001,
    )
    revision = _integer(
        cycle_revision,
        "cycle_revision",
        minimum=1,
    )
    parse_timestamp(published_at)
    parse_timestamp(captured_at)
    if parse_timestamp(captured_at) < parse_timestamp(published_at):
        raise PublishResultError(
            "publish result captured_at cannot precede published_at"
        )

    if source_class == "platform_export":
        _digest(
            provider_receipt_digest,
            "provider_receipt_digest",
        )
        if fixture_source_sha256 is not None:
            raise PublishResultError(
                "live publish result cannot carry fixture provenance"
            )
        live = True
    else:
        if provider_receipt_digest is not None:
            raise PublishResultError(
                "synthetic publish result cannot carry live receipt digest"
            )
        _digest(
            fixture_source_sha256,
            "fixture_source_sha256",
        )
        live = False

    material = {
        "contract_version": PUBLISH_RESULT_VERSION,
        "source_class": source_class,
        "platform": platform,
        "account_id": account_id,
        "post_id": post_id,
        "published_at": published_at,
        "captured_at": captured_at,
        "cycle_revision": revision,
        "artifact": {
            "creative_artifact_id": creative_artifact_id,
            "creative_artifact_digest": creative_artifact_digest,
            "media_artifact_id": media_artifact_id,
            "media_artifact_digest": media_artifact_digest,
            "media_render_fingerprint": media_render_fingerprint,
            "media_duration_seconds": float(media_duration_seconds),
        },
        "provenance": {
            "provider_receipt_digest": provider_receipt_digest,
            "fixture_source_sha256": fixture_source_sha256,
            "live_performance_claim_allowed": live,
        },
    }
    identity = {
        "platform": platform,
        "account_id": account_id,
        "post_id": post_id,
        "cycle_revision": revision,
        "media_artifact_digest": media_artifact_digest,
    }
    material["publish_result_id"] = "spr1:" + sha256_json(identity)
    material["publish_result_digest"] = sha256_json(material)
    return json.loads(canonical_json(material))


def parse_publish_result(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "publish_result_id",
        "publish_result_digest",
        "source_class",
        "platform",
        "account_id",
        "post_id",
        "published_at",
        "captured_at",
        "cycle_revision",
        "artifact",
        "provenance",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise PublishResultError(
            "publish-result fields must match v1 exactly"
        )
    artifact = payload["artifact"]
    provenance = payload["provenance"]
    if (
        not isinstance(artifact, Mapping)
        or set(artifact)
        != {
            "creative_artifact_id",
            "creative_artifact_digest",
            "media_artifact_id",
            "media_artifact_digest",
            "media_render_fingerprint",
            "media_duration_seconds",
        }
    ):
        raise PublishResultError("artifact fields invalid")
    if (
        not isinstance(provenance, Mapping)
        or set(provenance)
        != {
            "provider_receipt_digest",
            "fixture_source_sha256",
            "live_performance_claim_allowed",
        }
    ):
        raise PublishResultError("publish provenance fields invalid")
    rebuilt = build_publish_result(
        source_class=payload["source_class"],
        platform=payload["platform"],
        account_id=payload["account_id"],
        post_id=payload["post_id"],
        published_at=payload["published_at"],
        captured_at=payload["captured_at"],
        cycle_revision=payload["cycle_revision"],
        creative_artifact_id=artifact["creative_artifact_id"],
        creative_artifact_digest=artifact["creative_artifact_digest"],
        media_artifact_id=artifact["media_artifact_id"],
        media_artifact_digest=artifact["media_artifact_digest"],
        media_render_fingerprint=artifact["media_render_fingerprint"],
        media_duration_seconds=artifact["media_duration_seconds"],
        provider_receipt_digest=provenance["provider_receipt_digest"],
        fixture_source_sha256=provenance["fixture_source_sha256"],
    )
    if payload != rebuilt:
        raise PublishResultError(
            "publish-result identity or digest mismatch"
        )
    return rebuilt


def _normalize_retention(value: Any) -> list[dict[str, float]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise PlatformMetricsError(
            "retention_points must be null or non-empty array"
        )
    points: list[dict[str, float]] = []
    last = -1.0
    for raw in value:
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"position", "retained"}
        ):
            raise PlatformMetricsError(
                "retention point fields must be position/retained"
            )
        position = _number(
            raw["position"],
            "retention.position",
            minimum=0.0,
            maximum=1.0,
        )
        retained = _number(
            raw["retained"],
            "retention.retained",
            minimum=0.0,
            maximum=1.0,
        )
        if position <= last:
            raise PlatformMetricsError(
                "retention positions must be strictly increasing"
            )
        last = position
        points.append({
            "position": round(position, 8),
            "retained": round(retained, 8),
        })
    return points


def build_platform_metrics_event(
    *,
    source_class: str,
    platform: str,
    account_id: str,
    post_id: str,
    cycle_revision: int,
    captured_at: str,
    window_start: str,
    window_end: str,
    complete: bool,
    available_metrics: Sequence[str],
    metrics: Mapping[str, Any],
    export_id: str,
    export_digest: str,
    fixture_source_sha256: str | None,
) -> dict[str, Any]:
    if source_class not in SOURCE_CLASSES:
        raise PlatformMetricsError("unsupported metrics source_class")
    if platform not in PLATFORMS:
        raise PlatformMetricsError("unsupported short-form platform")
    for value, field in (
        (account_id, "account_id"),
        (post_id, "post_id"),
        (export_id, "export_id"),
    ):
        _string(value, field)
    revision = _integer(
        cycle_revision,
        "cycle_revision",
        minimum=1,
    )
    parse_timestamp(captured_at)
    window = _window({
        "start": window_start,
        "end": window_end,
    })
    if not isinstance(complete, bool):
        raise PlatformMetricsError("complete must be boolean")
    if (
        not isinstance(available_metrics, Sequence)
        or isinstance(available_metrics, (str, bytes))
    ):
        raise PlatformMetricsError(
            "available_metrics must be an array"
        )
    available = tuple(sorted(set(available_metrics)))
    if len(available) != len(available_metrics):
        raise PlatformMetricsError(
            "available_metrics must be unique"
        )
    if set(available) - OPTIONAL_METRICS:
        raise PlatformMetricsError(
            "available_metrics contains unknown metric"
        )
    if not isinstance(metrics, Mapping) or set(metrics) != OPTIONAL_METRICS:
        raise PlatformMetricsError(
            "metrics must contain every v1 metric key exactly"
        )

    normalized: dict[str, Any] = {}
    for name in sorted(OPTIONAL_METRICS):
        value = metrics[name]
        should_exist = name in available
        if not should_exist:
            if value is not None:
                raise PlatformMetricsError(
                    f"{name} must be null when unavailable"
                )
            normalized[name] = None
            continue
        if value is None:
            raise PlatformMetricsError(
                f"{name} is declared available but null"
            )
        if name in {
            "watch_time_seconds",
            "average_watch_duration_seconds",
        }:
            normalized[name] = round(
                _number(value, name, minimum=0.0),
                8,
            )
        elif name == "completion_rate":
            normalized[name] = round(
                _number(value, name, minimum=0.0, maximum=1.0),
                8,
            )
        elif name == "retention_points":
            normalized[name] = _normalize_retention(value)
        else:
            normalized[name] = _integer(
                value,
                name,
                minimum=0,
            )

    views = normalized.get("views")
    completed = normalized.get("completed_views")
    direct_completion = normalized.get("completion_rate")
    if completed is not None and views is not None and completed > views:
        raise PlatformMetricsError(
            "completed_views cannot exceed views"
        )
    retention_denominator = normalized.get(
        "retention_denominator_views"
    )
    if normalized.get("retention_points") is not None:
        if retention_denominator is None:
            raise PlatformMetricsError(
                "retention points require retention_denominator_views"
            )
    impressions = normalized.get("impressions")
    link_clicks = normalized.get("link_clicks")
    if (
        link_clicks is not None
        and impressions is not None
        and link_clicks > impressions
    ):
        raise PlatformMetricsError(
            "link_clicks cannot exceed impressions"
        )

    _digest(export_digest, "export_digest")
    if source_class == "platform_export":
        if fixture_source_sha256 is not None:
            raise PlatformMetricsError(
                "live metrics cannot carry fixture provenance"
            )
        provider = platform
        live = True
    else:
        _digest(
            fixture_source_sha256,
            "fixture_source_sha256",
        )
        provider = "fixture"
        live = False

    material = {
        "contract_version": PLATFORM_METRICS_VERSION,
        "source_class": source_class,
        "platform": platform,
        "account_id": account_id,
        "post_id": post_id,
        "cycle_revision": revision,
        "captured_at": captured_at,
        "window": window,
        "complete": complete,
        "available_metrics": list(available),
        "metrics": normalized,
        "provenance": {
            "provider": provider,
            "export_id": export_id,
            "export_digest": export_digest,
            "fixture_source_sha256": fixture_source_sha256,
            "live_performance_claim_allowed": live,
        },
    }
    identity = {
        "platform": platform,
        "account_id": account_id,
        "post_id": post_id,
        "cycle_revision": revision,
        "export_id": export_id,
        "window": window,
    }
    material["metrics_event_id"] = "spm1:" + sha256_json(identity)
    material["metrics_event_digest"] = sha256_json(material)
    return json.loads(canonical_json(material))


def parse_platform_metrics_event(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "metrics_event_id",
        "metrics_event_digest",
        "source_class",
        "platform",
        "account_id",
        "post_id",
        "cycle_revision",
        "captured_at",
        "window",
        "complete",
        "available_metrics",
        "metrics",
        "provenance",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise PlatformMetricsError(
            "platform-metrics fields must match v1 exactly"
        )
    provenance = payload["provenance"]
    if (
        not isinstance(provenance, Mapping)
        or set(provenance)
        != {
            "provider",
            "export_id",
            "export_digest",
            "fixture_source_sha256",
            "live_performance_claim_allowed",
        }
    ):
        raise PlatformMetricsError("metrics provenance fields invalid")
    rebuilt = build_platform_metrics_event(
        source_class=payload["source_class"],
        platform=payload["platform"],
        account_id=payload["account_id"],
        post_id=payload["post_id"],
        cycle_revision=payload["cycle_revision"],
        captured_at=payload["captured_at"],
        window_start=payload["window"]["start"],
        window_end=payload["window"]["end"],
        complete=payload["complete"],
        available_metrics=payload["available_metrics"],
        metrics=payload["metrics"],
        export_id=provenance["export_id"],
        export_digest=provenance["export_digest"],
        fixture_source_sha256=provenance["fixture_source_sha256"],
    )
    if payload != rebuilt:
        raise PlatformMetricsError(
            "platform-metrics identity or digest mismatch"
        )
    if provenance["provider"] != rebuilt["provenance"]["provider"]:
        raise PlatformMetricsError("metrics provider mismatch")
    if (
        provenance["live_performance_claim_allowed"]
        is not rebuilt["provenance"]["live_performance_claim_allowed"]
    ):
        raise PlatformMetricsError(
            "metrics live-performance scope mismatch"
        )
    return rebuilt


def _retention_auc(points: list[dict[str, float]] | None) -> float | None:
    if not points:
        return None
    normalized = list(points)
    if normalized[0]["position"] > 0.0:
        normalized = [
            {
                "position": 0.0,
                "retained": normalized[0]["retained"],
            },
            *normalized,
        ]
    if normalized[-1]["position"] < 1.0:
        normalized = [
            *normalized,
            {
                "position": 1.0,
                "retained": normalized[-1]["retained"],
            },
        ]
    area = 0.0
    for left, right in zip(normalized, normalized[1:]):
        area += (
            right["position"] - left["position"]
        ) * (
            left["retained"] + right["retained"]
        ) / 2.0
    return round(min(1.0, max(0.0, area)), 8)


def _wilson(successes: int | None, trials: int | None) -> dict[str, Any] | None:
    if successes is None or trials is None:
        return None
    report = wilson_interval(successes, trials)
    return {
        "method": report.method,
        "estimate": report.estimate,
        "lower": report.lower,
        "upper": report.upper,
        "half_width": report.half_width,
        "sample_size": report.sample_size,
        "confidence": report.confidence,
        "causal": False,
        "interpretation": report.interpretation,
    }


def build_metric_snapshot(
    *,
    publish_result: Mapping[str, Any],
    metrics_events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    published = parse_publish_result(publish_result)
    unique: dict[str, dict[str, Any]] = {}
    for raw in metrics_events:
        event = parse_platform_metrics_event(raw)
        event_id = event["metrics_event_id"]
        previous = unique.get(event_id)
        if previous is not None:
            if previous != event:
                raise PlatformMetricsError(
                    "conflicting duplicate metrics event"
                )
            continue
        unique[event_id] = event

    candidates = []
    for event in unique.values():
        for field in (
            "platform",
            "account_id",
            "post_id",
            "cycle_revision",
            "source_class",
        ):
            publish_value = (
                published["source_class"]
                if field == "source_class"
                else published[field]
            )
            if event[field] != publish_value:
                raise PlatformMetricsError(
                    f"metrics {field} does not match publish result"
                )
        if parse_timestamp(event["window"]["start"]) < parse_timestamp(
            published["published_at"]
        ):
            raise PlatformMetricsError(
                "metrics window begins before publication"
            )
        if event["complete"]:
            candidates.append(event)

    if not candidates:
        raise IncompleteMetricsError(
            "no complete platform metrics export is available"
        )

    selected = sorted(
        candidates,
        key=lambda event: (
            parse_timestamp(event["window"]["end"]),
            parse_timestamp(event["captured_at"]),
            event["metrics_event_id"],
        ),
    )[-1]
    raw = selected["metrics"]
    views = raw["views"]
    impressions = raw["impressions"]
    watch = raw["watch_time_seconds"]
    completed = raw["completed_views"]
    direct_completion = raw["completion_rate"]
    retention_points = raw["retention_points"]
    retention_denominator = raw["retention_denominator_views"]
    link_clicks = raw["link_clicks"]

    direct_average_watch = raw["average_watch_duration_seconds"]
    derived_average_watch = (
        round(watch / views, 8)
        if watch is not None and views not in (None, 0)
        else None
    )
    average_watch = (
        direct_average_watch
        if direct_average_watch is not None
        else derived_average_watch
    )
    derived_completion = (
        round(completed / views, 8)
        if completed is not None and views not in (None, 0)
        else None
    )
    completion_rate = (
        direct_completion
        if direct_completion is not None
        else derived_completion
    )
    link_ctr = (
        round(link_clicks / impressions, 8)
        if link_clicks is not None and impressions not in (None, 0)
        else None
    )
    normalized = {
        "views": views,
        "watch_time_seconds": watch,
        "average_watch_duration_seconds": average_watch,
        "completion_rate": completion_rate,
        "retention_auc": _retention_auc(retention_points),
        "likes": raw["likes"],
        "comments": raw["comments"],
        "shares": raw["shares"],
        "saves": raw["saves"],
        "follows": raw["follows"],
        "impressions": impressions,
        "link_clicks": link_clicks,
        "link_ctr": link_ctr,
    }
    denominators = {
        "average_watch_duration_seconds": (
            {
                "metric": (
                    "provider_defined"
                    if direct_average_watch is not None
                    else "views"
                ),
                "value": (
                    None
                    if direct_average_watch is not None
                    else views
                ),
            }
            if average_watch is not None
            else None
        ),
        "completion_rate": (
            {
                "metric": (
                    "provider_defined_views"
                    if direct_completion is not None
                    else "views"
                ),
                "value": views,
            }
            if completion_rate is not None
            else None
        ),
        "retention_auc": (
            {
                "metric": "retention_denominator_views",
                "value": retention_denominator,
            }
            if retention_points is not None
            else None
        ),
        "link_ctr": (
            {"metric": "impressions", "value": impressions}
            if link_clicks is not None and impressions is not None
            else None
        ),
        "engagement_rates": (
            {"metric": "views", "value": views}
            if views is not None
            else None
        ),
    }
    uncertainty = {
        "completion_rate": _wilson(completed, views),
        "link_ctr": _wilson(link_clicks, impressions),
        "average_watch_duration_seconds": {
            "state": "not_estimable_from_aggregate_export",
            "reason": "individual watch-duration samples are unavailable",
        } if watch is not None else None,
        "retention_auc": {
            "state": "not_estimable_from_aggregate_export",
            "reason": "retention curve is aggregate platform output",
        } if retention_points is not None else None,
        "engagement_counts": {
            "state": "descriptive_only",
            "reason": "platform count semantics are not assumed to be Bernoulli trials",
        },
    }
    snapshot = {
        "contract_version": METRIC_SNAPSHOT_VERSION,
        "publish_result_id": published["publish_result_id"],
        "publish_result_digest": published["publish_result_digest"],
        "platform": published["platform"],
        "account_id": published["account_id"],
        "post_id": published["post_id"],
        "cycle_revision": published["cycle_revision"],
        "source_class": published["source_class"],
        "live_performance_claim_allowed":
            published["provenance"]["live_performance_claim_allowed"],
        "window": dict(selected["window"]),
        "selected_metrics_event_id": selected["metrics_event_id"],
        "selected_metrics_event_digest": selected["metrics_event_digest"],
        "available_metrics": list(selected["available_metrics"]),
        "raw_metrics": json.loads(canonical_json(raw)),
        "normalized_metrics": normalized,
        "normalization_sources": {
            "average_watch_duration_seconds": (
                "provider_export"
                if direct_average_watch is not None
                else (
                    "derived_watch_time_over_views"
                    if derived_average_watch is not None
                    else "unavailable"
                )
            ),
            "completion_rate": (
                "provider_export"
                if direct_completion is not None
                else (
                    "derived_completed_views_over_views"
                    if derived_completion is not None
                    else "unavailable"
                )
            ),
            "retention_auc": (
                "derived_from_platform_retention_curve"
                if retention_points is not None
                else "unavailable"
            ),
            "link_ctr": (
                "derived_link_clicks_over_impressions"
                if link_ctr is not None
                else "unavailable"
            ),
        },
        "denominators": denominators,
        "uncertainty": uncertainty,
        "provenance": {
            "complete_export": True,
            "provider": selected["provenance"]["provider"],
            "export_id": selected["provenance"]["export_id"],
            "export_digest": selected["provenance"]["export_digest"],
            "fixture_source_sha256":
                selected["provenance"]["fixture_source_sha256"],
            "candidate_event_count": len(unique),
            "complete_event_count": len(candidates),
            "observational": True,
            "interpretation": (
                "Platform performance metrics are observational and do not "
                "establish causal effects."
            ),
        },
    }
    snapshot["snapshot_digest"] = sha256_json(snapshot)
    return json.loads(canonical_json(snapshot))


def parse_metric_snapshot(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "publish_result_id",
        "publish_result_digest",
        "platform",
        "account_id",
        "post_id",
        "cycle_revision",
        "source_class",
        "live_performance_claim_allowed",
        "window",
        "selected_metrics_event_id",
        "selected_metrics_event_digest",
        "available_metrics",
        "raw_metrics",
        "normalized_metrics",
        "normalization_sources",
        "denominators",
        "uncertainty",
        "provenance",
        "snapshot_digest",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise PlatformMetricsError(
            "metric snapshot fields must match v1 exactly"
        )
    if payload["contract_version"] != METRIC_SNAPSHOT_VERSION:
        raise PlatformMetricsError(
            "unsupported metric snapshot version"
        )
    provided = _digest(
        payload["snapshot_digest"],
        "snapshot_digest",
    )
    material = dict(payload)
    material.pop("snapshot_digest")
    if sha256_json(material) != provided:
        raise PlatformMetricsError(
            "metric snapshot digest mismatch"
        )
    if payload["source_class"] == "synthetic_fixture":
        if payload["live_performance_claim_allowed"] is not False:
            raise PlatformMetricsError(
                "synthetic snapshot cannot claim live performance"
            )
    elif payload["source_class"] == "platform_export":
        if payload["live_performance_claim_allowed"] is not True:
            raise PlatformMetricsError(
                "platform snapshot must carry live source scope"
            )
    else:
        raise PlatformMetricsError("unsupported snapshot source_class")
    return json.loads(canonical_json(dict(payload)))


class ReelsFeedbackLedger:
    """Append-only publish/metrics event ledger with idempotent replay."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}
        self._sequence = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReelsDeliveryConflictError(
                    f"invalid feedback ledger JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "event_kind",
                    "identity",
                    "payload_digest",
                    "payload",
                }
            ):
                raise ReelsDeliveryConflictError(
                    "feedback ledger row fields invalid"
                )
            if row["ledger_version"] != INGEST_LEDGER_VERSION:
                raise ReelsDeliveryConflictError(
                    "unsupported feedback ledger version"
                )
            if row["sequence"] != self._sequence + 1:
                raise ReelsDeliveryConflictError(
                    "feedback ledger sequence not contiguous"
                )
            kind = row["event_kind"]
            if kind == "publish_result":
                parsed = parse_publish_result(row["payload"])
                identity = parsed["publish_result_id"]
                digest = parsed["publish_result_digest"]
            elif kind == "platform_metrics":
                parsed = parse_platform_metrics_event(row["payload"])
                identity = parsed["metrics_event_id"]
                digest = parsed["metrics_event_digest"]
            else:
                raise ReelsDeliveryConflictError(
                    "unknown feedback ledger event_kind"
                )
            if row["identity"] != identity or row["payload_digest"] != digest:
                raise ReelsDeliveryConflictError(
                    "feedback ledger payload binding mismatch"
                )
            key = (kind, identity)
            if key in self._rows:
                raise ReelsDeliveryConflictError(
                    "duplicate durable feedback event"
                )
            self._rows[key] = dict(row)
            self._sequence += 1

    def _append(
        self,
        kind: str,
        identity: str,
        digest: str,
        payload: Mapping[str, Any],
    ) -> str:
        key = (kind, identity)
        existing = self._rows.get(key)
        if existing is not None:
            if existing["payload_digest"] != digest:
                raise ReelsDeliveryConflictError(
                    "feedback identity changed payload"
                )
            return "duplicate"
        row = {
            "ledger_version": INGEST_LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "event_kind": kind,
            "identity": identity,
            "payload_digest": digest,
            "payload": json.loads(canonical_json(dict(payload))),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._rows[key] = row
        self._sequence += 1
        return "accepted"

    def ingest_publish_result(self, payload: Mapping[str, Any]) -> str:
        parsed = parse_publish_result(payload)
        return self._append(
            "publish_result",
            parsed["publish_result_id"],
            parsed["publish_result_digest"],
            parsed,
        )

    def ingest_platform_metrics(self, payload: Mapping[str, Any]) -> str:
        parsed = parse_platform_metrics_event(payload)
        return self._append(
            "platform_metrics",
            parsed["metrics_event_id"],
            parsed["metrics_event_digest"],
            parsed,
        )

    def publish_result(self, publish_result_id: str) -> dict[str, Any] | None:
        row = self._rows.get(("publish_result", publish_result_id))
        return None if row is None else json.loads(
            canonical_json(row["payload"])
        )

    def metrics_for_post(
        self,
        *,
        platform: str,
        account_id: str,
        post_id: str,
    ) -> tuple[dict[str, Any], ...]:
        rows = [
            row["payload"]
            for (kind, _), row in self._rows.items()
            if (
                kind == "platform_metrics"
                and row["payload"]["platform"] == platform
                and row["payload"]["account_id"] == account_id
                and row["payload"]["post_id"] == post_id
            )
        ]
        return tuple(
            json.loads(canonical_json(row))
            for row in sorted(
                rows,
                key=lambda row: (
                    parse_timestamp(row["captured_at"]),
                    row["metrics_event_id"],
                ),
            )
        )

    @property
    def row_count(self) -> int:
        return self._sequence


def _decision_reference(
    decision_handoff: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if decision_handoff is None:
        return {
            "state": "none",
            "handoff_version": None,
            "handoff_digest": None,
            "audit_bundle_digest": None,
            "classification": None,
            "experiment_id": None,
            "registry_revision": None,
        }
    try:
        parsed = parse_decision_handoff(decision_handoff)
    except DecisionHandoffError as exc:
        raise AutonomousReelsError(
            "invalid decision handoff reference"
        ) from exc
    return {
        "state": "bound",
        "handoff_version": DECISION_HANDOFF_VERSION,
        "handoff_digest": parsed["handoff_digest"],
        "audit_bundle_digest": parsed["audit_bundle_digest"],
        "classification": parsed["classification"],
        "experiment_id": parsed["registry"]["experiment_id"],
        "registry_revision": parsed["registry"]["revision"],
    }


def _ratio(
    numerator: int | None,
    denominator: int | None,
) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator, 8)


def _recommendations(
    snapshot: Mapping[str, Any],
    *,
    media_duration_seconds: float,
    min_views: int,
) -> tuple[str, list[dict[str, Any]]]:
    metrics = snapshot["normalized_metrics"]
    raw = snapshot["raw_metrics"]
    views = metrics["views"]
    ref = f"metric_snapshot:{snapshot['snapshot_digest']}"
    if views is None or views < min_views:
        return "insufficient_data", [{
            "action": "collect_more_platform_evidence",
            "state": "insufficient_data",
            "certainty": "insufficient",
            "evidence_refs": [ref + "#normalized_metrics.views"],
            "rationale": (
                f"views={views!r} is below deterministic minimum {min_views}"
            ),
        }]

    recs: list[dict[str, Any]] = []
    average_watch = metrics["average_watch_duration_seconds"]
    if average_watch is not None:
        watch_ratio = average_watch / media_duration_seconds
        if watch_ratio < 0.35:
            recs.append({
                "action": "strengthen_first_seconds_and_pacing",
                "state": "observational_signal",
                "certainty": "directional_not_causal",
                "evidence_refs": [
                    ref + "#normalized_metrics.average_watch_duration_seconds",
                    (
                        "publish_result:"
                        + snapshot["publish_result_digest"]
                        + "#artifact.media_duration_seconds"
                    ),
                ],
                "rationale": (
                    f"average_watch_ratio={watch_ratio:.8f} below 0.35 heuristic"
                ),
            })

    completion = metrics["completion_rate"]
    if completion is not None and completion < 0.25:
        recs.append({
            "action": "tighten_ending_and_loop",
            "state": "observational_signal",
            "certainty": "directional_not_causal",
            "evidence_refs": [
                ref + "#normalized_metrics.completion_rate",
                ref + "#uncertainty.completion_rate",
            ],
            "rationale": (
                f"completion_rate={completion:.8f} below 0.25 heuristic"
            ),
        })

    shares_rate = _ratio(raw["shares"], views)
    if shares_rate is not None and shares_rate >= 0.02:
        recs.append({
            "action": "preserve_shareable_hook",
            "state": "observational_signal",
            "certainty": "directional_not_causal",
            "evidence_refs": [ref + "#raw_metrics.shares"],
            "rationale": (
                f"shares_per_view={shares_rate:.8f} at or above 0.02 heuristic"
            ),
        })

    saves_rate = _ratio(raw["saves"], views)
    if saves_rate is not None and saves_rate >= 0.01:
        recs.append({
            "action": "preserve_saveable_value",
            "state": "observational_signal",
            "certainty": "directional_not_causal",
            "evidence_refs": [ref + "#raw_metrics.saves"],
            "rationale": (
                f"saves_per_view={saves_rate:.8f} at or above 0.01 heuristic"
            ),
        })

    follows_rate = _ratio(raw["follows"], views)
    if follows_rate is not None and follows_rate < 0.005:
        recs.append({
            "action": "test_follow_call_to_action",
            "state": "observational_signal",
            "certainty": "directional_not_causal",
            "evidence_refs": [ref + "#raw_metrics.follows"],
            "rationale": (
                f"follows_per_view={follows_rate:.8f} below 0.005 heuristic"
            ),
        })

    link_ctr = metrics["link_ctr"]
    if link_ctr is not None and link_ctr < 0.01:
        recs.append({
            "action": "test_link_call_to_action",
            "state": "observational_signal",
            "certainty": "directional_not_causal",
            "evidence_refs": [
                ref + "#normalized_metrics.link_ctr",
                ref + "#uncertainty.link_ctr",
            ],
            "rationale": (
                f"link_ctr={link_ctr:.8f} below 0.01 heuristic"
            ),
        })

    if not recs:
        recs.append({
            "action": "preserve_current_structure_for_next_test",
            "state": "observational_signal",
            "certainty": "directional_not_causal",
            "evidence_refs": [ref],
            "rationale": (
                "no configured directional heuristic crossed its threshold"
            ),
        })
    return "directional_observational", recs


def build_next_cycle_seed(
    *,
    publish_result: Mapping[str, Any],
    metric_snapshot: Mapping[str, Any],
    next_cycle_id: str,
    expected_cycle_revision: int,
    decision_handoff: Mapping[str, Any] | None = None,
    min_views: int = 100,
) -> dict[str, Any]:
    published = parse_publish_result(publish_result)
    snapshot = parse_metric_snapshot(metric_snapshot)
    _string(next_cycle_id, "next_cycle_id")
    _integer(min_views, "min_views", minimum=1)
    if published["cycle_revision"] != expected_cycle_revision:
        raise StaleCycleRevisionError(
            "publish result cycle revision is stale"
        )
    if snapshot["cycle_revision"] != expected_cycle_revision:
        raise StaleCycleRevisionError(
            "metric snapshot cycle revision is stale"
        )
    if (
        snapshot["publish_result_id"] != published["publish_result_id"]
        or snapshot["publish_result_digest"]
        != published["publish_result_digest"]
    ):
        raise AutonomousReelsError(
            "metric snapshot is not bound to publish result"
        )
    decision = _decision_reference(decision_handoff)
    if (
        decision["state"] == "bound"
        and decision["registry_revision"] != expected_cycle_revision
    ):
        raise StaleCycleRevisionError(
            "decision handoff revision does not match active cycle"
        )

    evidence_state, recommendations = _recommendations(
        snapshot,
        media_duration_seconds=
            published["artifact"]["media_duration_seconds"],
        min_views=min_views,
    )
    live = (
        published["source_class"] == "platform_export"
        and snapshot["source_class"] == "platform_export"
        and published["provenance"][
            "live_performance_claim_allowed"
        ] is True
        and snapshot["live_performance_claim_allowed"] is True
    )
    if published["source_class"] != snapshot["source_class"]:
        raise SyntheticEvidenceRejected(
            "publish and metric source classes differ"
        )
    seed = {
        "contract_version": NEXT_CYCLE_SEED_VERSION,
        "next_cycle_id": next_cycle_id,
        "cycle_revision": expected_cycle_revision,
        "source_class": published["source_class"],
        "live_performance_claim_allowed": live,
        "creator_cycle_eligible": live,
        "evidence_state": evidence_state,
        "lineage": {
            "creative_artifact_id":
                published["artifact"]["creative_artifact_id"],
            "creative_artifact_digest":
                published["artifact"]["creative_artifact_digest"],
            "media_artifact_id":
                published["artifact"]["media_artifact_id"],
            "media_artifact_digest":
                published["artifact"]["media_artifact_digest"],
            "media_render_fingerprint":
                published["artifact"]["media_render_fingerprint"],
            "media_duration_seconds":
                published["artifact"]["media_duration_seconds"],
            "publish_result_id": published["publish_result_id"],
            "publish_result_digest": published["publish_result_digest"],
            "platform": published["platform"],
            "account_id": published["account_id"],
            "post_id": published["post_id"],
            "published_at": published["published_at"],
            "metric_snapshot_digest": snapshot["snapshot_digest"],
            "metric_window": dict(snapshot["window"]),
            "decision": decision,
        },
        "evidence": {
            "publish_result": json.loads(canonical_json(published)),
            "metric_snapshot": json.loads(canonical_json(snapshot)),
            "decision_handoff": (
                json.loads(canonical_json(
                    parse_decision_handoff(decision_handoff)
                ))
                if decision_handoff is not None
                else None
            ),
        },
        "metrics": {
            "normalized": json.loads(canonical_json(
                snapshot["normalized_metrics"]
            )),
            "normalization_sources": json.loads(canonical_json(
                snapshot["normalization_sources"]
            )),
            "denominators": json.loads(canonical_json(
                snapshot["denominators"]
            )),
            "uncertainty": json.loads(canonical_json(
                snapshot["uncertainty"]
            )),
            "available_metrics": list(snapshot["available_metrics"]),
        },
        "recommendations": recommendations,
        "authority": {
            "auto_publish": False,
            "external_mutation": False,
            "release_authorized": False,
            "publish_authorized": False,
            "requires_creator_release_authorization": True,
        },
        "interpretation": (
            "Next-cycle recommendations are observational heuristics derived "
            "from source-bound platform metrics. They are not causal claims "
            "and do not authorize publishing."
        ),
    }
    identity = {
        "next_cycle_id": next_cycle_id,
        "cycle_revision": expected_cycle_revision,
        "publish_result_digest": published["publish_result_digest"],
        "metric_snapshot_digest": snapshot["snapshot_digest"],
        "decision_handoff_digest": decision["handoff_digest"],
    }
    seed["idempotency_key"] = "grs1:" + sha256_json(identity)
    seed["seed_digest"] = sha256_json(seed)
    return json.loads(canonical_json(seed))


def validate_next_cycle_seed(
    payload: Mapping[str, Any],
    *,
    expected_cycle_revision: int,
    allow_synthetic_fixture: bool = False,
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "next_cycle_id",
        "cycle_revision",
        "source_class",
        "live_performance_claim_allowed",
        "creator_cycle_eligible",
        "evidence_state",
        "lineage",
        "evidence",
        "metrics",
        "recommendations",
        "authority",
        "interpretation",
        "idempotency_key",
        "seed_digest",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise AutonomousReelsError(
            "next-cycle seed fields must match v1 exactly"
        )
    if payload["contract_version"] != NEXT_CYCLE_SEED_VERSION:
        raise AutonomousReelsError(
            "unsupported next-cycle seed version"
        )
    revision = _integer(
        payload["cycle_revision"],
        "cycle_revision",
        minimum=1,
    )
    if revision != expected_cycle_revision:
        raise StaleCycleRevisionError(
            "next-cycle seed revision is stale"
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
        raise AutonomousReelsError(
            "next-cycle seed authority boundary invalid"
        )
    source_class = payload["source_class"]
    if source_class == "synthetic_fixture":
        if (
            payload["live_performance_claim_allowed"] is not False
            or payload["creator_cycle_eligible"] is not False
        ):
            raise SyntheticEvidenceRejected(
                "synthetic seed cannot claim live or Creator eligibility"
            )
        if not allow_synthetic_fixture:
            raise SyntheticEvidenceRejected(
                "synthetic next-cycle seed is conformance-only"
            )
    elif source_class == "platform_export":
        if (
            payload["live_performance_claim_allowed"] is not True
            or payload["creator_cycle_eligible"] is not True
        ):
            raise AutonomousReelsError(
                "live platform seed must preserve live source scope"
            )
    else:
        raise AutonomousReelsError("unsupported seed source_class")

    lineage = payload["lineage"]
    if not isinstance(lineage, Mapping):
        raise AutonomousReelsError("seed lineage missing")
    evidence = payload["evidence"]
    if (
        not isinstance(evidence, Mapping)
        or set(evidence)
        != {"publish_result", "metric_snapshot", "decision_handoff"}
    ):
        raise AutonomousReelsError(
            "next-cycle seed evidence fields invalid"
        )
    published = parse_publish_result(evidence["publish_result"])
    snapshot = parse_metric_snapshot(evidence["metric_snapshot"])
    if (
        snapshot["publish_result_id"] != published["publish_result_id"]
        or snapshot["publish_result_digest"]
        != published["publish_result_digest"]
    ):
        raise AutonomousReelsError(
            "embedded metric snapshot is not bound to embedded publish result"
        )
    if (
        published["source_class"] != payload["source_class"]
        or snapshot["source_class"] != payload["source_class"]
    ):
        raise SyntheticEvidenceRejected(
            "seed source_class conflicts with embedded evidence"
        )
    if (
        published["cycle_revision"] != revision
        or snapshot["cycle_revision"] != revision
    ):
        raise StaleCycleRevisionError(
            "embedded evidence revision is stale"
        )
    lineage_fields = {
        "creative_artifact_id",
        "creative_artifact_digest",
        "media_artifact_id",
        "media_artifact_digest",
        "media_render_fingerprint",
        "media_duration_seconds",
        "publish_result_id",
        "publish_result_digest",
        "platform",
        "account_id",
        "post_id",
        "published_at",
        "metric_snapshot_digest",
        "metric_window",
        "decision",
    }
    if set(lineage) != lineage_fields:
        raise AutonomousReelsError(
            "next-cycle lineage fields must match v1 exactly"
        )
    if (
        lineage.get("publish_result_id") != published["publish_result_id"]
        or lineage.get("publish_result_digest")
        != published["publish_result_digest"]
        or lineage.get("metric_snapshot_digest")
        != snapshot["snapshot_digest"]
        or lineage.get("creative_artifact_digest")
        != published["artifact"]["creative_artifact_digest"]
        or lineage.get("media_artifact_digest")
        != published["artifact"]["media_artifact_digest"]
        or lineage.get("platform") != published["platform"]
        or lineage.get("account_id") != published["account_id"]
        or lineage.get("post_id") != published["post_id"]
        or lineage.get("published_at") != published["published_at"]
        or lineage.get("metric_window") != snapshot["window"]
    ):
        raise AutonomousReelsError(
            "seed lineage does not match embedded evidence"
        )
    decision_ref = lineage.get("decision")
    if not isinstance(decision_ref, Mapping):
        raise AutonomousReelsError(
            "next-cycle decision reference missing"
        )
    decision_payload = evidence["decision_handoff"]
    if decision_ref.get("state") == "bound":
        if decision_payload is None:
            raise AutonomousReelsError(
                "bound decision reference lacks embedded handoff"
            )
        parsed_decision = parse_decision_handoff(decision_payload)
        if (
            decision_ref.get("handoff_digest")
            != parsed_decision["handoff_digest"]
            or decision_ref.get("audit_bundle_digest")
            != parsed_decision["audit_bundle_digest"]
            or decision_ref.get("classification")
            != parsed_decision["classification"]
            or decision_ref.get("experiment_id")
            != parsed_decision["registry"]["experiment_id"]
            or decision_ref.get("registry_revision")
            != parsed_decision["registry"]["revision"]
        ):
            raise AutonomousReelsError(
                "decision reference does not match embedded handoff"
            )
    elif decision_ref.get("state") == "none":
        if decision_payload is not None:
            raise AutonomousReelsError(
                "unbound decision reference contains a handoff"
            )
    else:
        raise AutonomousReelsError(
            "unsupported decision reference state"
        )

    metrics = payload["metrics"]
    if (
        not isinstance(metrics, Mapping)
        or set(metrics)
        != {
            "normalized",
            "normalization_sources",
            "denominators",
            "uncertainty",
            "available_metrics",
        }
    ):
        raise AutonomousReelsError(
            "next-cycle metrics fields must match v1 exactly"
        )
    if (
        metrics["normalized"] != snapshot["normalized_metrics"]
        or metrics["normalization_sources"]
        != snapshot["normalization_sources"]
        or metrics["denominators"] != snapshot["denominators"]
        or metrics["uncertainty"] != snapshot["uncertainty"]
        or metrics["available_metrics"] != snapshot["available_metrics"]
    ):
        raise AutonomousReelsError(
            "next-cycle metrics do not match embedded snapshot"
        )

    if payload["evidence_state"] not in {
        "insufficient_data",
        "directional_observational",
    }:
        raise AutonomousReelsError(
            "unsupported next-cycle evidence_state"
        )
    recommendations = payload["recommendations"]
    if not isinstance(recommendations, list) or not recommendations:
        raise AutonomousReelsError(
            "next-cycle recommendations must be non-empty"
        )
    for item in recommendations:
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {
                "action", "state", "certainty",
                "evidence_refs", "rationale",
            }
        ):
            raise AutonomousReelsError(
                "recommendation fields must match v1 exactly"
            )
        _string(item["action"], "recommendation.action")
        _string(item["rationale"], "recommendation.rationale")
        if item["state"] not in {
            "insufficient_data",
            "observational_signal",
        }:
            raise AutonomousReelsError(
                "unsupported recommendation state"
            )
        if item["certainty"] not in {
            "insufficient",
            "directional_not_causal",
        }:
            raise AutonomousReelsError(
                "unsupported recommendation certainty"
            )
        if (
            not isinstance(item["evidence_refs"], list)
            or not item["evidence_refs"]
            or any(
                not isinstance(ref, str) or not ref
                for ref in item["evidence_refs"]
            )
        ):
            raise AutonomousReelsError(
                "recommendation evidence_refs must be non-empty strings"
            )
    expected_id = "grs1:" + sha256_json({
        "next_cycle_id": payload["next_cycle_id"],
        "cycle_revision": revision,
        "publish_result_digest": lineage["publish_result_digest"],
        "metric_snapshot_digest": lineage["metric_snapshot_digest"],
        "decision_handoff_digest":
            lineage["decision"]["handoff_digest"],
    })
    if payload["idempotency_key"] != expected_id:
        raise AutonomousReelsError(
            "next-cycle idempotency identity mismatch"
        )
    provided = _digest(
        payload["seed_digest"],
        "seed_digest",
    )
    material = dict(payload)
    material.pop("seed_digest")
    if sha256_json(material) != provided:
        raise AutonomousReelsError(
            "next-cycle seed digest mismatch"
        )
    return json.loads(canonical_json(dict(payload)))


class NextCycleOutbox:
    """Durable prepare/ack outbox for exactly-once logical next-cycle seeds."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._prepared: dict[str, dict[str, Any]] = {}
        self._acked: set[str] = set()
        self._sequence = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReelsDeliveryConflictError(
                    f"invalid outbox JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "event_type",
                    "idempotency_key",
                    "seed_digest",
                    "seed",
                }
            ):
                raise ReelsDeliveryConflictError(
                    "next-cycle outbox fields invalid"
                )
            if row["ledger_version"] != OUTBOX_LEDGER_VERSION:
                raise ReelsDeliveryConflictError(
                    "unsupported next-cycle outbox version"
                )
            if row["sequence"] != self._sequence + 1:
                raise ReelsDeliveryConflictError(
                    "next-cycle outbox sequence not contiguous"
                )
            key = row["idempotency_key"]
            if row["event_type"] == "prepare":
                if row["seed"] is None:
                    raise ReelsDeliveryConflictError(
                        "prepare row missing seed"
                    )
                seed = row["seed"]
                if (
                    seed.get("idempotency_key") != key
                    or seed.get("seed_digest") != row["seed_digest"]
                ):
                    raise ReelsDeliveryConflictError(
                        "prepare seed binding mismatch"
                    )
                if key in self._prepared:
                    raise ReelsDeliveryConflictError(
                        "duplicate durable next-cycle prepare"
                    )
                self._prepared[key] = seed
            elif row["event_type"] == "ack":
                prepared = self._prepared.get(key)
                if prepared is None:
                    raise ReelsDeliveryConflictError(
                        "ack without prepare"
                    )
                if row["seed"] is not None:
                    raise ReelsDeliveryConflictError(
                        "ack row cannot contain seed"
                    )
                if prepared["seed_digest"] != row["seed_digest"]:
                    raise ReelsDeliveryConflictError(
                        "ack seed digest mismatch"
                    )
                if key in self._acked:
                    raise ReelsDeliveryConflictError(
                        "duplicate durable ack"
                    )
                self._acked.add(key)
            else:
                raise ReelsDeliveryConflictError(
                    "unknown outbox event_type"
                )
            self._sequence += 1

    def _append(self, row: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence += 1

    def prepare(
        self,
        seed: Mapping[str, Any],
        *,
        expected_cycle_revision: int,
        allow_synthetic_fixture: bool = False,
    ) -> str:
        parsed = validate_next_cycle_seed(
            seed,
            expected_cycle_revision=expected_cycle_revision,
            allow_synthetic_fixture=allow_synthetic_fixture,
        )
        key = parsed["idempotency_key"]
        existing = self._prepared.get(key)
        if existing is not None:
            if existing["seed_digest"] != parsed["seed_digest"]:
                raise ReelsDeliveryConflictError(
                    "next-cycle identity changed payload"
                )
            return "duplicate"
        row = {
            "ledger_version": OUTBOX_LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "event_type": "prepare",
            "idempotency_key": key,
            "seed_digest": parsed["seed_digest"],
            "seed": parsed,
        }
        self._append(row)
        self._prepared[key] = parsed
        return "prepared"

    def acknowledge(
        self,
        idempotency_key: str,
        seed_digest: str,
    ) -> str:
        prepared = self._prepared.get(idempotency_key)
        if prepared is None:
            raise ReelsDeliveryConflictError(
                "cannot acknowledge unknown next-cycle seed"
            )
        if prepared["seed_digest"] != seed_digest:
            raise ReelsDeliveryConflictError(
                "next-cycle ack digest mismatch"
            )
        if idempotency_key in self._acked:
            return "duplicate"
        row = {
            "ledger_version": OUTBOX_LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "event_type": "ack",
            "idempotency_key": idempotency_key,
            "seed_digest": seed_digest,
            "seed": None,
        }
        self._append(row)
        self._acked.add(idempotency_key)
        return "acknowledged"

    def pending(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            self._prepared[key]
            for key in sorted(self._prepared)
            if key not in self._acked
        )

    @property
    def logical_seed_count(self) -> int:
        return len(self._prepared)


class ReferenceCreatorNextCycleConsumer:
    """Fail-closed reference consumer; does not publish or mutate providers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReelsDeliveryConflictError(
                    f"invalid consumer JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "idempotency_key",
                    "seed_digest",
                    "status",
                }
            ):
                raise ReelsDeliveryConflictError(
                    "consumer ledger fields invalid"
                )
            if row["ledger_version"] != CONSUMER_LEDGER_VERSION:
                raise ReelsDeliveryConflictError(
                    "unsupported Creator consumer ledger version"
                )
            if row["sequence"] != len(self._rows) + 1:
                raise ReelsDeliveryConflictError(
                    "Creator consumer sequence not contiguous"
                )
            if row["status"] != "accepted":
                raise ReelsDeliveryConflictError(
                    "Creator consumer status invalid"
                )
            key = row["idempotency_key"]
            if key in self._rows:
                raise ReelsDeliveryConflictError(
                    "duplicate durable Creator seed"
                )
            self._rows[key] = dict(row)

    def consume(
        self,
        seed: Mapping[str, Any],
        *,
        expected_cycle_revision: int,
        allow_synthetic_fixture: bool = False,
    ) -> str:
        parsed = validate_next_cycle_seed(
            seed,
            expected_cycle_revision=expected_cycle_revision,
            allow_synthetic_fixture=allow_synthetic_fixture,
        )
        key = parsed["idempotency_key"]
        existing = self._rows.get(key)
        if existing is not None:
            if existing["seed_digest"] != parsed["seed_digest"]:
                raise ReelsDeliveryConflictError(
                    "Creator next-cycle identity changed"
                )
            return "duplicate"
        row = {
            "ledger_version": CONSUMER_LEDGER_VERSION,
            "sequence": len(self._rows) + 1,
            "idempotency_key": key,
            "seed_digest": parsed["seed_digest"],
            "status": "accepted",
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._rows[key] = row
        return "accepted"

    @property
    def accepted_count(self) -> int:
        return len(self._rows)
