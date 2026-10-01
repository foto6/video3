# Growth R17 Production Platform Metrics Runtime

## Scope

R17 turns the existing R11 provider-ingestion and R12 collection scheduler contracts into a production-capable **read-only** runtime for Creator-published Instagram Reels, TikTok videos, and YouTube Shorts.

R10-R16 contract versions are preserved. The runtime does not publish, edit, or delete social content. It does not automate CAPTCHA or 2FA. Creator and Media repositories are not modified.

## Current API verification — 2026-10-01

The implementation was written only after checking current platform documentation.

### Instagram / Meta

Current Meta Graph API is v26.0, released July 29, 2026. Meta's official Instagram Postman workspace was current in September 2026 and documents the Instagram API as the professional-account API used to gather insights.

Canonical references:

- https://www.postman.com/meta/instagram/overview
- https://developers.facebook.com/docs/graph-api/changelog/
- https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights

The Meta developer reference blocks automated retrieval in this execution environment. R17 therefore deliberately does **not** assume that every metric in the reference is available for every login path/account/media. It uses the current v26 media-insights endpoint and capability-probes requested metrics. A metric rejected or omitted by the provider is retained as explicit `unavailable_evidence`, never converted to zero.

Supported authorization profiles:

- Instagram Login: `instagram_business_basic` + `instagram_business_manage_insights`
- Facebook Login: `instagram_basic` + `instagram_manage_insights` + `pages_read_engagement`

Professional Business/Creator account access remains a platform prerequisite.

R17 normalizes these when actually returned:

- views
- likes
- comments
- shares
- saves
- Reel total watch time
- Reel average watch time

Reach and total interactions may be provider-observed but have no R10 normalized field and are not fabricated into another metric.

### TikTok

Official TikTok references checked:

- https://developers.tiktok.com/docs/en/tiktok-api-v2-video-query
- https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object
- https://developers.tiktok.com/docs/en/tiktok-api-v2-video-list
- https://developers.tiktok.com/docs/en/oauth-user-access-token-management

The Query Videos and Video Object references were updated August 24, 2026. `/v2/video/query/` is a read-only query operation even though it uses HTTP POST. It requires the user-authorized `video.list` scope and verifies that queried video IDs belong to that user.

Current Content Display video fields normalized by R17:

- `view_count` -> views
- `like_count` -> likes
- `comment_count` -> comments
- `share_count` -> shares

The current Content Display object does not expose watch time, completion, retention, saves/favorites, per-video follows, or link clicks. Those remain explicit unavailable evidence.

### YouTube

Official references checked:

- https://developers.google.com/youtube/v3/docs/videos
- https://developers.google.com/youtube/v3/docs/videos/list
- https://developers.google.com/youtube/analytics/metrics
- https://developers.google.com/youtube/analytics/reference/reports/query

R17 uses `videos.list(part=statistics)` for immediately available video counters and optionally augments them from YouTube Analytics `reports.query`.

`youtube.readonly` is required for the base runtime. `yt-analytics.readonly` enables watch-time, average-view-duration, shares, and subscriber-gain observations when Analytics has data.

Important current semantic change: Google documents that starting August 24, 2026, `video.statistics.viewCount` for all formats including Shorts counts a view when playback begins, including autoplay, hover, click, or tap. R17 stores that provider counter as `views` and does not pretend it has older semantics. `engagedViews` is available in Analytics but has no preserved R10 field, so it is not silently substituted.

## Credential boundary

Durable Growth state stores only:

- `credential_ref_id`
- `authorization_lineage`
- provider account ID
- provider post ID
- optional public post URL
- publish receipt identity/digest
- provider receipt digest
- Media artifact/render SHA-256

Actual access/refresh tokens are resolved at call time through `CredentialResolver`. `ResolvedProviderCredential` exists only in process memory.

The runtime and durable ledgers recursively reject secret-bearing fields. Authorization headers are never copied into reports, fixtures, or ledgers.

## Live lineage gate

`ProductionPlatformMetricsRuntime.register_live_post()` accepts only a validated `growth.shortform_publish_result.v1` with:

- `source_class=platform_export`
- exact provider receipt digest
- exact provider account/post identity
- exact Media artifact digest/render SHA
- `live_performance_claim_allowed=true`

Synthetic or unknown lineage fails closed for live-performance claims.

This preserves the existing R10 source-class separation. CI and sandbox replay remain synthetic.

## Read-only provider clients

R17 adds concrete live clients behind the existing `ProviderPageClient` / R11 adapters:

- `InstagramLiveMetricsClient`
- `TikTokLiveMetricsClient`
- `YouTubeLiveMetricsClient`

`build_live_adapters()` returns the existing normalized:

- `InstagramReelsMetricsAdapter`
- `TikTokMetricsAdapter`
- `YouTubeShortsMetricsAdapter`

No R10 normalized event schema is widened.

## Explicit unavailable evidence

R17 extends durable provider page evidence with `unavailable_evidence`.

Every R10 optional metric whose value is null has a bounded reason. Examples:

- unsupported by current API
- missing required analytics scope
- provider response omitted/delayed the metric
- metric intentionally not normalized because the preserved R10 contract has no matching field

Null means unavailable. It never means zero.

## Error and recovery semantics

Provider failure classification:

| Provider result | Runtime handling |
| --- | --- |
| HTTP 401 / revoked token | terminal `authorization_expired_or_revoked` |
| HTTP 403 permission | terminal `provider_permission_denied` |
| HTTP 403 quota reason | rate-limit backoff |
| HTTP 404 / no owned post | terminal `provider_post_deleted_or_unavailable` |
| HTTP 429 | `Retry-After` respected, durable backoff |
| HTTP 5xx/network | bounded temporary retry |
| older provider revision | bounded stale/out-of-order retry |
| local clock materially behind collection window | bounded temporary retry |
| partial/delayed metrics | supported values normalized; unavailable fields retain reasons |

`ProductionPlatformMetricsRuntime.recover()` can requeue an authorization/permission terminal after an operator fixes the external credential or permission. Deleted/unavailable posts and expired schedules do not auto-recover.

## Freshness and operator status

`runtime.status(post_key)` exposes:

- collector state
- freshness (`fresh`, `stale`, `no_data_yet`, or `broken`)
- lag seconds
- last success
- last error
- error classification
- provider backoff-until
- backfill/recovery state
- latest normalized snapshot digest
- latest provider revision
- explicit unavailable evidence
- publish receipt/provider receipt/Media render lineage

This distinguishes “no new data yet” from collector failure.

## Idempotency and restart behavior

R17 continues to use:

- `growth.provider_metrics_ingest_ledger.v1`
- `growth.metrics_collection_schedule_ledger.v1`
- `growth.reels_feedback_ledger.v1`
- `growth.reels_next_cycle_outbox.v1`

A prepared normalized event is replayed after lost acknowledgement rather than refetched. Duplicate events remain exactly-once at the R10 feedback ledger. Provider revisions reject out-of-order older samples. Higher revisions can legitimately change platform counters.

## One-command sandbox replay

No credentials or live platform requests are needed:

`python -m growth_analytics.platform_metrics_replay --output-dir /tmp/growth-r17-replay`

The replay creates synthetic Creator publish receipts for Instagram, TikTok, and YouTube, runs them through the same provider adapters and R12 scheduler, builds normalized metric snapshots, and emits next-cycle seeds.

The replay explicitly remains:

- `source_class=synthetic_fixture`
- `live_performance_claim_allowed=false`
- Creator-cycle ineligible
- provider-mutation free

The committed readiness report is:

`fixtures/platform_metrics_runtime_v1/readiness_report.json`

The committed deterministic replay is:

`fixtures/platform_metrics_runtime_v1/replay_report.json`

## Production capability boundary

The code path is production-capable **conditional on real account/API authorization**. This milestone does not claim that any specific account is connected or approved.

Blocked until the external account/app satisfies platform requirements:

- Instagram: professional account and current Insights permissions/app access.
- TikTok: approved/user-authorized `video.list`.
- YouTube: OAuth `youtube.readonly`; `yt-analytics.readonly` for deeper Analytics fields.

No live account probe is fabricated in CI.
