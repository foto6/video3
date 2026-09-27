from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .core import CreatorFeedback, CreatorFeedbackValidationError
from .engine import CAUSALITY_NOTICE, TimeWindow


FEEDBACK_BATCH_VERSION = "growth.feedback_batch.v1"
CREATOR_SEED_HANDOFF_VERSION = "growth.creator_seed.v1"
DELIVERY_LEDGER_VERSION = "growth.feedback_delivery_ledger.v1"


class FeedbackBatchValidationError(ValueError):
    pass


class CreatorSeedValidationError(ValueError):
    pass


class DeliveryConflictError(ValueError):
    pass


class InjectedDeliveryFault(RuntimeError):
    pass


@dataclass(frozen=True)
class FeedbackBatch:
    handoff_version: str
    batch_id: str
    campaign_id: str
    window_label: str
    window_start: str
    window_end: str
    payload_digest: str
    feedback: tuple[CreatorFeedback, ...]
    causal: bool
    interpretation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_version": self.handoff_version,
            "batch_id": self.batch_id,
            "campaign_id": self.campaign_id,
            "window": {
                "label": self.window_label,
                "start": self.window_start,
                "end": self.window_end,
            },
            "payload_digest": self.payload_digest,
            "feedback": [json.loads(item.to_json()) for item in self.feedback],
            "causal": self.causal,
            "interpretation": self.interpretation,
        }

    def to_json(self) -> str:
        validated = type(self).from_dict(self.to_dict())
        return _canonical_json(validated.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FeedbackBatch":
        if not isinstance(payload, Mapping):
            raise FeedbackBatchValidationError("feedback batch must be an object")
        expected_fields = {
            "handoff_version",
            "batch_id",
            "campaign_id",
            "window",
            "payload_digest",
            "feedback",
            "causal",
            "interpretation",
        }
        if set(payload) != expected_fields:
            raise FeedbackBatchValidationError("feedback batch fields must match v1 exactly")
        if payload["handoff_version"] != FEEDBACK_BATCH_VERSION:
            raise FeedbackBatchValidationError("unsupported feedback batch version")
        if payload["causal"] is not False:
            raise FeedbackBatchValidationError("feedback batch must remain observational/non-causal")
        if payload["interpretation"] != CAUSALITY_NOTICE:
            raise FeedbackBatchValidationError("feedback batch interpretation must preserve observational semantics")
        campaign_id = payload["campaign_id"]
        if not isinstance(campaign_id, str) or not campaign_id:
            raise FeedbackBatchValidationError("campaign_id must be a non-empty string")
        window_payload = payload["window"]
        if not isinstance(window_payload, Mapping) or set(window_payload) != {"label", "start", "end"}:
            raise FeedbackBatchValidationError("window must contain label/start/end exactly")
        for field in ("label", "start", "end"):
            value = window_payload[field]
            if not isinstance(value, str) or not value:
                raise FeedbackBatchValidationError(f"window.{field} must be a non-empty string")
        try:
            window = TimeWindow(
                window_payload["label"],
                window_payload["start"],
                window_payload["end"],
            )
        except ValueError as exc:
            raise FeedbackBatchValidationError("invalid feedback batch window") from exc
        feedback_payload = payload["feedback"]
        if not isinstance(feedback_payload, list) or not feedback_payload:
            raise FeedbackBatchValidationError("feedback must be a non-empty array")
        try:
            feedback = tuple(CreatorFeedback.from_dict(item) for item in feedback_payload)
        except CreatorFeedbackValidationError as exc:
            raise FeedbackBatchValidationError("invalid CreatorFeedback 1.0 payload") from exc

        rebuilt = build_feedback_batch(
            campaign_id=campaign_id,
            window=window,
            feedbacks=feedback,
        )
        if payload["batch_id"] != rebuilt.batch_id:
            raise FeedbackBatchValidationError("batch_id does not match campaign/window/evidence identity")
        if payload["payload_digest"] != rebuilt.payload_digest:
            raise FeedbackBatchValidationError("payload_digest does not match byte-stable feedback set")
        if tuple(item.to_json() for item in feedback) != tuple(item.to_json() for item in rebuilt.feedback):
            raise FeedbackBatchValidationError("feedback array is not in canonical deterministic order")
        return rebuilt

    @classmethod
    def from_json(cls, wire_json: str) -> "FeedbackBatch":
        if not isinstance(wire_json, str):
            raise FeedbackBatchValidationError("feedback batch JSON must be a string")
        try:
            payload = json.loads(wire_json)
        except json.JSONDecodeError as exc:
            raise FeedbackBatchValidationError("invalid feedback batch JSON") from exc
        return cls.from_dict(payload)


@dataclass(frozen=True)
class DeliveryReceipt:
    batch_id: str
    sequence: int
    status: str
    payload_digest: str
    seed_digest: str


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_feedback(feedbacks: Iterable[CreatorFeedback]) -> tuple[CreatorFeedback, ...]:
    validated = tuple(CreatorFeedback.from_dict(item.to_dict()) for item in feedbacks)
    if not validated:
        raise FeedbackBatchValidationError("feedback batch must not be empty")
    content_job_ids = [item.content_job_id for item in validated]
    if len(content_job_ids) != len(set(content_job_ids)):
        raise FeedbackBatchValidationError("feedback batch content_job_id values must be unique")
    return tuple(
        sorted(
            validated,
            key=lambda item: (
                item.content_job_id,
                item.variant_id or "",
                item.video_id,
                item.to_json(),
            ),
        )
    )


def _identity_material(
    campaign_id: str,
    window: TimeWindow,
    feedback: tuple[CreatorFeedback, ...],
) -> dict[str, Any]:
    return {
        "campaign_id": campaign_id,
        "window": {"label": window.label, "start": window.start, "end": window.end},
        "evidence": [
            {
                "content_job_id": item.content_job_id,
                "variant_id": item.variant_id,
                "evidence_event_ids": sorted(item.evidence_event_ids),
            }
            for item in feedback
        ],
    }


def _payload_digest(feedback: tuple[CreatorFeedback, ...]) -> str:
    payload_set = "\n".join(item.to_json() for item in feedback)
    return _sha256_text(payload_set)


def build_feedback_batch(
    *,
    campaign_id: str,
    window: TimeWindow,
    feedbacks: Iterable[CreatorFeedback],
) -> FeedbackBatch:
    if not isinstance(campaign_id, str) or not campaign_id:
        raise FeedbackBatchValidationError("campaign_id must be a non-empty string")
    canonical_feedback = _canonical_feedback(feedbacks)
    identity_json = _canonical_json(_identity_material(campaign_id, window, canonical_feedback))
    batch_id = "fb1:" + _sha256_text(identity_json)
    return FeedbackBatch(
        handoff_version=FEEDBACK_BATCH_VERSION,
        batch_id=batch_id,
        campaign_id=campaign_id,
        window_label=window.label,
        window_start=window.start,
        window_end=window.end,
        payload_digest=_payload_digest(canonical_feedback),
        feedback=canonical_feedback,
        causal=False,
        interpretation=CAUSALITY_NOTICE,
    )


@dataclass(frozen=True)
class CreatorSeedHandoff:
    handoff_version: str
    seed_kind: str
    idempotency_key: str
    batch_id: str
    campaign_id: str
    window_label: str
    window_start: str
    window_end: str
    payload_digest: str
    causal: bool
    interpretation: str
    feedback: tuple[CreatorFeedback, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_version": self.handoff_version,
            "seed_kind": self.seed_kind,
            "idempotency_key": self.idempotency_key,
            "batch_id": self.batch_id,
            "campaign_id": self.campaign_id,
            "window": {
                "label": self.window_label,
                "start": self.window_start,
                "end": self.window_end,
            },
            "payload_digest": self.payload_digest,
            "causal": self.causal,
            "interpretation": self.interpretation,
            "feedback": [json.loads(item.to_json()) for item in self.feedback],
        }

    def to_json(self) -> str:
        validated = type(self).from_dict(self.to_dict())
        return _canonical_json(validated.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CreatorSeedHandoff":
        if not isinstance(payload, Mapping):
            raise CreatorSeedValidationError("creator seed handoff must be an object")
        expected_fields = {
            "handoff_version",
            "seed_kind",
            "idempotency_key",
            "batch_id",
            "campaign_id",
            "window",
            "payload_digest",
            "causal",
            "interpretation",
            "feedback",
        }
        if set(payload) != expected_fields:
            raise CreatorSeedValidationError("creator seed fields must match v1 exactly")
        if payload["handoff_version"] != CREATOR_SEED_HANDOFF_VERSION:
            raise CreatorSeedValidationError("unsupported creator seed handoff version")
        if payload["seed_kind"] != "growth_feedback_batch":
            raise CreatorSeedValidationError("unsupported creator seed kind")
        if payload["causal"] is not False:
            raise CreatorSeedValidationError("creator seed must remain observational/non-causal")
        if payload["interpretation"] != CAUSALITY_NOTICE:
            raise CreatorSeedValidationError(
                "creator seed interpretation must preserve observational semantics"
            )
        for field in ("idempotency_key", "batch_id", "campaign_id", "payload_digest"):
            value = payload[field]
            if not isinstance(value, str) or not value:
                raise CreatorSeedValidationError(f"{field} must be a non-empty string")
        if payload["idempotency_key"] != payload["batch_id"]:
            raise CreatorSeedValidationError("idempotency_key must equal batch_id")

        window_payload = payload["window"]
        if not isinstance(window_payload, Mapping) or set(window_payload) != {"label", "start", "end"}:
            raise CreatorSeedValidationError("window must contain label/start/end exactly")
        for field in ("label", "start", "end"):
            value = window_payload[field]
            if not isinstance(value, str) or not value:
                raise CreatorSeedValidationError(f"window.{field} must be a non-empty string")
        try:
            window = TimeWindow(
                window_payload["label"],
                window_payload["start"],
                window_payload["end"],
            )
        except ValueError as exc:
            raise CreatorSeedValidationError("invalid creator seed window") from exc

        feedback_payload = payload["feedback"]
        if not isinstance(feedback_payload, list) or not feedback_payload:
            raise CreatorSeedValidationError("feedback must be a non-empty array")
        try:
            feedback = tuple(CreatorFeedback.from_dict(item) for item in feedback_payload)
        except CreatorFeedbackValidationError as exc:
            raise CreatorSeedValidationError("invalid CreatorFeedback 1.0 payload") from exc

        try:
            batch = build_feedback_batch(
                campaign_id=str(payload["campaign_id"]),
                window=window,
                feedbacks=feedback,
            )
        except FeedbackBatchValidationError as exc:
            raise CreatorSeedValidationError("invalid creator seed feedback batch") from exc

        if payload["batch_id"] != batch.batch_id:
            raise CreatorSeedValidationError(
                "batch_id does not match campaign/window/evidence identity"
            )
        if payload["payload_digest"] != batch.payload_digest:
            raise CreatorSeedValidationError(
                "payload_digest does not match byte-stable feedback set"
            )
        if tuple(item.to_json() for item in feedback) != tuple(
            item.to_json() for item in batch.feedback
        ):
            raise CreatorSeedValidationError(
                "feedback array is not in canonical deterministic order"
            )
        return cls(
            handoff_version=CREATOR_SEED_HANDOFF_VERSION,
            seed_kind="growth_feedback_batch",
            idempotency_key=batch.batch_id,
            batch_id=batch.batch_id,
            campaign_id=batch.campaign_id,
            window_label=batch.window_label,
            window_start=batch.window_start,
            window_end=batch.window_end,
            payload_digest=batch.payload_digest,
            causal=False,
            interpretation=CAUSALITY_NOTICE,
            feedback=batch.feedback,
        )

    @classmethod
    def from_json(cls, wire_json: str) -> "CreatorSeedHandoff":
        if not isinstance(wire_json, str):
            raise CreatorSeedValidationError("creator seed JSON must be a string")
        try:
            payload = json.loads(wire_json)
        except json.JSONDecodeError as exc:
            raise CreatorSeedValidationError("invalid creator seed JSON") from exc
        return cls.from_dict(payload)


def creator_seed_handoff(batch: FeedbackBatch) -> dict[str, Any]:
    validated = FeedbackBatch.from_dict(batch.to_dict())
    return CreatorSeedHandoff(
        handoff_version=CREATOR_SEED_HANDOFF_VERSION,
        seed_kind="growth_feedback_batch",
        idempotency_key=validated.batch_id,
        batch_id=validated.batch_id,
        campaign_id=validated.campaign_id,
        window_label=validated.window_label,
        window_start=validated.window_start,
        window_end=validated.window_end,
        payload_digest=validated.payload_digest,
        causal=False,
        interpretation=CAUSALITY_NOTICE,
        feedback=validated.feedback,
    ).to_dict()


def creator_seed_handoff_json(batch: FeedbackBatch) -> str:
    return CreatorSeedHandoff.from_dict(creator_seed_handoff(batch)).to_json()


class FeedbackDeliveryLedger:
    """Append-only replay-safe commit ledger for Growth -> Creator seed handoffs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DeliveryConflictError(
                        f"invalid delivery ledger JSON on line {line_number}"
                    ) from exc
                required = {
                    "ledger_version",
                    "sequence",
                    "batch_id",
                    "payload_digest",
                    "seed_digest",
                    "status",
                }
                if not isinstance(row, Mapping) or set(row) != required:
                    raise DeliveryConflictError("delivery ledger row fields are invalid")
                if row["ledger_version"] != DELIVERY_LEDGER_VERSION or row["status"] != "committed":
                    raise DeliveryConflictError("unsupported delivery ledger row")
                if row["sequence"] != len(self._rows) + 1:
                    raise DeliveryConflictError("delivery ledger sequence is not contiguous")
                batch_id = row["batch_id"]
                if not isinstance(batch_id, str) or not batch_id:
                    raise DeliveryConflictError("delivery ledger batch_id is invalid")
                if batch_id in self._rows:
                    raise DeliveryConflictError(f"duplicate durable delivery row for {batch_id}")
                self._rows[batch_id] = dict(row)

    def commit(
        self,
        batch: FeedbackBatch,
        *,
        fault: str | None = None,
    ) -> DeliveryReceipt:
        validated = FeedbackBatch.from_dict(batch.to_dict())
        seed_json = creator_seed_handoff_json(validated)
        seed_digest = _sha256_text(seed_json)
        existing = self._rows.get(validated.batch_id)
        if existing is not None:
            if (
                existing["payload_digest"] != validated.payload_digest
                or existing["seed_digest"] != seed_digest
            ):
                raise DeliveryConflictError(
                    f"conflicting delivery for existing batch identity {validated.batch_id}"
                )
            return DeliveryReceipt(
                batch_id=validated.batch_id,
                sequence=int(existing["sequence"]),
                status="duplicate",
                payload_digest=validated.payload_digest,
                seed_digest=seed_digest,
            )

        if fault == "before_commit":
            raise InjectedDeliveryFault("injected crash before delivery commit")
        if fault not in {None, "after_commit"}:
            raise ValueError("fault must be None, 'before_commit', or 'after_commit'")

        sequence = len(self._rows) + 1
        row = {
            "ledger_version": DELIVERY_LEDGER_VERSION,
            "sequence": sequence,
            "batch_id": validated.batch_id,
            "payload_digest": validated.payload_digest,
            "seed_digest": seed_digest,
            "status": "committed",
        }
        serialized = _canonical_json(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._rows[validated.batch_id] = row

        if fault == "after_commit":
            raise InjectedDeliveryFault("injected crash after delivery commit")

        return DeliveryReceipt(
            batch_id=validated.batch_id,
            sequence=sequence,
            status="committed",
            payload_digest=validated.payload_digest,
            seed_digest=seed_digest,
        )

    def receipt_for(self, batch_id: str) -> DeliveryReceipt | None:
        row = self._rows.get(batch_id)
        if row is None:
            return None
        return DeliveryReceipt(
            batch_id=batch_id,
            sequence=int(row["sequence"]),
            status=str(row["status"]),
            payload_digest=str(row["payload_digest"]),
            seed_digest=str(row["seed_digest"]),
        )

    @property
    def committed_count(self) -> int:
        return len(self._rows)
