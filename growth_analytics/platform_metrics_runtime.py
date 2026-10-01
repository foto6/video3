from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .autonomous_reels import (
    OPTIONAL_METRICS,
    NextCycleOutbox,
    ReelsFeedbackLedger,
    canonical_json,
    parse_publish_result,
    sha256_json,
)
from .collection_scheduler import (
    CollectionScheduleLedger,
    CollectionSchedulePolicy,
    DurableMetricsCollectionScheduler,
)
from .event_stream import parse_timestamp
from .provider_ingest import (
    CredentialReference,
    InstagramReelsMetricsAdapter,
    ProviderAuthenticationError,
    ProviderContractError,
    ProviderIngestLedger,
    ProviderPermissionDenied,
    ProviderPostUnavailable,
    ProviderRateLimited,
    TikTokMetricsAdapter,
    YouTubeShortsMetricsAdapter,
)


PLATFORM_METRICS_RUNTIME_VERSION = "growth.platform_metrics_runtime.r17.v1"
PLATFORM_METRICS_RUNTIME_LEDGER_VERSION = (
    "growth.platform_metrics_runtime_ledger.r17.v1"
)
PLATFORM_METRICS_READINESS_VERSION = (
    "growth.platform_metrics_readiness.r17.v1"
)

META_GRAPH_VERSION = "v26.0"
META_IG_LOGIN_BASE = (
    f"https://graph.instagram.com/{META_GRAPH_VERSION}"
)
META_FB_LOGIN_BASE = (
    f"https://graph.facebook.com/{META_GRAPH_VERSION}"
)
TIKTOK_API_BASE = "https://open.tiktokapis.com"
YOUTUBE_DATA_BASE = "https://www.googleapis.com"
YOUTUBE_ANALYTICS_BASE = "https://youtubeanalytics.googleapis.com"

YOUTUBE_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/youtube.readonly"
)
YOUTUBE_ANALYTICS_SCOPE = (
    "https://www.googleapis.com/auth/yt-analytics.readonly"
)

INSTAGRAM_LOGIN_INSIGHTS_SCOPE = (
    "instagram_business_manage_insights"
)
INSTAGRAM_LOGIN_BASIC_SCOPE = "instagram_business_basic"
INSTAGRAM_FB_INSIGHTS_SCOPE = "instagram_manage_insights"
INSTAGRAM_FB_BASIC_SCOPE = "instagram_basic"
INSTAGRAM_FB_PAGE_SCOPE = "pages_read_engagement"

TIKTOK_VIDEO_LIST_SCOPE = "video.list"

_SECRET_NAMES = frozenset({
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "secret",
    "client_secret",
    "api_key",
    "token",
})


class PlatformMetricsRuntimeError(RuntimeError):
    pass


class ProviderTransientError(ConnectionError):
    pass


class CredentialResolutionError(PlatformMetricsRuntimeError):
    pass


class LiveLineageError(PlatformMetricsRuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedProviderCredential:
    access_token: str
    scopes: tuple[str, ...]
    api_base_url: str | None = None
    subject_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.access_token, str) or not self.access_token:
            raise CredentialResolutionError(
                "resolved access token must be non-empty"
            )
        if (
            not isinstance(self.scopes, tuple)
            or any(
                not isinstance(scope, str) or not scope
                for scope in self.scopes
            )
        ):
            raise CredentialResolutionError(
                "resolved scopes must be a tuple of non-empty strings"
            )
        if self.api_base_url is not None:
            if (
                not isinstance(self.api_base_url, str)
                or not self.api_base_url.startswith("https://")
            ):
                raise CredentialResolutionError(
                    "api_base_url must be https when supplied"
                )
        if (
            self.subject_id is not None
            and (
                not isinstance(self.subject_id, str)
                or not self.subject_id
            )
        ):
            raise CredentialResolutionError(
                "subject_id must be null or non-empty"
            )


class CredentialResolver(Protocol):
    def resolve(
        self,
        *,
        platform: str,
        credential_ref_id: str,
        authorization_lineage: str,
    ) -> ResolvedProviderCredential:
        ...


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class ReadOnlyHttpTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        ...


class UrllibReadOnlyTransport:
    """Small HTTPS transport; mutation methods/endpoints are never exposed."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        if method not in {"GET", "POST"}:
            raise PlatformMetricsRuntimeError(
                "runtime transport permits GET and read-only query POST only"
            )
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https":
            raise PlatformMetricsRuntimeError(
                "runtime transport requires https"
            )
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers=dict(headers),
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
            ) as response:
                return HttpResponse(
                    status=int(response.status),
                    headers={
                        str(k): str(v)
                        for k, v in response.headers.items()
                    },
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(
                status=int(exc.code),
                headers={
                    str(k): str(v)
                    for k, v in exc.headers.items()
                },
                body=exc.read(),
            )
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ) as exc:
            raise ProviderTransientError(
                "provider network request failed"
            ) from exc


def _assert_secret_free(
    value: Any,
    path: str = "root",
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _SECRET_NAMES:
                raise PlatformMetricsRuntimeError(
                    f"secret-bearing field forbidden in durable evidence: {path}.{key}"
                )
            _assert_secret_free(
                child,
                f"{path}.{key}",
            )
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_secret_free(
                child,
                f"{path}[{index}]",
            )
    elif isinstance(value, str):
        lowered = value.lower()
        if (
            lowered.startswith("bearer ")
            or lowered.startswith("basic ")
        ):
            raise PlatformMetricsRuntimeError(
                f"authorization value forbidden in durable evidence at {path}"
            )


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise PlatformMetricsRuntimeError(
            "runtime clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _json_body(response: HttpResponse) -> dict[str, Any]:
    try:
        decoded = json.loads(
            response.body.decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderContractError(
            "provider response is not valid JSON"
        ) from exc
    if not isinstance(decoded, dict):
        raise ProviderContractError(
            "provider JSON response must be object"
        )
    _assert_secret_free(decoded, "provider_response")
    return decoded


def _retry_after_seconds(
    headers: Mapping[str, str],
    *,
    now: datetime,
    default: float = 60.0,
) -> float:
    raw = None
    for key, value in headers.items():
        if key.lower() == "retry-after":
            raw = value
            break
    if raw is None:
        return default
    try:
        seconds = float(raw)
        if seconds > 0:
            return seconds
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return default
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = (
        when.astimezone(timezone.utc)
        - now.astimezone(timezone.utc)
    ).total_seconds()
    return max(1.0, delta)


def _error_reasons(payload: Mapping[str, Any]) -> set[str]:
    reasons: set[str] = set()
    error = payload.get("error")
    if isinstance(error, Mapping):
        for field in ("reason", "status", "code", "type"):
            value = error.get(field)
            if isinstance(value, str):
                reasons.add(value)
        errors = error.get("errors")
        if isinstance(errors, list):
            for item in errors:
                if isinstance(item, Mapping):
                    value = item.get("reason")
                    if isinstance(value, str):
                        reasons.add(value)
    return reasons


def _raise_http(
    response: HttpResponse,
    *,
    provider: str,
    now: datetime,
    decoded: Mapping[str, Any] | None = None,
) -> None:
    if 200 <= response.status < 300:
        return
    payload = (
        decoded
        if decoded is not None
        else (
            _json_body(response)
            if response.body
            else {}
        )
    )
    reasons = {
        reason.lower()
        for reason in _error_reasons(payload)
    }
    if response.status == 401:
        raise ProviderAuthenticationError(
            f"{provider} authorization expired or revoked"
        )
    if response.status == 404:
        raise ProviderPostUnavailable(
            f"{provider} post is unavailable or deleted"
        )
    if response.status == 429:
        raise ProviderRateLimited(
            _retry_after_seconds(
                response.headers,
                now=now,
            )
        )
    if response.status == 403:
        quota_markers = {
            "quotaexceeded",
            "dailylimitexceeded",
            "ratelimitexceeded",
            "userratelimitexceeded",
        }
        if reasons & quota_markers:
            raise ProviderRateLimited(
                _retry_after_seconds(
                    response.headers,
                    now=now,
                    default=300.0,
                )
            )
        raise ProviderPermissionDenied(
            f"{provider} permission or account tier denied"
        )
    if response.status >= 500:
        raise ProviderTransientError(
            f"{provider} provider outage HTTP {response.status}"
        )
    raise ProviderContractError(
        f"{provider} request failed HTTP {response.status}"
    )


class _BaseLiveClient:
    evidence_origin = "live_provider"
    fixture_source_sha256 = None

    def __init__(
        self,
        *,
        credential_resolver: CredentialResolver,
        transport: ReadOnlyHttpTransport | None = None,
        now: Callable[[], datetime] | None = None,
        timeout_seconds: float = 20.0,
        max_clock_skew_seconds: float = 300.0,
    ) -> None:
        self.credential_resolver = credential_resolver
        self.transport = transport or UrllibReadOnlyTransport()
        self.now = now or (
            lambda: datetime.now(timezone.utc)
        )
        self.timeout_seconds = float(timeout_seconds)
        self.max_clock_skew_seconds = float(
            max_clock_skew_seconds
        )
        if self.timeout_seconds <= 0:
            raise PlatformMetricsRuntimeError(
                "timeout_seconds must be positive"
            )
        if self.max_clock_skew_seconds <= 0:
            raise PlatformMetricsRuntimeError(
                "max_clock_skew_seconds must be positive"
            )

    def _clock(
        self,
        *,
        window_end: str,
    ) -> tuple[datetime, str, int]:
        now = self.now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise PlatformMetricsRuntimeError(
                "runtime clock must return aware datetime"
            )
        current = now.astimezone(timezone.utc)
        expected_end = parse_timestamp(window_end)
        if (
            current.timestamp()
            + self.max_clock_skew_seconds
            < expected_end.timestamp()
        ):
            raise ProviderTransientError(
                "provider collection clock is behind requested window"
            )
        captured_at = _utc_timestamp(current)
        revision = max(
            1,
            int(current.timestamp() * 1000),
        )
        return current, captured_at, revision

    def _credential(
        self,
        *,
        platform: str,
        credential_ref_id: str,
        authorization_lineage: str,
    ) -> ResolvedProviderCredential:
        resolved = self.credential_resolver.resolve(
            platform=platform,
            credential_ref_id=credential_ref_id,
            authorization_lineage=authorization_lineage,
        )
        if not isinstance(
            resolved,
            ResolvedProviderCredential,
        ):
            raise CredentialResolutionError(
                "credential resolver returned wrong type"
            )
        return resolved

    def _request(
        self,
        *,
        method: str,
        url: str,
        credential: ResolvedProviderCredential,
        json_body: Mapping[str, Any] | None = None,
    ) -> HttpResponse:
        body = (
            None
            if json_body is None
            else canonical_json(
                dict(json_body)
            ).encode("utf-8")
        )
        headers = {
            "Authorization":
                f"Bearer {credential.access_token}",
            "Accept": "application/json",
            "User-Agent":
                "foto6-growth-r17-readonly-metrics/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        return self.transport.request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=self.timeout_seconds,
        )

    @staticmethod
    def _unavailable(
        available_normalized: Sequence[str],
        reasons: Mapping[str, str],
    ) -> dict[str, str]:
        available = set(available_normalized)
        result: dict[str, str] = {}
        for metric in OPTIONAL_METRICS:
            if metric in available:
                continue
            reason = reasons.get(
                metric,
                "not_exposed_by_selected_provider_api",
            )
            result[metric] = reason
        return result


class InstagramLiveMetricsClient(_BaseLiveClient):
    """
    Current v26 media-insights reader.

    It supports both Instagram Login and Facebook Login via the scope set
    resolved outside durable state. Unsupported/deprecated metrics are
    omitted and represented by unavailable_evidence.
    """

    _metrics = (
        "views",
        "reach",
        "likes",
        "comments",
        "saved",
        "shares",
        "total_interactions",
        "ig_reels_avg_watch_time",
        "ig_reels_video_view_total_time",
    )

    @staticmethod
    def _base(
        credential: ResolvedProviderCredential,
    ) -> str:
        if credential.api_base_url is not None:
            return credential.api_base_url.rstrip("/")
        scopes = set(credential.scopes)
        if (
            INSTAGRAM_LOGIN_BASIC_SCOPE in scopes
            and INSTAGRAM_LOGIN_INSIGHTS_SCOPE in scopes
        ):
            return META_IG_LOGIN_BASE
        if (
            INSTAGRAM_FB_BASIC_SCOPE in scopes
            and INSTAGRAM_FB_INSIGHTS_SCOPE in scopes
            and INSTAGRAM_FB_PAGE_SCOPE in scopes
        ):
            return META_FB_LOGIN_BASE
        raise ProviderPermissionDenied(
            "Instagram insights scopes are missing"
        )

    @staticmethod
    def _extract_metric(
        raw: Mapping[str, Any],
    ) -> Any:
        total = raw.get("total_value")
        if isinstance(total, Mapping):
            value = total.get("value")
            if value is not None:
                return value
        values = raw.get("values")
        if isinstance(values, list) and values:
            latest = values[-1]
            if isinstance(latest, Mapping):
                return latest.get("value")
        return raw.get("value")

    def _one_call(
        self,
        *,
        base: str,
        post_id: str,
        metrics: Sequence[str],
        credential: ResolvedProviderCredential,
        now: datetime,
    ) -> tuple[dict[str, Any], set[str]]:
        query = urllib.parse.urlencode({
            "metric": ",".join(metrics),
        })
        response = self._request(
            method="GET",
            url=(
                f"{base}/{urllib.parse.quote(post_id, safe='')}"
                f"/insights?{query}"
            ),
            credential=credential,
        )
        decoded = _json_body(response)
        if response.status == 400:
            error = decoded.get("error")
            code = (
                error.get("code")
                if isinstance(error, Mapping)
                else None
            )
            if code == 190:
                raise ProviderAuthenticationError(
                    "Instagram authorization expired or revoked"
                )
            if code in {4, 17, 32, 613}:
                raise ProviderRateLimited(
                    _retry_after_seconds(
                        response.headers,
                        now=now,
                    )
                )
            if code in {10, 200, 294}:
                raise ProviderPermissionDenied(
                    "Instagram insights permission denied"
                )
            if code != 100:
                _raise_http(
                    response,
                    provider="instagram",
                    now=now,
                    decoded=decoded,
                )
        else:
            _raise_http(
                response,
                provider="instagram",
                now=now,
                decoded=decoded,
            )
        if response.status == 400:
            return {}, set(metrics)
        data = decoded.get("data")
        if not isinstance(data, list):
            raise ProviderContractError(
                "Instagram insights response missing data array"
            )
        values: dict[str, Any] = {}
        returned: set[str] = set()
        for item in data:
            if not isinstance(item, Mapping):
                raise ProviderContractError(
                    "Instagram insight row must be object"
                )
            name = item.get("name")
            if (
                not isinstance(name, str)
                or name not in metrics
            ):
                continue
            returned.add(name)
            value = self._extract_metric(item)
            if value is not None:
                values[name] = value
        return values, set(metrics) - returned

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
        if platform != "instagram_reels":
            raise ProviderContractError(
                "Instagram client platform mismatch"
            )
        if cursor is not None:
            raise ProviderContractError(
                "Instagram live client is single-page"
            )
        now, captured_at, revision = self._clock(
            window_end=window_end
        )
        credential = self._credential(
            platform=platform,
            credential_ref_id=credential_ref_id,
            authorization_lineage=authorization_lineage,
        )
        base = self._base(credential)
        values, unsupported = self._one_call(
            base=base,
            post_id=post_id,
            metrics=self._metrics,
            credential=credential,
            now=now,
        )
        if unsupported:
            recovered: dict[str, Any] = {}
            still_unsupported: set[str] = set()
            for metric in sorted(unsupported):
                one, missing = self._one_call(
                    base=base,
                    post_id=post_id,
                    metrics=(metric,),
                    credential=credential,
                    now=now,
                )
                recovered.update(one)
                still_unsupported.update(missing)
            values.update(recovered)
            unsupported = still_unsupported

        normalized_available = []
        mapping = {
            "views": "views",
            "likes": "likes",
            "comments": "comments",
            "shares": "shares",
            "saved": "saves",
            "ig_reels_avg_watch_time":
                "average_watch_duration_seconds",
            "ig_reels_video_view_total_time":
                "watch_time_seconds",
        }
        for provider_metric, normalized in mapping.items():
            if provider_metric in values:
                normalized_available.append(normalized)
        reasons = {
            "impressions":
                "Meta removed impressions for current media insights; views is used instead",
            "completed_views":
                "Instagram media insights does not expose completed-view count through this adapter",
            "completion_rate":
                "Instagram completion rate is not assumed from current media-insights fields",
            "retention_points":
                "Instagram retention curve is not exposed by this media-insights adapter",
            "retention_denominator_views":
                "retention curve unavailable",
            "follows":
                "per-reel follows not requested because availability varies by login flow/media",
            "link_clicks":
                "link clicks are not a Reel media-insights metric in this adapter",
        }
        for provider_metric, normalized in mapping.items():
            if provider_metric in unsupported:
                reasons[normalized] = (
                    f"Instagram API v26 did not expose metric {provider_metric} "
                    "for this media/account/login path"
                )
        return {
            "provider_export_id":
                f"instagram:{account_id}:{post_id}:{window_end}",
            "revision": revision,
            "captured_at": captured_at,
            "window": {
                "start": window_start,
                "end": window_end,
            },
            "complete": bool(values),
            "next_cursor": None,
            "metrics": values,
            "unavailable_evidence": self._unavailable(
                normalized_available,
                reasons,
            ),
        }


class TikTokLiveMetricsClient(_BaseLiveClient):
    _fields = (
        "id",
        "view_count",
        "like_count",
        "comment_count",
        "share_count",
    )

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
        if platform != "tiktok":
            raise ProviderContractError(
                "TikTok client platform mismatch"
            )
        if cursor is not None:
            raise ProviderContractError(
                "TikTok video query is single-page for one post"
            )
        now, captured_at, revision = self._clock(
            window_end=window_end
        )
        credential = self._credential(
            platform=platform,
            credential_ref_id=credential_ref_id,
            authorization_lineage=authorization_lineage,
        )
        if TIKTOK_VIDEO_LIST_SCOPE not in set(
            credential.scopes
        ):
            raise ProviderPermissionDenied(
                "TikTok video.list scope is required"
            )
        base = (
            credential.api_base_url.rstrip("/")
            if credential.api_base_url
            else TIKTOK_API_BASE
        )
        fields = ",".join(self._fields)
        response = self._request(
            method="POST",
            url=(
                f"{base}/v2/video/query/"
                f"?{urllib.parse.urlencode({'fields': fields})}"
            ),
            credential=credential,
            json_body={
                "filters": {
                    "video_ids": [post_id],
                }
            },
        )
        decoded = _json_body(response)
        _raise_http(
            response,
            provider="tiktok",
            now=now,
            decoded=decoded,
        )
        error = decoded.get("error")
        if isinstance(error, Mapping):
            code = error.get("code")
            if code not in {None, 0, "ok", "OK"}:
                code_text = str(code).lower()
                if (
                    "access" in code_text
                    or "token" in code_text
                ):
                    raise ProviderAuthenticationError(
                        "TikTok access token rejected"
                    )
                if (
                    "scope" in code_text
                    or "permission" in code_text
                ):
                    raise ProviderPermissionDenied(
                        "TikTok video.list permission denied"
                    )
                if "rate" in code_text:
                    raise ProviderRateLimited(60)
                raise ProviderContractError(
                    f"TikTok API error code {code}"
                )
        data = decoded.get("data")
        videos = (
            data.get("videos")
            if isinstance(data, Mapping)
            else None
        )
        if not isinstance(videos, list):
            raise ProviderContractError(
                "TikTok query response missing videos array"
            )
        target = None
        for video in videos:
            if (
                isinstance(video, Mapping)
                and str(video.get("id")) == post_id
            ):
                target = dict(video)
                break
        if target is None:
            raise ProviderPostUnavailable(
                "TikTok video not returned for authorized user"
            )
        available = {
            "view_count": "views",
            "like_count": "likes",
            "comment_count": "comments",
            "share_count": "shares",
        }
        normalized_available = [
            normalized
            for provider_name, normalized in available.items()
            if target.get(provider_name) is not None
        ]
        reasons = {
            "impressions":
                "TikTok Display API video object does not expose impressions",
            "watch_time_seconds":
                "TikTok Display API video object does not expose total watch time",
            "average_watch_duration_seconds":
                "TikTok Display API video object does not expose average watch duration",
            "completed_views":
                "TikTok Display API video object does not expose completed views",
            "completion_rate":
                "TikTok Display API video object does not expose completion rate",
            "retention_points":
                "TikTok Display API video object does not expose retention curve",
            "retention_denominator_views":
                "retention curve unavailable",
            "saves":
                "TikTok Display API video object does not expose saves/favorites in Content Display",
            "follows":
                "TikTok per-video follower conversion is unavailable in Content Display",
            "link_clicks":
                "TikTok Display API video object does not expose link clicks",
        }
        return {
            "provider_export_id":
                f"tiktok:{account_id}:{post_id}:{window_end}",
            "revision": revision,
            "captured_at": captured_at,
            "window": {
                "start": window_start,
                "end": window_end,
            },
            "complete": True,
            "next_cursor": None,
            "metrics": target,
            "unavailable_evidence": self._unavailable(
                normalized_available,
                reasons,
            ),
        }


class YouTubeLiveMetricsClient(_BaseLiveClient):
    _analytics_metrics = (
        "views",
        "engagedViews",
        "estimatedMinutesWatched",
        "averageViewDuration",
        "likes",
        "comments",
        "shares",
        "subscribersGained",
    )

    @staticmethod
    def _column_map(
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        headers = payload.get("columnHeaders")
        rows = payload.get("rows")
        if not isinstance(headers, list):
            raise ProviderContractError(
                "YouTube Analytics missing columnHeaders"
            )
        if rows is None:
            return {}
        if not isinstance(rows, list):
            raise ProviderContractError(
                "YouTube Analytics rows must be array"
            )
        if not rows:
            return {}
        row = rows[-1]
        if not isinstance(row, list):
            raise ProviderContractError(
                "YouTube Analytics row must be array"
            )
        names = []
        for header in headers:
            if (
                not isinstance(header, Mapping)
                or not isinstance(header.get("name"), str)
            ):
                raise ProviderContractError(
                    "YouTube Analytics header invalid"
                )
            names.append(header["name"])
        if len(names) != len(row):
            raise ProviderContractError(
                "YouTube Analytics header/row width mismatch"
            )
        return dict(zip(names, row))

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
        if platform != "youtube_shorts":
            raise ProviderContractError(
                "YouTube client platform mismatch"
            )
        if cursor is not None:
            raise ProviderContractError(
                "YouTube per-video query is single-page"
            )
        now, captured_at, revision = self._clock(
            window_end=window_end
        )
        credential = self._credential(
            platform=platform,
            credential_ref_id=credential_ref_id,
            authorization_lineage=authorization_lineage,
        )
        scopes = set(credential.scopes)
        if YOUTUBE_READONLY_SCOPE not in scopes:
            raise ProviderPermissionDenied(
                "youtube.readonly scope is required"
            )
        data_base = (
            credential.api_base_url.rstrip("/")
            if credential.api_base_url
            else YOUTUBE_DATA_BASE
        )
        response = self._request(
            method="GET",
            url=(
                f"{data_base}/youtube/v3/videos?"
                + urllib.parse.urlencode({
                    "part": "statistics",
                    "id": post_id,
                })
            ),
            credential=credential,
        )
        decoded = _json_body(response)
        _raise_http(
            response,
            provider="youtube_data",
            now=now,
            decoded=decoded,
        )
        items = decoded.get("items")
        if not isinstance(items, list):
            raise ProviderContractError(
                "YouTube Data API missing items"
            )
        if not items:
            raise ProviderPostUnavailable(
                "YouTube video not returned"
            )
        item = items[0]
        statistics = (
            item.get("statistics")
            if isinstance(item, Mapping)
            else None
        )
        if not isinstance(statistics, Mapping):
            raise ProviderContractError(
                "YouTube video statistics missing"
            )
        metrics: dict[str, Any] = {}
        for source, target in (
            ("viewCount", "views"),
            ("likeCount", "likes"),
            ("commentCount", "comments"),
        ):
            value = statistics.get(source)
            if value is not None:
                try:
                    metrics[target] = int(value)
                except (TypeError, ValueError) as exc:
                    raise ProviderContractError(
                        f"YouTube {source} is not integer"
                    ) from exc

        analytics_available = (
            YOUTUBE_ANALYTICS_SCOPE in scopes
        )
        reasons: dict[str, str] = {
            "impressions":
                "YouTube per-video impression CTR is not requested by the preserved Growth v1 runtime",
            "completed_views":
                "YouTube Analytics does not expose a direct completed-view count in this adapter",
            "completion_rate":
                "completion is not synthesized from view counters",
            "retention_points":
                "audience-retention reports require a separate report shape and are not normalized by this adapter",
            "retention_denominator_views":
                "retention curve unavailable",
            "saves":
                "YouTube Shorts has no normalized save metric in the selected APIs",
            "link_clicks":
                "YouTube Data/Analytics APIs selected here do not expose post link clicks",
        }
        if analytics_available:
            analytics_base = YOUTUBE_ANALYTICS_BASE
            start_date = parse_timestamp(
                window_start
            ).date().isoformat()
            end_date = parse_timestamp(
                window_end
            ).date().isoformat()
            query = urllib.parse.urlencode({
                "ids": "channel==MINE",
                "startDate": start_date,
                "endDate": end_date,
                "metrics": ",".join(
                    self._analytics_metrics
                ),
                "filters": f"video=={post_id}",
            })
            analytics_response = self._request(
                method="GET",
                url=f"{analytics_base}/v2/reports?{query}",
                credential=credential,
            )
            analytics_json = _json_body(
                analytics_response
            )
            _raise_http(
                analytics_response,
                provider="youtube_analytics",
                now=now,
                decoded=analytics_json,
            )
            analytics = self._column_map(
                analytics_json
            )
            if "estimatedMinutesWatched" in analytics:
                metrics["estimated_minutes_watched"] = (
                    analytics["estimatedMinutesWatched"]
                )
            if "averageViewDuration" in analytics:
                metrics[
                    "average_view_duration_seconds"
                ] = analytics["averageViewDuration"]
            if "shares" in analytics:
                metrics["shares"] = analytics["shares"]
            if "subscribersGained" in analytics:
                metrics["subscribers_gained"] = (
                    analytics["subscribersGained"]
                )
            for name, key in (
                ("likes", "likes"),
                ("comments", "comments"),
            ):
                if key not in metrics and name in analytics:
                    metrics[key] = analytics[name]
        else:
            reasons.update({
                "watch_time_seconds":
                    "yt-analytics.readonly scope not available",
                "average_watch_duration_seconds":
                    "yt-analytics.readonly scope not available",
                "shares":
                    "yt-analytics.readonly scope not available",
                "follows":
                    "yt-analytics.readonly scope not available for subscribersGained",
            })

        normalized_available = []
        normal_map = {
            "views": "views",
            "likes": "likes",
            "comments": "comments",
            "shares": "shares",
            "subscribers_gained": "follows",
            "estimated_minutes_watched":
                "watch_time_seconds",
            "average_view_duration_seconds":
                "average_watch_duration_seconds",
        }
        for provider_metric, normalized in normal_map.items():
            if metrics.get(provider_metric) is not None:
                normalized_available.append(normalized)
        if analytics_available:
            if "estimated_minutes_watched" not in metrics:
                reasons["watch_time_seconds"] = (
                    "YouTube Analytics row unavailable/delayed for window"
                )
            if "average_view_duration_seconds" not in metrics:
                reasons["average_watch_duration_seconds"] = (
                    "YouTube Analytics averageViewDuration unavailable/delayed"
                )
            if "shares" not in metrics:
                reasons["shares"] = (
                    "YouTube Analytics shares unavailable/delayed"
                )
            if "subscribers_gained" not in metrics:
                reasons["follows"] = (
                    "YouTube Analytics subscribersGained unavailable/delayed"
                )
        return {
            "provider_export_id":
                f"youtube:{account_id}:{post_id}:{window_end}",
            "revision": revision,
            "captured_at": captured_at,
            "window": {
                "start": window_start,
                "end": window_end,
            },
            "complete": True,
            "next_cursor": None,
            "metrics": metrics,
            "unavailable_evidence": self._unavailable(
                normalized_available,
                reasons,
            ),
        }


def build_live_adapters(
    *,
    credential_resolver: CredentialResolver,
    transport: ReadOnlyHttpTransport | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    common = {
        "credential_resolver": credential_resolver,
        "transport": transport,
        "now": now,
    }
    return {
        "instagram_reels":
            InstagramReelsMetricsAdapter(
                InstagramLiveMetricsClient(**common)
            ),
        "tiktok":
            TikTokMetricsAdapter(
                TikTokLiveMetricsClient(**common)
            ),
        "youtube_shorts":
            YouTubeShortsMetricsAdapter(
                YouTubeLiveMetricsClient(**common)
            ),
    }


_RUNTIME_LEDGER_FIELDS = frozenset({
    "ledger_version",
    "sequence",
    "event_type",
    "post_key",
    "observed_at",
    "payload",
})


class PlatformMetricsRuntimeLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: list[dict[str, Any]] = []
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
                raise PlatformMetricsRuntimeError(
                    f"invalid runtime ledger JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row) != _RUNTIME_LEDGER_FIELDS
                or row["ledger_version"]
                != PLATFORM_METRICS_RUNTIME_LEDGER_VERSION
                or row["sequence"] != len(self._rows) + 1
            ):
                raise PlatformMetricsRuntimeError(
                    "runtime ledger row invalid"
                )
            _assert_secret_free(row)
            self._rows.append(dict(row))

    def append(
        self,
        *,
        event_type: str,
        post_key: str,
        observed_at: str,
        payload: Mapping[str, Any],
    ) -> None:
        parse_timestamp(observed_at)
        _assert_secret_free(payload)
        row = {
            "ledger_version":
                PLATFORM_METRICS_RUNTIME_LEDGER_VERSION,
            "sequence": len(self._rows) + 1,
            "event_type": event_type,
            "post_key": post_key,
            "observed_at": observed_at,
            "payload": json.loads(
                canonical_json(dict(payload))
            ),
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
        self._rows.append(row)

    def rows_for(
        self,
        post_key: str,
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            json.loads(canonical_json(row))
            for row in self._rows
            if row["post_key"] == post_key
        )


class ProductionPlatformMetricsRuntime:
    """
    Production orchestration over R11/R12 durability.

    It never resolves credentials into durable state. Live registration is
    accepted only when the R10 publish receipt has exact provider receipt and
    Media artifact lineage.
    """

    def __init__(
        self,
        *,
        root: str | Path,
        adapters: Mapping[str, Any],
        policy: CollectionSchedulePolicy | None = None,
        now: Callable[[], datetime] | None = None,
        freshness_seconds: int = 21600,
    ) -> None:
        self.root = Path(root)
        self.now = now or (
            lambda: datetime.now(timezone.utc)
        )
        self.freshness_seconds = int(
            freshness_seconds
        )
        if self.freshness_seconds <= 0:
            raise PlatformMetricsRuntimeError(
                "freshness_seconds must be positive"
            )
        self.schedule_ledger = CollectionScheduleLedger(
            self.root / "schedule.jsonl"
        )
        self.provider_ledger = ProviderIngestLedger(
            self.root / "provider.jsonl"
        )
        self.feedback_ledger = ReelsFeedbackLedger(
            self.root / "feedback.jsonl"
        )
        self.seed_outbox = NextCycleOutbox(
            self.root / "seed_outbox.jsonl"
        )
        self.runtime_ledger = PlatformMetricsRuntimeLedger(
            self.root / "runtime.jsonl"
        )
        self.scheduler = DurableMetricsCollectionScheduler(
            schedule_ledger=self.schedule_ledger,
            provider_ledger=self.provider_ledger,
            feedback_ledger=self.feedback_ledger,
            seed_outbox=self.seed_outbox,
            adapters=adapters,
            policy=policy,
            now=self.now,
        )

    def register_live_post(
        self,
        *,
        publish_result: Mapping[str, Any],
        credential: CredentialReference,
        post_url: str | None = None,
    ) -> tuple[str, str]:
        published = parse_publish_result(
            publish_result
        )
        if published["source_class"] != "platform_export":
            raise LiveLineageError(
                "production runtime requires platform_export publish receipt"
            )
        provenance = published["provenance"]
        if (
            provenance["live_performance_claim_allowed"]
            is not True
            or provenance["provider_receipt_digest"] is None
        ):
            raise LiveLineageError(
                "live performance requires exact provider receipt lineage"
            )
        artifact = published["artifact"]
        if (
            not isinstance(
                artifact["media_artifact_digest"],
                str,
            )
            or len(
                artifact["media_artifact_digest"]
            ) != 64
        ):
            raise LiveLineageError(
                "live performance requires exact Media render SHA"
            )
        if post_url is not None:
            parsed = urllib.parse.urlsplit(post_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise LiveLineageError(
                    "post_url must be https when supplied"
                )
        post_key, status = self.scheduler.register_post(
            publish_result=published,
            credential=credential,
        )
        now = _utc_timestamp(self.now())
        self.runtime_ledger.append(
            event_type="register",
            post_key=post_key,
            observed_at=now,
            payload={
                "platform": published["platform"],
                "account_id": published["account_id"],
                "provider_post_id": published["post_id"],
                "provider_post_url": post_url,
                "publish_result_id":
                    published["publish_result_id"],
                "publish_result_digest":
                    published["publish_result_digest"],
                "provider_receipt_digest":
                    provenance["provider_receipt_digest"],
                "media_artifact_id":
                    artifact["media_artifact_id"],
                "media_render_sha256":
                    artifact["media_artifact_digest"],
                "credential_ref_id":
                    credential.credential_ref_id,
                "authorization_lineage":
                    credential.authorization_lineage,
                "registration_status": status,
                "live_performance_claim_allowed": True,
            },
        )
        return post_key, status

    def tick(self) -> dict[str, Any]:
        report = self.scheduler.tick()
        observed_at = report["observed_at"]
        enriched = []
        for result in report["results"]:
            post_key = result["post_key"]
            state = self.schedule_ledger.get(
                post_key
            )
            if state is None:
                raise PlatformMetricsRuntimeError(
                    "scheduler result references unknown post"
                )
            status = self.status(post_key)
            self.runtime_ledger.append(
                event_type="collection_status",
                post_key=post_key,
                observed_at=observed_at,
                payload=status,
            )
            enriched.append({
                **result,
                "runtime_status": status,
            })
        return {
            **report,
            "runtime_version":
                PLATFORM_METRICS_RUNTIME_VERSION,
            "results": enriched,
        }

    def recover(
        self,
        post_key: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        state = self.schedule_ledger.get(
            post_key
        )
        if state is None:
            raise PlatformMetricsRuntimeError(
                "cannot recover unknown post"
            )
        if not isinstance(reason, str) or not reason:
            raise PlatformMetricsRuntimeError(
                "recovery reason must be non-empty"
            )
        if state["status"] not in {
            "terminal",
            "expired",
        }:
            return self.status(post_key)
        if state["status"] == "expired":
            raise PlatformMetricsRuntimeError(
                "expired collection cannot be recovered"
            )
        terminal = state["terminal_reason"] or ""
        if "deleted_or_unavailable" in terminal:
            raise PlatformMetricsRuntimeError(
                "deleted/unavailable provider post cannot be recovered"
            )
        now = _utc_timestamp(self.now())
        self.schedule_ledger.update(
            post_key,
            updated_at=now,
            status="scheduled",
            attempts_for_window=0,
            next_due_at=now,
            last_error=f"recovery:{reason}"[:160],
            backoff_until=None,
            terminal_reason=None,
        )
        status = self.status(post_key)
        self.runtime_ledger.append(
            event_type="recovery",
            post_key=post_key,
            observed_at=now,
            payload={
                "reason": reason,
                "status": status,
            },
        )
        return status

    def status(
        self,
        post_key: str,
    ) -> dict[str, Any]:
        state = self.schedule_ledger.get(
            post_key
        )
        if state is None:
            raise PlatformMetricsRuntimeError(
                "unknown post"
            )
        published = state["publish_result"]
        now = self.now().astimezone(
            timezone.utc
        )
        published_at = parse_timestamp(
            published["published_at"]
        )
        last_success = (
            None
            if state["last_success_at"] is None
            else parse_timestamp(
                state["last_success_at"]
            )
        )
        lag = (
            now - (
                last_success
                if last_success is not None
                else published_at
            )
        ).total_seconds()
        lag_seconds = max(0, int(lag))

        if (
            state["status"] == "terminal"
            and state["terminal_reason"]
            == "schedule_complete"
        ):
            collector_state = "complete"
        elif state["status"] == "backoff":
            collector_state = "backoff"
        elif state["status"] in {
            "terminal",
            "expired",
        }:
            collector_state = "broken_or_terminal"
        else:
            collector_state = "running"

        if last_success is None:
            freshness = (
                "broken"
                if collector_state
                == "broken_or_terminal"
                else "no_data_yet"
            )
        elif lag_seconds <= self.freshness_seconds:
            freshness = "fresh"
        else:
            freshness = "stale"

        terminal = state["terminal_reason"] or ""
        error = state["last_error"]
        if terminal == "authorization_expired_or_revoked":
            error_class = "authorization"
            backfill = "eligible_after_external_reauthorization"
        elif terminal == "provider_permission_denied":
            error_class = "permission"
            backfill = "eligible_after_permission_grant"
        elif terminal == "provider_post_deleted_or_unavailable":
            error_class = "post_unavailable"
            backfill = "blocked_post_unavailable"
        elif state["status"] == "expired":
            error_class = "expired"
            backfill = "blocked_expired"
        elif error == "provider_rate_limited":
            error_class = "rate_limit"
            backfill = "automatic_after_retry_after"
        elif (
            isinstance(error, str)
            and error.startswith("temporary:")
        ):
            error_class = "provider_outage_or_network"
            backfill = "automatic_bounded_retry"
        elif error == "stale_provider_revision":
            error_class = "out_of_order_or_clock_skew"
            backfill = "automatic_bounded_retry"
        elif error:
            error_class = "provider_contract"
            backfill = "operator_review"
        else:
            error_class = None
            backfill = "not_needed"

        latest = self.provider_ledger.latest_page_for_post(
            platform=published["platform"],
            account_id=published["account_id"],
            post_id=published["post_id"],
        )
        unavailable = (
            {}
            if latest is None
            else latest.get(
                "unavailable_evidence",
                {},
            )
        )
        status = {
            "platform": published["platform"],
            "post_id": published["post_id"],
            "cycle_revision":
                published["cycle_revision"],
            "collector_state": collector_state,
            "freshness": freshness,
            "lag_seconds": lag_seconds,
            "last_success_at":
                state["last_success_at"],
            "last_error": state["last_error"],
            "error_classification": error_class,
            "backoff_until":
                state["backoff_until"],
            "backfill_recovery_state": backfill,
            "latest_snapshot_digest":
                state["latest_snapshot_digest"],
            "latest_provider_revision":
                state["latest_provider_revision"],
            "unavailable_evidence": unavailable,
            "lineage": {
                "publish_result_id":
                    published["publish_result_id"],
                "publish_result_digest":
                    published["publish_result_digest"],
                "provider_receipt_digest":
                    published["provenance"][
                        "provider_receipt_digest"
                    ],
                "media_render_sha256":
                    published["artifact"][
                        "media_artifact_digest"
                    ],
                "live_performance_claim_allowed":
                    published["provenance"][
                        "live_performance_claim_allowed"
                    ],
            },
        }
        _assert_secret_free(status)
        return json.loads(
            canonical_json(status)
        )
