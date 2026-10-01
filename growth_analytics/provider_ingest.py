from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .adapters import ReadOnlyProviderBase
from .autonomous_reels import (
    OPTIONAL_METRICS,
    PLATFORM_METRICS_VERSION,
    PLATFORMS,
    ReelsFeedbackLedger,
    build_platform_metrics_event,
    canonical_json,
    sha256_json,
)
from .event_stream import parse_timestamp


PROVIDER_ADAPTER_VERSION = "growth.provider_metrics_adapter.v1"
PROVIDER_INGEST_LEDGER_VERSION = "growth.provider_metrics_ingest_ledger.v1"
PROVIDER_INGEST_REPORT_VERSION = "growth.provider_metrics_ingest_report.v1"

EVIDENCE_ORIGINS = frozenset({"live_provider", "mock_fixture"})
_SECRET_FIELD_NAMES = frozenset({
    "authorization",
    "authorization_header",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
})


class ProviderIngestError(ValueError):
    pass


class ProviderContractError(ProviderIngestError):
    pass


class ProviderLedgerConflictError(ProviderIngestError):
    pass


class StaleProviderRevision(ProviderIngestError):
    pass


class BackoffActive(ProviderIngestError):
    def __init__(self, not_before: str) -> None:
        super().__init__(f"provider backoff active until {not_before}")
        self.not_before = not_before


class ProviderRateLimited(ProviderIngestError):
    def __init__(self, retry_after_seconds: float) -> None:
        if (
            isinstance(retry_after_seconds, bool)
            or not isinstance(retry_after_seconds, (int, float))
            or retry_after_seconds <= 0
        ):
            raise ProviderContractError(
                "retry_after_seconds must be positive"
            )
        self.retry_after_seconds = float(retry_after_seconds)
        super().__init__(
            f"provider rate limited for {self.retry_after_seconds} seconds"
        )


class InjectedProviderIngestFault(RuntimeError):
    pass


@dataclass(frozen=True)
class CredentialReference:
    credential_ref_id: str
    authorization_lineage: str

    def __post_init__(self) -> None:
        _safe_reference(
            self.credential_ref_id,
            "credential_ref_id",
            "credential-ref:",
        )
        _safe_reference(
            self.authorization_lineage,
            "authorization_lineage",
            "authz:",
        )


@dataclass(frozen=True)
class ProviderFetchRequest:
    platform: str
    account_id: str
    post_id: str
    cycle_revision: int
    window_start: str
    window_end: str
    collection_id: str
    credential: CredentialReference

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise ProviderContractError("unsupported provider platform")
        for value, name in (
            (self.account_id, "account_id"),
            (self.post_id, "post_id"),
            (self.collection_id, "collection_id"),
        ):
            _nonempty(value, name)
        if (
            isinstance(self.cycle_revision, bool)
            or not isinstance(self.cycle_revision, int)
            or self.cycle_revision < 1
        ):
            raise ProviderContractError(
                "cycle_revision must be an integer >= 1"
            )
        if parse_timestamp(self.window_start) >= parse_timestamp(
            self.window_end
        ):
            raise ProviderContractError(
                "window_start must be before window_end"
            )

    @property
    def ingest_key(self) -> str:
        identity = {
            "platform": self.platform,
            "account_id": self.account_id,
            "post_id": self.post_id,
            "cycle_revision": self.cycle_revision,
            "window": {
                "start": self.window_start,
                "end": self.window_end,
            },
            "collection_id": self.collection_id,
        }
        return "pmi1:" + sha256_json(identity)


@dataclass(frozen=True)
class ProviderMetricsPage:
    platform: str
    provider_export_id: str
    provider_revision: int
    captured_at: str
    window_start: str
    window_end: str
    complete: bool
    available_metrics: tuple[str, ...]
    metrics: Mapping[str, Any]
    request_cursor: str | None
    next_cursor: str | None
    raw_page_digest: str
    content_digest: str
    evidence_origin: str
    fixture_source_sha256: str | None

    @property
    def source_identity(self) -> str:
        return (
            f"{self.platform}/{self.provider_export_id}"
            f"/revision/{self.provider_revision}"
        )


@dataclass(frozen=True)
class ProviderIngestOutcome:
    ingest_key: str
    status: str
    sink_status: str
    metrics_event: Mapping[str, Any]
    provider_revision: int
    page_count: int


class ProviderPageClient(Protocol):
    evidence_origin: str
    fixture_source_sha256: str | None

    def fetch_metrics_page(
        self,
        *,
        platform: str,
        account_id: str,
        post_id: str,
        window_start: str,
        window_end: str,
        cursor: str | None,
        credential_ref_id: str,
        authorization_lineage: str,
    ) -> Mapping[str, Any]:
        ...


class ProviderMetricsAdapter(Protocol):
    contract_version: str
    platform: str
    read_only: bool

    def fetch_page(
        self,
        request: ProviderFetchRequest,
        cursor: str | None,
    ) -> ProviderMetricsPage:
        ...


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderContractError(
            f"{field} must be a non-empty string"
        )
    return value


def _safe_reference(
    value: Any,
    field: str,
    required_prefix: str,
) -> str:
    text = _nonempty(value, field)
    if len(text) > 200 or not text.startswith(required_prefix):
        raise ProviderContractError(
            f"{field} must be a bounded {required_prefix} reference"
        )
    lower = text.lower()
    if (
        lower.startswith("bearer ")
        or lower.startswith("basic ")
        or "\n" in text
        or "\r" in text
    ):
        raise ProviderContractError(
            f"{field} contains secret-like material"
        )
    return text


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ProviderContractError(
            f"{field} must be lowercase SHA-256 hex"
        )
    return value


def _int_metric(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise ProviderContractError(
            f"{field} must be a non-negative integer"
        )
    return value


def _number_metric(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or float(value) < 0
    ):
        raise ProviderContractError(
            f"{field} must be a non-negative number"
        )
    return round(float(value), 8)


def _ratio_metric(value: Any, field: str) -> float:
    result = _number_metric(value, field)
    if result > 1.0:
        raise ProviderContractError(
            f"{field} must be a ratio in [0, 1]"
        )
    return result


def _assert_secret_free(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).lower()
            if name in _SECRET_FIELD_NAMES:
                raise ProviderContractError(
                    f"secret-bearing field forbidden in durable state: {path}.{key}"
                )
            _assert_secret_free(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_secret_free(child, f"{path}[{index}]")
    elif isinstance(value, str):
        lower = value.lower()
        if lower.startswith("bearer ") or lower.startswith("basic "):
            raise ProviderContractError(
                f"secret-like authorization value forbidden at {path}"
            )


class _MappedReadOnlyAdapter(ReadOnlyProviderBase):
    contract_version = PROVIDER_ADAPTER_VERSION
    platform = ""
    read_only = True

    def __init__(self, client: ProviderPageClient) -> None:
        if client.evidence_origin not in EVIDENCE_ORIGINS:
            raise ProviderContractError(
                "client evidence_origin must be live_provider or mock_fixture"
            )
        if client.evidence_origin == "mock_fixture":
            _digest(
                client.fixture_source_sha256,
                "fixture_source_sha256",
            )
        elif client.fixture_source_sha256 is not None:
            raise ProviderContractError(
                "live provider client cannot carry fixture provenance"
            )
        self.client = client

    def _normalize_metrics(
        self,
        metrics: Mapping[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    def fetch_page(
        self,
        request: ProviderFetchRequest,
        cursor: str | None,
    ) -> ProviderMetricsPage:
        if request.platform != self.platform:
            raise ProviderContractError(
                "request platform does not match adapter"
            )
        raw = self.client.fetch_metrics_page(
            platform=request.platform,
            account_id=request.account_id,
            post_id=request.post_id,
            window_start=request.window_start,
            window_end=request.window_end,
            cursor=cursor,
            credential_ref_id=request.credential.credential_ref_id,
            authorization_lineage=request.credential.authorization_lineage,
        )
        if not isinstance(raw, Mapping):
            raise ProviderContractError(
                "provider page must be a mapping"
            )
        _assert_secret_free(raw, "provider_page")
        required = {
            "provider_export_id",
            "revision",
            "captured_at",
            "window",
            "complete",
            "next_cursor",
            "metrics",
        }
        if not required.issubset(raw):
            missing = sorted(required - set(raw))
            raise ProviderContractError(
                f"provider page missing required fields: {missing}"
            )
        provider_export_id = _nonempty(
            raw["provider_export_id"],
            "provider_export_id",
        )
        revision = raw["revision"]
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
        ):
            raise ProviderContractError(
                "provider revision must be an integer >= 1"
            )
        captured_at = _nonempty(raw["captured_at"], "captured_at")
        parse_timestamp(captured_at)
        window = raw["window"]
        if (
            not isinstance(window, Mapping)
            or set(window) != {"start", "end"}
        ):
            raise ProviderContractError(
                "provider window must contain start/end exactly"
            )
        if (
            window["start"] != request.window_start
            or window["end"] != request.window_end
        ):
            raise ProviderContractError(
                "provider window does not match requested bounds"
            )
        if not isinstance(raw["complete"], bool):
            raise ProviderContractError(
                "provider complete must be boolean"
            )
        next_cursor = raw["next_cursor"]
        if next_cursor is not None:
            _nonempty(next_cursor, "next_cursor")
        provider_metrics = raw["metrics"]
        if not isinstance(provider_metrics, Mapping):
            raise ProviderContractError(
                "provider metrics must be a mapping"
            )
        normalized = self._normalize_metrics(provider_metrics)
        if set(normalized) != OPTIONAL_METRICS:
            raise ProviderContractError(
                "adapter must emit every v1 metric key exactly"
            )
        available = tuple(
            sorted(
                name
                for name, value in normalized.items()
                if value is not None
            )
        )
        raw_page_digest = hashlib.sha256(
            canonical_json(raw).encode("utf-8")
        ).hexdigest()
        content_digest = sha256_json({
            "provider_export_id": provider_export_id,
            "revision": revision,
            "captured_at": captured_at,
            "window": dict(window),
            "complete": raw["complete"],
            "metrics": dict(provider_metrics),
        })
        return ProviderMetricsPage(
            platform=self.platform,
            provider_export_id=provider_export_id,
            provider_revision=revision,
            captured_at=captured_at,
            window_start=window["start"],
            window_end=window["end"],
            complete=raw["complete"],
            available_metrics=available,
            metrics=normalized,
            request_cursor=cursor,
            next_cursor=next_cursor,
            raw_page_digest=raw_page_digest,
            content_digest=content_digest,
            evidence_origin=self.client.evidence_origin,
            fixture_source_sha256=self.client.fixture_source_sha256,
        )

    @staticmethod
    def _empty_metrics() -> dict[str, Any]:
        return {name: None for name in OPTIONAL_METRICS}

    @staticmethod
    def _put(
        target: dict[str, Any],
        source: Mapping[str, Any],
        provider_name: str,
        normalized_name: str,
        converter: Callable[[Any, str], Any],
    ) -> None:
        if provider_name not in source or source[provider_name] is None:
            return
        target[normalized_name] = converter(
            source[provider_name],
            provider_name,
        )


class InstagramReelsMetricsAdapter(_MappedReadOnlyAdapter):
    platform = "instagram_reels"

    def _normalize_metrics(
        self,
        metrics: Mapping[str, Any],
    ) -> dict[str, Any]:
        out = self._empty_metrics()
        for provider_name, normalized_name in (
            ("plays", "views"),
            ("likes", "likes"),
            ("comments", "comments"),
            ("shares", "shares"),
            ("saved", "saves"),
            ("follows", "follows"),
            ("link_clicks", "link_clicks"),
            ("impressions", "impressions"),
        ):
            self._put(
                out,
                metrics,
                provider_name,
                normalized_name,
                _int_metric,
            )
        if metrics.get("total_watch_time_ms") is not None:
            out["watch_time_seconds"] = round(
                _number_metric(
                    metrics["total_watch_time_ms"],
                    "total_watch_time_ms",
                ) / 1000.0,
                8,
            )
        if metrics.get("average_watch_time_ms") is not None:
            out["average_watch_duration_seconds"] = round(
                _number_metric(
                    metrics["average_watch_time_ms"],
                    "average_watch_time_ms",
                ) / 1000.0,
                8,
            )
        if metrics.get("completed_plays") is not None:
            out["completed_views"] = _int_metric(
                metrics["completed_plays"],
                "completed_plays",
            )
        if metrics.get("completion_rate") is not None:
            out["completion_rate"] = _ratio_metric(
                metrics["completion_rate"],
                "completion_rate",
            )
        if metrics.get("retention_curve") is not None:
            curve = metrics["retention_curve"]
            if not isinstance(curve, list) or not curve:
                raise ProviderContractError(
                    "retention_curve must be a non-empty array"
                )
            out["retention_points"] = [
                {
                    "position": _ratio_metric(
                        point["position"],
                        "retention_curve.position",
                    ),
                    "retained": _ratio_metric(
                        point["retained"],
                        "retention_curve.retained",
                    ),
                }
                for point in curve
            ]
            if out["views"] is None:
                raise ProviderContractError(
                    "Instagram retention requires plays denominator"
                )
            out["retention_denominator_views"] = out["views"]
        return out


class TikTokMetricsAdapter(_MappedReadOnlyAdapter):
    platform = "tiktok"

    def _normalize_metrics(
        self,
        metrics: Mapping[str, Any],
    ) -> dict[str, Any]:
        out = self._empty_metrics()
        for provider_name, normalized_name in (
            ("video_views", "views"),
            ("likes", "likes"),
            ("comments", "comments"),
            ("shares", "shares"),
            ("profile_follows", "follows"),
        ):
            self._put(
                out,
                metrics,
                provider_name,
                normalized_name,
                _int_metric,
            )
        for provider_name, normalized_name in (
            ("total_play_time_seconds", "watch_time_seconds"),
            (
                "average_time_watched_seconds",
                "average_watch_duration_seconds",
            ),
        ):
            self._put(
                out,
                metrics,
                provider_name,
                normalized_name,
                _number_metric,
            )
        if metrics.get("full_video_watched_rate") is not None:
            out["completion_rate"] = _ratio_metric(
                metrics["full_video_watched_rate"],
                "full_video_watched_rate",
            )
        return out


class YouTubeShortsMetricsAdapter(_MappedReadOnlyAdapter):
    platform = "youtube_shorts"

    def _normalize_metrics(
        self,
        metrics: Mapping[str, Any],
    ) -> dict[str, Any]:
        out = self._empty_metrics()
        for provider_name, normalized_name in (
            ("views", "views"),
            ("likes", "likes"),
            ("comments", "comments"),
            ("shares", "shares"),
            ("subscribers_gained", "follows"),
        ):
            self._put(
                out,
                metrics,
                provider_name,
                normalized_name,
                _int_metric,
            )
        if metrics.get("estimated_minutes_watched") is not None:
            out["watch_time_seconds"] = round(
                _number_metric(
                    metrics["estimated_minutes_watched"],
                    "estimated_minutes_watched",
                ) * 60.0,
                8,
            )
        self._put(
            out,
            metrics,
            "average_view_duration_seconds",
            "average_watch_duration_seconds",
            _number_metric,
        )
        return out


class ProviderIngestLedger:
    """Append-only provider collection state. It stores references, never secrets."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: list[dict[str, Any]] = []
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
                raise ProviderLedgerConflictError(
                    f"invalid provider ledger JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "event_type",
                    "ingest_key",
                    "payload",
                }
            ):
                raise ProviderLedgerConflictError(
                    "provider ledger row fields invalid"
                )
            if row["ledger_version"] != PROVIDER_INGEST_LEDGER_VERSION:
                raise ProviderLedgerConflictError(
                    "unsupported provider ledger version"
                )
            if row["sequence"] != len(self._rows) + 1:
                raise ProviderLedgerConflictError(
                    "provider ledger sequence not contiguous"
                )
            _assert_secret_free(row)
            self._rows.append(dict(row))

    def _append(
        self,
        event_type: str,
        ingest_key: str,
        payload: Mapping[str, Any],
    ) -> None:
        _assert_secret_free(payload)
        row = {
            "ledger_version": PROVIDER_INGEST_LEDGER_VERSION,
            "sequence": len(self._rows) + 1,
            "event_type": event_type,
            "ingest_key": ingest_key,
            "payload": json.loads(canonical_json(dict(payload))),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._rows.append(row)

    def ensure_begin(self, request: ProviderFetchRequest) -> None:
        payload = {
            "platform": request.platform,
            "account_id": request.account_id,
            "post_id": request.post_id,
            "cycle_revision": request.cycle_revision,
            "window": {
                "start": request.window_start,
                "end": request.window_end,
            },
            "collection_id": request.collection_id,
            "credential_ref_id": request.credential.credential_ref_id,
            "authorization_lineage":
                request.credential.authorization_lineage,
        }
        existing = [
            row for row in self._rows
            if row["ingest_key"] == request.ingest_key
            and row["event_type"] == "begin"
        ]
        if existing:
            if existing[0]["payload"] != payload:
                raise ProviderLedgerConflictError(
                    "ingest identity changed request metadata"
                )
            return
        self._append("begin", request.ingest_key, payload)

    def pages_for(self, ingest_key: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            row["payload"]
            for row in self._rows
            if row["ingest_key"] == ingest_key
            and row["event_type"] == "page"
        )

    def next_cursor(self, ingest_key: str) -> str | None:
        pages = self.pages_for(ingest_key)
        if not pages:
            return None
        return pages[-1]["next_cursor"]

    def record_page(
        self,
        request: ProviderFetchRequest,
        page: ProviderMetricsPage,
    ) -> str:
        max_revision = self.max_revision_for_window(request)
        if (
            max_revision is not None
            and page.provider_revision < max_revision
        ):
            raise StaleProviderRevision(
                "provider export revision is older than durable evidence"
            )
        page_identity = {
            "platform": page.platform,
            "provider_export_id": page.provider_export_id,
            "provider_revision": page.provider_revision,
            "request_cursor": page.request_cursor,
        }
        page_id = "pmp1:" + sha256_json(page_identity)
        existing_pages = self.pages_for(request.ingest_key)
        for existing in existing_pages:
            if existing["page_id"] == page_id:
                if existing["raw_page_digest"] != page.raw_page_digest:
                    raise ProviderLedgerConflictError(
                        "provider page identity changed payload"
                    )
                return "duplicate"
        duplicate_of = None
        for existing in existing_pages:
            if (
                existing["content_digest"] == page.content_digest
                and existing["provider_export_id"]
                == page.provider_export_id
                and existing["provider_revision"]
                == page.provider_revision
            ):
                duplicate_of = existing["page_id"]
                break
        payload = {
            "page_id": page_id,
            "platform": page.platform,
            "provider_export_id": page.provider_export_id,
            "provider_revision": page.provider_revision,
            "captured_at": page.captured_at,
            "window": {
                "start": page.window_start,
                "end": page.window_end,
            },
            "complete": page.complete,
            "available_metrics": list(page.available_metrics),
            "metrics": dict(page.metrics),
            "request_cursor": page.request_cursor,
            "next_cursor": page.next_cursor,
            "raw_page_digest": page.raw_page_digest,
            "content_digest": page.content_digest,
            "duplicate_of": duplicate_of,
            "source_identity": page.source_identity,
            "evidence_origin": page.evidence_origin,
            "fixture_source_sha256":
                page.fixture_source_sha256,
        }
        self._append("page", request.ingest_key, payload)
        return "duplicate_content" if duplicate_of else "accepted"

    def record_backoff(
        self,
        ingest_key: str,
        *,
        retry_after_seconds: float,
        not_before: str,
    ) -> None:
        self._append(
            "backoff",
            ingest_key,
            {
                "retry_after_seconds": retry_after_seconds,
                "not_before": not_before,
            },
        )

    def active_backoff(
        self,
        ingest_key: str,
        now: datetime,
    ) -> str | None:
        rows = [
            row["payload"]
            for row in self._rows
            if row["ingest_key"] == ingest_key
            and row["event_type"] == "backoff"
        ]
        if not rows:
            return None
        not_before = rows[-1]["not_before"]
        return (
            not_before
            if now < parse_timestamp(not_before)
            else None
        )

    def record_prepared(
        self,
        ingest_key: str,
        *,
        event: Mapping[str, Any],
        provider_revision: int,
        page_count: int,
        source_identity: str,
    ) -> str:
        event_digest = event["metrics_event_digest"]
        existing = self.prepared_for(ingest_key)
        if existing is not None:
            if existing["event"]["metrics_event_digest"] != event_digest:
                raise ProviderLedgerConflictError(
                    "prepared metrics event changed after restart"
                )
            return "duplicate"
        self._append(
            "prepared",
            ingest_key,
            {
                "event": dict(event),
                "provider_revision": provider_revision,
                "page_count": page_count,
                "source_identity": source_identity,
            },
        )
        return "prepared"

    def prepared_for(
        self,
        ingest_key: str,
    ) -> dict[str, Any] | None:
        for row in reversed(self._rows):
            if (
                row["ingest_key"] == ingest_key
                and row["event_type"] == "prepared"
            ):
                return row["payload"]
        return None

    def record_sink_ack(
        self,
        ingest_key: str,
        *,
        metrics_event_digest: str,
        sink_status: str,
    ) -> str:
        existing = self.ack_for(ingest_key)
        if existing is not None:
            if (
                existing["metrics_event_digest"]
                != metrics_event_digest
            ):
                raise ProviderLedgerConflictError(
                    "sink acknowledgement digest changed"
                )
            return "duplicate"
        self._append(
            "sink_ack",
            ingest_key,
            {
                "metrics_event_digest": metrics_event_digest,
                "sink_status": sink_status,
            },
        )
        return "acknowledged"

    def ack_for(
        self,
        ingest_key: str,
    ) -> dict[str, Any] | None:
        for row in reversed(self._rows):
            if (
                row["ingest_key"] == ingest_key
                and row["event_type"] == "sink_ack"
            ):
                return row["payload"]
        return None

    def max_revision_for_window(
        self,
        request: ProviderFetchRequest,
    ) -> int | None:
        revisions: list[int] = []
        target = {
            "start": request.window_start,
            "end": request.window_end,
        }
        for row in self._rows:
            if row["event_type"] != "page":
                continue
            payload = row["payload"]
            if (
                payload["platform"] == request.platform
                and payload["window"] == target
            ):
                begin = self._begin_for(row["ingest_key"])
                if (
                    begin is not None
                    and begin["account_id"] == request.account_id
                    and begin["post_id"] == request.post_id
                ):
                    revisions.append(payload["provider_revision"])
        return max(revisions) if revisions else None

    def _begin_for(
        self,
        ingest_key: str,
    ) -> dict[str, Any] | None:
        for row in self._rows:
            if (
                row["ingest_key"] == ingest_key
                and row["event_type"] == "begin"
            ):
                return row["payload"]
        return None

    @property
    def row_count(self) -> int:
        return len(self._rows)


class ProviderMetricsIngestor:
    def __init__(
        self,
        *,
        adapter: ProviderMetricsAdapter,
        provider_ledger: ProviderIngestLedger,
        feedback_ledger: ReelsFeedbackLedger,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if adapter.contract_version != PROVIDER_ADAPTER_VERSION:
            raise ProviderContractError(
                "unsupported provider adapter contract version"
            )
        if adapter.read_only is not True:
            raise ProviderContractError(
                "provider metrics adapters must be read-only"
            )
        self.adapter = adapter
        self.provider_ledger = provider_ledger
        self.feedback_ledger = feedback_ledger
        self.now = now or (lambda: datetime.now(timezone.utc))

    def ingest_window(
        self,
        request: ProviderFetchRequest,
        *,
        inject_fault: str | None = None,
    ) -> ProviderIngestOutcome:
        if request.platform != self.adapter.platform:
            raise ProviderContractError(
                "request platform does not match ingestor adapter"
            )
        self.provider_ledger.ensure_begin(request)
        existing_ack = self.provider_ledger.ack_for(
            request.ingest_key
        )
        prepared = self.provider_ledger.prepared_for(
            request.ingest_key
        )
        if existing_ack is not None and prepared is not None:
            return ProviderIngestOutcome(
                ingest_key=request.ingest_key,
                status="duplicate",
                sink_status=existing_ack["sink_status"],
                metrics_event=prepared["event"],
                provider_revision=prepared["provider_revision"],
                page_count=prepared["page_count"],
            )
        if prepared is not None:
            return self._deliver_prepared(
                request,
                prepared,
                inject_fault=inject_fault,
            )

        current_now = self._normalized_now()
        active = self.provider_ledger.active_backoff(
            request.ingest_key,
            current_now,
        )
        if active is not None:
            raise BackoffActive(active)

        cursor = self.provider_ledger.next_cursor(
            request.ingest_key
        )
        while True:
            try:
                page = self.adapter.fetch_page(request, cursor)
            except ProviderRateLimited as exc:
                not_before = _timestamp(
                    current_now
                    + timedelta(
                        seconds=exc.retry_after_seconds
                    )
                )
                self.provider_ledger.record_backoff(
                    request.ingest_key,
                    retry_after_seconds=
                        exc.retry_after_seconds,
                    not_before=not_before,
                )
                raise BackoffActive(not_before) from exc
            self.provider_ledger.record_page(request, page)
            if inject_fault == "after_page_persist":
                raise InjectedProviderIngestFault(
                    "fault after provider page durability boundary"
                )
            cursor = page.next_cursor
            if cursor is None:
                break

        prepared = self._prepare_event(request)
        self.provider_ledger.record_prepared(
            request.ingest_key,
            event=prepared["event"],
            provider_revision=prepared["provider_revision"],
            page_count=prepared["page_count"],
            source_identity=prepared["source_identity"],
        )
        return self._deliver_prepared(
            request,
            prepared,
            inject_fault=inject_fault,
        )

    def _deliver_prepared(
        self,
        request: ProviderFetchRequest,
        prepared: Mapping[str, Any],
        *,
        inject_fault: str | None,
    ) -> ProviderIngestOutcome:
        event = prepared["event"]
        sink_status = self.feedback_ledger.ingest_platform_metrics(
            event
        )
        if inject_fault == "after_sink_accept":
            raise InjectedProviderIngestFault(
                "fault after R10 feedback ledger acceptance"
            )
        self.provider_ledger.record_sink_ack(
            request.ingest_key,
            metrics_event_digest=event["metrics_event_digest"],
            sink_status=sink_status,
        )
        return ProviderIngestOutcome(
            ingest_key=request.ingest_key,
            status="accepted",
            sink_status=sink_status,
            metrics_event=event,
            provider_revision=prepared["provider_revision"],
            page_count=prepared["page_count"],
        )

    def _prepare_event(
        self,
        request: ProviderFetchRequest,
    ) -> dict[str, Any]:
        pages = list(
            self.provider_ledger.pages_for(request.ingest_key)
        )
        if not pages:
            raise ProviderLedgerConflictError(
                "cannot prepare event without provider pages"
            )
        unique = [
            page for page in pages
            if page["duplicate_of"] is None
        ]
        first = unique[0]
        for page in unique[1:]:
            for field in (
                "platform",
                "provider_export_id",
                "provider_revision",
                "captured_at",
                "window",
                "complete",
                "evidence_origin",
                "fixture_source_sha256",
            ):
                if page[field] != first[field]:
                    raise ProviderLedgerConflictError(
                        f"provider page export changed {field}"
                    )
        merged = {name: None for name in OPTIONAL_METRICS}
        available: set[str] = set()
        for page in unique:
            for name in page["available_metrics"]:
                value = page["metrics"][name]
                if (
                    merged[name] is not None
                    and merged[name] != value
                ):
                    raise ProviderLedgerConflictError(
                        f"provider pages conflict on metric {name}"
                    )
                merged[name] = value
                available.add(name)
        export_digest = sha256_json({
            "provider_export_id": first["provider_export_id"],
            "provider_revision": first["provider_revision"],
            "raw_page_digests": [
                page["raw_page_digest"]
                for page in pages
            ],
        })
        source_class = (
            "platform_export"
            if first["evidence_origin"] == "live_provider"
            else "synthetic_fixture"
        )
        event = build_platform_metrics_event(
            source_class=source_class,
            platform=request.platform,
            account_id=request.account_id,
            post_id=request.post_id,
            cycle_revision=request.cycle_revision,
            captured_at=first["captured_at"],
            window_start=request.window_start,
            window_end=request.window_end,
            complete=first["complete"],
            available_metrics=sorted(available),
            metrics=merged,
            export_id=(
                f"{first['provider_export_id']}"
                f"@revision-{first['provider_revision']}"
            ),
            export_digest=export_digest,
            fixture_source_sha256=(
                None
                if source_class == "platform_export"
                else first["fixture_source_sha256"]
            ),
        )
        if event["contract_version"] != PLATFORM_METRICS_VERSION:
            raise ProviderLedgerConflictError(
                "R10 metrics contract version drifted"
            )
        return {
            "event": event,
            "provider_revision": first["provider_revision"],
            "page_count": len(pages),
            "source_identity": first["source_identity"],
        }

    def _normalized_now(self) -> datetime:
        value = self.now()
        if not isinstance(value, datetime):
            raise ProviderContractError(
                "now provider must return datetime"
            )
        if value.tzinfo is None:
            raise ProviderContractError(
                "now provider must return timezone-aware datetime"
            )
        return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
