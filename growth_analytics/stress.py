from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .core import AnalyticsEvent
from .delivery import (
    FeedbackDeliveryLedger,
    InjectedDeliveryFault,
    creator_seed_handoff_json,
)
from .engine import CAUSALITY_NOTICE, TimeWindow
from .event_stream import (
    DurableAnalyticsEventStream,
    InjectedEventStreamFault,
    analytics_event_from_dict,
)
from .reliability import (
    WindowFinalizationLedger,
    build_finalized_feedback_batch,
    finalize_window_snapshot,
)


STRESS_FIXTURE_VERSION = "growth.reliability_stress_fixture.v1"
RELIABILITY_REPORT_VERSION = "growth.reliability_report.v1"
FINALIZED_LEARNING_REPORT_VERSION = "growth.finalized_learning_report.v1"
DEFAULT_STRESS_SEED = 20260927


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def generate_stress_fixture(seed: int = DEFAULT_STRESS_SEED) -> dict[str, Any]:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    rng = random.Random(seed)
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    allowed_lateness_seconds = 2 * 24 * 60 * 60
    campaigns: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for campaign_index in range(3):
        campaign_id = f"stress-campaign-{campaign_index + 1}"
        channel_id = f"stress-channel-{campaign_index + 1}"
        video_id = f"stress-video-{campaign_index + 1}"
        variants = []
        for variant_index in range(4):
            variant_id = f"c{campaign_index + 1}-v{variant_index + 1}"
            variants.append(
                {
                    "variant_id": variant_id,
                    "video_id": video_id,
                    "duration_seconds": float(45 + campaign_index * 5),
                }
            )
        windows = []
        for window_index in range(4):
            start = base + timedelta(days=7 * window_index)
            end = start + timedelta(days=7)
            windows.append(
                {
                    "label": f"w{window_index + 1}",
                    "start": _iso(start),
                    "end": _iso(end),
                    "watermark": _iso(
                        end + timedelta(seconds=allowed_lateness_seconds)
                    ),
                }
            )
            for variant_index, variant in enumerate(variants):
                for event_index in range(24):
                    captured = start + timedelta(
                        seconds=rng.randrange(0, 7 * 24 * 60 * 60)
                    )
                    impressions = 250 + rng.randrange(0, 751)
                    ctr_rate = (
                        0.025
                        + 0.008 * variant_index
                        + 0.002 * window_index
                        + rng.random() * 0.006
                    )
                    clicks = min(
                        impressions,
                        max(0, int(round(impressions * ctr_rate))),
                    )
                    view_rate = (
                        0.42
                        + 0.045 * variant_index
                        + 0.015 * window_index
                        + rng.random() * 0.05
                    )
                    views = min(
                        impressions,
                        max(clicks, int(round(impressions * view_rate))),
                    )
                    duration = float(variant["duration_seconds"])
                    watch_ratio = min(
                        0.82,
                        0.30
                        + 0.055 * variant_index
                        + 0.018 * window_index
                        + rng.random() * 0.05,
                    )
                    watch_time = round(views * duration * watch_ratio, 6)
                    end_retention = min(
                        0.80,
                        0.24
                        + 0.065 * variant_index
                        + 0.02 * window_index
                        + rng.random() * 0.035,
                    )
                    mid_retention = min(0.92, end_retention + 0.24)
                    quarter_retention = min(0.97, mid_retention + 0.16)
                    event_id = (
                        f"{campaign_id}-w{window_index + 1}-"
                        f"v{variant_index + 1}-e{event_index + 1:02d}"
                    )
                    events.append(
                        {
                            "provider": "fixture",
                            "event_id": event_id,
                            "channel_id": channel_id,
                            "video_id": video_id,
                            "variant_id": variant["variant_id"],
                            "captured_at": _iso(captured),
                            "impressions": impressions,
                            "views": views,
                            "clicks": clicks,
                            "watch_time_seconds": watch_time,
                            "retention": [
                                {"position": 0.0, "retained": 1.0},
                                {
                                    "position": 0.25,
                                    "retained": round(quarter_retention, 8),
                                },
                                {
                                    "position": 0.5,
                                    "retained": round(mid_retention, 8),
                                },
                                {
                                    "position": 1.0,
                                    "retained": round(end_retention, 8),
                                },
                            ],
                        }
                    )
        campaigns.append(
            {
                "campaign_id": campaign_id,
                "channel_id": channel_id,
                "variants": variants,
                "windows": windows,
            }
        )

    event_ids = [item["event_id"] for item in events]
    early_ids = [
        item["event_id"]
        for item in events
        if "-w1-" in item["event_id"]
    ]
    rng.shuffle(early_ids)
    designated_late_ids = sorted(early_ids[:96])
    designated_late_set = set(designated_late_ids)

    main_order = [event_id for event_id in event_ids if event_id not in designated_late_set]
    rng.shuffle(main_order)
    delayed = list(designated_late_ids)
    rng.shuffle(delayed)
    ingestion_order = main_order + delayed

    duplicate_ids = list(event_ids)
    rng.shuffle(duplicate_ids)
    duplicate_ids = sorted(duplicate_ids[:128])
    for event_id in duplicate_ids:
        position = rng.randrange(0, len(ingestion_order) + 1)
        ingestion_order.insert(position, event_id)

    fault_candidates = [
        event_id
        for event_id in ingestion_order
        if event_id not in set(duplicate_ids)
    ]
    append_faults = {
        "before_commit_event_id": fault_candidates[111],
        "after_commit_event_id": fault_candidates[777],
    }

    return {
        "fixture_version": STRESS_FIXTURE_VERSION,
        "seed": seed,
        "allowed_lateness_seconds": allowed_lateness_seconds,
        "restart_every_attempts": 97,
        "campaigns": campaigns,
        "events": events,
        "ingestion_order": ingestion_order,
        "duplicate_event_ids": duplicate_ids,
        "designated_late_event_ids": designated_late_ids,
        "append_faults": append_faults,
        "delivery_fault_identities": {
            "before_commit": "stress-campaign-1|w1",
            "after_commit": "stress-campaign-1|w2",
        },
    }


def _validate_fixture(payload: Mapping[str, Any]) -> None:
    if payload.get("fixture_version") != STRESS_FIXTURE_VERSION:
        raise ValueError("unsupported reliability stress fixture version")
    if payload.get("seed") != DEFAULT_STRESS_SEED:
        raise ValueError("unexpected reliability stress seed")
    events = payload.get("events")
    if not isinstance(events, list) or len(events) < 1000:
        raise ValueError("stress fixture requires at least 1000 unique events")
    campaigns = payload.get("campaigns")
    if not isinstance(campaigns, list) or len(campaigns) < 2:
        raise ValueError("stress fixture requires multiple campaigns")


def finalized_learning_report(
    payload: Mapping[str, Any],
    events: Sequence[AnalyticsEvent],
) -> dict[str, Any]:
    _validate_fixture(payload)
    allowed_lateness = int(payload["allowed_lateness_seconds"])
    rows: list[dict[str, Any]] = []
    for campaign in sorted(
        payload["campaigns"], key=lambda item: item["campaign_id"]
    ):
        campaign_events = tuple(
            event
            for event in events
            if event.channel_id == campaign["channel_id"]
        )
        for raw_window in campaign["windows"]:
            window = TimeWindow(
                raw_window["label"],
                raw_window["start"],
                raw_window["end"],
            )
            snapshot = finalize_window_snapshot(
                campaign_id=campaign["campaign_id"],
                window=window,
                events=campaign_events,
                watermark=raw_window["watermark"],
                allowed_lateness_seconds=allowed_lateness,
            )
            variant_specs = [
                {
                    **variant,
                    "next_content_job_id": (
                        f"next-{campaign['campaign_id']}-"
                        f"{window.label}-{variant['variant_id']}"
                    ),
                }
                for variant in campaign["variants"]
            ]
            batch = build_finalized_feedback_batch(
                snapshot=snapshot,
                channel_id=campaign["channel_id"],
                variants=variant_specs,
            )
            seed_wire = creator_seed_handoff_json(batch)
            rows.append(
                {
                    "campaign_id": campaign["campaign_id"],
                    "window": {
                        "label": window.label,
                        "start": window.start,
                        "end": window.end,
                        "watermark": raw_window["watermark"],
                    },
                    "event_count": len(snapshot.events),
                    "event_set_digest": snapshot.event_set_digest,
                    "batch_id": batch.batch_id,
                    "payload_digest": batch.payload_digest,
                    "seed_digest": _sha256_text(seed_wire),
                    "feedback_count": len(batch.feedback),
                    "causal": False,
                    "interpretation": CAUSALITY_NOTICE,
                }
            )
    return {
        "report_version": FINALIZED_LEARNING_REPORT_VERSION,
        "seed": payload["seed"],
        "unique_event_count": len(events),
        "campaign_count": len(payload["campaigns"]),
        "finalized_window_count": len(rows),
        "causal": False,
        "interpretation": CAUSALITY_NOTICE,
        "windows": rows,
    }


def run_reliability_stress(
    payload: Mapping[str, Any],
    workdir: str | Path,
) -> dict[str, Any]:
    _validate_fixture(payload)
    directory = Path(workdir)
    directory.mkdir(parents=True, exist_ok=True)
    event_path = directory / "events.jsonl"
    finalization_path = directory / "finalizations.jsonl"
    delivery_path = directory / "delivery.jsonl"

    event_by_id = {
        raw["event_id"]: analytics_event_from_dict(raw)
        for raw in payload["events"]
    }
    if len(event_by_id) != len(payload["events"]):
        raise ValueError("stress fixture event ids must be unique")

    stream = DurableAnalyticsEventStream(event_path)
    duplicate_receipts = 0
    late_accepts = 0
    restart_count = 0
    injected = {"before_commit": False, "after_commit": False}
    before_id = payload["append_faults"]["before_commit_event_id"]
    after_id = payload["append_faults"]["after_commit_event_id"]

    for attempt_index, event_id in enumerate(payload["ingestion_order"], 1):
        event = event_by_id[event_id]
        if event_id == before_id and not injected["before_commit"]:
            injected["before_commit"] = True
            try:
                stream.append(event, fault="before_commit")
            except InjectedEventStreamFault:
                pass
            stream = DurableAnalyticsEventStream(event_path)
            restart_count += 1
        if event_id == after_id and not injected["after_commit"]:
            injected["after_commit"] = True
            try:
                stream.append(event, fault="after_commit")
            except InjectedEventStreamFault:
                pass
            stream = DurableAnalyticsEventStream(event_path)
            restart_count += 1

        receipt = stream.append(event)
        if receipt.status == "duplicate":
            duplicate_receipts += 1
        elif receipt.late:
            late_accepts += 1

        if attempt_index % int(payload["restart_every_attempts"]) == 0:
            replay_before = tuple(
                item.idempotency_key
                for item in stream.replay(order="captured_at")
            )
            stream = DurableAnalyticsEventStream(event_path)
            replay_after = tuple(
                item.idempotency_key
                for item in stream.replay(order="captured_at")
            )
            if replay_before != replay_after:
                raise RuntimeError("event replay changed across restart")
            restart_count += 1

    stream = DurableAnalyticsEventStream(event_path)
    replayed = stream.replay(order="captured_at")
    if len(replayed) != len(event_by_id):
        raise RuntimeError("durable replay did not converge to unique event set")

    learning = finalized_learning_report(payload, replayed)
    finalization_ledger = WindowFinalizationLedger(finalization_path)
    delivery_ledger = FeedbackDeliveryLedger(delivery_path)
    delivery_faults = payload["delivery_fault_identities"]
    delivery_count = 0

    for campaign in sorted(
        payload["campaigns"], key=lambda item: item["campaign_id"]
    ):
        campaign_events = tuple(
            event
            for event in replayed
            if event.channel_id == campaign["channel_id"]
        )
        for raw_window in campaign["windows"]:
            window = TimeWindow(
                raw_window["label"],
                raw_window["start"],
                raw_window["end"],
            )
            snapshot = finalize_window_snapshot(
                campaign_id=campaign["campaign_id"],
                window=window,
                events=campaign_events,
                watermark=raw_window["watermark"],
                allowed_lateness_seconds=int(payload["allowed_lateness_seconds"]),
            )
            variant_specs = [
                {
                    **variant,
                    "next_content_job_id": (
                        f"next-{campaign['campaign_id']}-"
                        f"{window.label}-{variant['variant_id']}"
                    ),
                }
                for variant in campaign["variants"]
            ]
            batch = build_finalized_feedback_batch(
                snapshot=snapshot,
                channel_id=campaign["channel_id"],
                variants=variant_specs,
            )
            rebuilt = build_finalized_feedback_batch(
                snapshot=snapshot,
                channel_id=campaign["channel_id"],
                variants=tuple(reversed(variant_specs)),
            )
            if batch.to_json() != rebuilt.to_json():
                raise RuntimeError("feedback batch changed across deterministic rebuild")

            finalization_ledger.commit(snapshot, batch)
            finalization_ledger = WindowFinalizationLedger(finalization_path)
            restart_count += 1

            fault_identity = f"{campaign['campaign_id']}|{window.label}"
            if fault_identity == delivery_faults["before_commit"]:
                try:
                    delivery_ledger.commit(batch, fault="before_commit")
                except InjectedDeliveryFault:
                    pass
                delivery_ledger = FeedbackDeliveryLedger(delivery_path)
                restart_count += 1
                receipt = delivery_ledger.commit(batch)
            elif fault_identity == delivery_faults["after_commit"]:
                try:
                    delivery_ledger.commit(batch, fault="after_commit")
                except InjectedDeliveryFault:
                    pass
                delivery_ledger = FeedbackDeliveryLedger(delivery_path)
                restart_count += 1
                receipt = delivery_ledger.commit(batch)
                if receipt.status != "duplicate":
                    raise RuntimeError(
                        "post-commit crash must replay as duplicate delivery"
                    )
            else:
                receipt = delivery_ledger.commit(batch)

            if receipt.status == "committed":
                delivery_count += 1
            elif receipt.status == "duplicate":
                delivery_count += 1
            else:
                raise RuntimeError("unexpected delivery status")

            delivery_ledger = FeedbackDeliveryLedger(delivery_path)
            restart_count += 1
            duplicate = delivery_ledger.commit(batch)
            if duplicate.status != "duplicate":
                raise RuntimeError("repeated delivery must be duplicate")

    if finalization_ledger.finalized_count != learning["finalized_window_count"]:
        raise RuntimeError("finalization count mismatch")
    if delivery_ledger.committed_count != learning["finalized_window_count"]:
        raise RuntimeError("delivery count mismatch")

    learning_wire = canonical_json(learning)
    return {
        "report_version": RELIABILITY_REPORT_VERSION,
        "seed": payload["seed"],
        "unique_event_count": len(payload["events"]),
        "ingestion_attempt_count": len(payload["ingestion_order"]),
        "configured_duplicate_attempt_count": len(payload["duplicate_event_ids"]),
        "designated_late_event_count": len(payload["designated_late_event_ids"]),
        "observed_duplicate_receipt_count": duplicate_receipts,
        "observed_late_accept_count": late_accepts,
        "campaign_count": len(payload["campaigns"]),
        "variant_count": sum(len(item["variants"]) for item in payload["campaigns"]),
        "window_count": sum(len(item["windows"]) for item in payload["campaigns"]),
        "finalized_window_count": finalization_ledger.finalized_count,
        "logical_delivery_count": delivery_ledger.committed_count,
        "restart_count": restart_count,
        "event_stream_sha256": _sha256_bytes(event_path.read_bytes()),
        "finalization_ledger_sha256": _sha256_bytes(
            finalization_path.read_bytes()
        ),
        "delivery_ledger_sha256": _sha256_bytes(delivery_path.read_bytes()),
        "finalized_learning_sha256": _sha256_text(learning_wire),
        "causal": False,
        "interpretation": CAUSALITY_NOTICE,
        "finalized_learning": learning,
    }
