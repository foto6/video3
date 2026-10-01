# Growth R11 Provider Metrics Ingestion

## Scope

This layer collects post-publication short-form analytics through a versioned, read-only adapter boundary and normalizes them into the existing \`growth.shortform_platform_metrics.v1\` event contract. It does not publish, delete, edit, update accounts, or otherwise mutate providers.

Supported adapter identities are \`instagram_reels\`, \`tiktok\`, and \`youtube_shorts\`. The adapter contract is \`growth.provider_metrics_adapter.v1\`; the durable collection ledger is \`growth.provider_metrics_ingest_ledger.v1\`.

## Read-only provider boundary

A provider adapter exposes only \`fetch_page(request, cursor)\`. Its client receives a credential reference plus authorization-lineage reference, not token material. The durable ledger accepts only bounded identifiers prefixed with \`credential-ref:\` and \`authz:\`; secret-bearing fields such as access tokens, refresh tokens, passwords, authorization headers, and Bearer/Basic values are rejected before durability.

The adapter surface deliberately has no publish/delete/edit/update methods. It inherits the existing account-mutation guard, which raises if account mutation is attempted.

## Pagination and restart

Every collection attempt has a stable \`collection_id\`, which produces a deterministic \`pmi1:\` ingest key. The provider ledger records:

- begin metadata with source identity and credential references;
- each normalized page, requested cursor, next cursor, raw-page digest, content digest, provider export revision, and fixture/live evidence origin;
- rate-limit backoff deadlines;
- the fully prepared R10 metrics event;
- the acknowledgement returned by the existing R10 feedback ledger.

Each row is append-only, sequence-checked, canonical JSON, flushed, and fsynced. A restart resumes from the last durable \`next_cursor\`. If a provider repeats page content under another cursor, that page remains in raw lineage but is excluded from metric merging so values are not double-counted.

## Rate limits and delayed evidence

A provider can raise \`ProviderRateLimited(retry_after_seconds)\`. Growth persists a \`not_before\` deadline before returning \`BackoffActive\`; retries before that deadline do not call the provider.

A provider snapshot may set \`complete=false\`. Such an event is still normalized and stored by R10, but R10's existing snapshot builder refuses to finalize from incomplete metrics. A later provider revision for the same exact window can add delayed metrics. Fields not supplied by a provider remain unavailable and therefore \`null\`; no adapter invents zero.

Provider revisions are monotonic per platform/account/post/window. A lower revision arriving after a higher durable revision fails closed. Different windows are independent, so late arrival of an older time window remains valid.

## Provenance

The normalized event preserves the existing R10 contract exactly:

- \`captured_at\` is the provider export capture time;
- \`window.start\` and \`window.end\` exactly match the requested/exported bounds;
- \`platform\`, \`account_id\`, \`post_id\`, and \`provenance.export_id\` bind the source identity;
- \`provenance.export_id\` is \`<provider_export_id>@revision-<n>\`;
- \`provenance.export_digest\` is SHA-256 over the ordered raw-page digest list plus provider export identity/revision;
- single-page raw lineage includes the exact SHA-256 of canonical provider JSON;
- the existing \`metrics_event_id\` and \`metrics_event_digest\` remain R10-owned.

No R10 contract fields or exactly-once semantics are changed.

## Lost acknowledgement

The provider ledger durably records the prepared R10 event before calling \`ReelsFeedbackLedger.ingest_platform_metrics\`. If the process loses acknowledgement after R10 accepted the event, restart replays the identical prepared event. R10 returns \`duplicate\`; Growth then durably records the sink acknowledgement. This preserves one logical metrics event under an unknown-result failure.

## Synthetic/live separation

Mock fixtures use \`evidence_origin=mock_fixture\` and must provide an exact fixture SHA-256. The normalized R10 event therefore has \`source_class=synthetic_fixture\` and \`live_performance_claim_allowed=false\`. A mock fixture cannot be promoted to live evidence by the ingestion layer.

A live provider client must instead declare \`evidence_origin=live_provider\` and must not carry fixture provenance. Live transport implementation and credential resolution remain external to the durable ledger; this repository stores only credential references and authorization lineage.

## Deterministic replay evidence

\`fixtures/provider_ingest_v1/replay_report.json\` pins the R11 restart/retry matrix. \`tests/test_provider_ingest_r11.py\` covers rate-limit restart, partial-page restart, repeated provider pages, stale revisions, out-of-order windows, lost acknowledgement, differing provider metric surfaces, explicit null/unavailable handling, raw export digest binding, credential-secret rejection, read-only mutation guards, and delayed metrics flowing through the unchanged R10 snapshot contract.

The conformance manifest in \`conformance/growth.provider_metrics_ingest.v1/manifest.json\` pins the R10 source commit, the existing R10 contract blob, and the exact Git blob identities of the R11 implementation, tests, documentation, fixtures, and replay report.
