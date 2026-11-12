# Humble's November 2026 site transition: developer brief

**Read this first; implementation details are in
[the adapter migration reference](humble-nextjs-migration.md).**

## The 60-second version

Humble moved the public books listing **and bundle detail pages** from named
JSON script blocks to a Next.js application with embedded Flight data. Our
scraper still expected the old scripts, so ETL stopped. An already-expired
Robotics bundle remained visible because discovery trusted its stored active
flag.

We adapted the **source boundary**, not the database or public routes: decode
the embedded data without running JavaScript, map it to the existing models,
and retain the legacy parsers. We also added pre-write scrape checks and
clock-based API/MCP availability checks so another upstream change is less
likely to damage retained metadata or display expired bundles.

**Status at verification, November 12, 2026:** implementation is on PR #4;
production has not been deployed or modified.

## What we actually observed

- **November 11, 05:00 UTC:** last successful scheduled snapshot before the
  failure.
- **November 12, 05:00 UTC:** scheduled ETL failed because
  `script#landingPage-json-data` was missing. It stopped before expiry cleanup.
- Direct requests returned HTTP 200 and Next.js pages, not just an error or
  bot-challenge page. The bundle detail script had changed too.
- We confirmed the new source shape and later rechecked all 20 bundle pages
  individually, with a five-second minimum wait plus random jitter.

These observations bound the detection window; **we do not know Humble's exact
deployment time**.

| Before | Observed after | Our response |
| --- | --- | --- |
| Listing `landingPage-json-data` → `data.books.mosaic[].products` | Flight models containing `feedItems` | Versioned listing adapter |
| Detail `webpack-bundle-page-data` → `bundleData` | Matching bundle model with dates, tiers and item records | New detail adapter, same output model |
| Legacy money/identity fields | `productKey`, item `key`, `amount` / `centAmount` | Preserve source IDs; normalize units explicitly |

## Why this approach

- **A selector change was not enough.** Both the container format and field
  names changed, and some listing information moved to the detail model.
- **No headless browser is needed.** The required data is already in the HTTP
  response. We parse JSON frames and references, never execute page JavaScript.
- **Rendered countdowns are not a source of truth.** Initial HTML can show
  `00:00:00:00`; we use actual `startDate` / `endDate` values.
- **Keep existing identities and consumers.** Bundle `productKey` and book
  `key` map to existing `machine_name` fields. Slugs are not invented IDs.
  Database schema and API/MCP routes stay unchanged.
- **Isolate the unstable part.** Flight is undocumented application data, not
  a supported Humble API. Size/depth/expansion budgets and offline fixtures
  make the adapter bounded, testable, and replaceable.

## Guardrails that matter

1. A Next-format run needs valid details for every bundle. Invalid identities,
   schedules, prices, or records stop the scrape instead of silently dropping
   rows and announcing success. Only the observed bootstrap (`0`) and text (`1`)
   Flight channels are accepted; unknown channels fail even if channel `1`
   still contains a plausible partial catalog.
2. Both ETL entry points reject empty/duplicate results or a catalog count loss
   **greater than 50%** against the trusted snapshot/current-catalog baseline,
   before snapshot/archive/upsert writes.
3. Unknown/null metadata does not erase existing enrichment. Explicit empty
   lists remain meaningful; removed books are not restored by metadata merging.
   Malformed members in nonempty author/publisher/format lists fail the scrape,
   rather than being filtered into empty lists that could erase saved values.
4. API and MCP discovery require active, unarchived records with known dates
   satisfying **`start <= UTC now < end`**, independently of scrape freshness.
   Retained records remain accessible through opt-in/known-ID paths.

Absence from a listing alone does **not** delete or archive a bundle. A retained,
nonexpired bundle can therefore make the database catalog count differ from a
fresh website listing.

## Evidence, not just unit tests

| Check on November 12, 2026 | Result |
| --- | --- |
| Live read-only scrape | 20 bundles, 642 book entries; all 20 bundle IDs already known |
| Independent direct-page expiration checks | 20/20 exact timestamps matched |
| Replay on a disposable database copy | All 66 rows and existing IDs preserved; expired Robotics archived |
| Offline regression suite, locally and with production runtime dependencies | 301 tests passed, including real-HTTP API/MCP and channel/metadata ETL regressions |
| Updated Docker image, real HTTP, direct and nginx proxy | 106 checks passed; all five MCP tools and four resource reads worked |
| Full retained-catalog detail sweep, direct and proxied | All 66 bundle responses serialized 2,166 book entries consistently across API/MCP |
| API/MCP consistency on that copy | Same 21 active / 66 retained bundles; expected retained-catalog behavior |

HTTP checks also exercised API health, detail/featured/history/snapshot routes,
login, authenticated raw HTML, unauthorized requests, missing records and the
relative MCP slash redirect. These used isolated services and synthetic login
credentials, not a production deployment.

## Where to look / what to do next

- `spider/scrapers/next_page.py`: Flight framing, references and listing adapter.
- `spider/scrapers/bundle_detail_scraper.py`: detail mapping and cumulative tiers.
- `spider/core/spider.py`: format detection, enrichment and validation.
- `spider/database/persistence.py`: quality gate, metadata retention and replay.
- `hbetl_mcp/availability.py`: shared read-time availability rule.
- `tests/fixtures/next/`: reduced captures with provenance.
- `tests/test_service_http.py`: offline real-HTTP API/MCP regression tests.

Before rollout: **back up the database**, run tests and a read-only source
check, deploy the reviewed image, then explicitly run write-enabled ETL and
check discovery/history. No schema or frontend migration is required.
`python -m spider.cli.run_spider` and `POST /etl/run` are **not dry runs**.

On the next source change, capture a failing response, update the isolated
adapter and fixtures, and investigate quality-gate failures rather than
bypassing them. Existing per-record database commits were not redesigned:
source/quality failures happen before writes, but a later database failure can
still leave a partially applied run.
