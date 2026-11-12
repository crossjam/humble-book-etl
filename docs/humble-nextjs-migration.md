# Humble source adapter migration (2026-11-12)

For the incident, rationale and verification results in two minutes, start with
[the developer brief](humble-site-transition.md).

Humble's public books listing and bundle pages now embed Next.js Flight model
data instead of the old `landingPage-json-data` / `webpack-bundle-page-data`
JSON scripts. Changing a selector alone is insufficient.

## Source contract

`spider/scrapers/next_page.py` is a deliberately limited, versioned adapter:

* Extract JSON arguments to `self.__next_f.push`, without executing JavaScript.
* Join channel-1 frames before parsing. Record boundaries need not match scripts.
* Decode JSON model rows and UTF-8 **byte-counted** `T` text rows. Skip known
  framework hints/imports/control metadata; reject unknown record framing.
* Resolve data references, including cumulative-tier paths through serialized
  React `props`, with checks for missing references and cycles.
* Discover `feedItems`, select book bundles, deduplicate repeated feeds and
  reject conflicting identities. Never scrape the rendered `00:00:00:00`
  countdown or derive identifiers from URL slugs.

The Flight format is undocumented application data, not a supported Humble API.
Keep its parsing isolated here; a future redesign should fail closed, not
silently produce an empty successful catalog. There is no headless-browser
dependency, browser automation, credential, or new Humble API dependency.

| Current source | Existing catalog field |
| --- | --- |
| Listing `productKey` | `machine_name` (stable across the migration) |
| Listing `url`, `title`, `category` | `product_url`, `tile_name`, `category` |
| Listing `thumbnail`, `titleImageUrl` | `tile_image`, `tile_logo` |
| Detail `startDate`, `endDate` | Required UTC bundle schedule |
| Detail `tiers[].sku` (actual `id` fallback) | Tier identifier |
| Detail `tiers[].bundleItems[].key` | Stable book `machine_name` |
| Detail `developers`, `publishers`, `platforms` | Authors, publishers, formats |
| Money `amount` | Major units, **not** divided by `fractionDigits` |
| Money `centAmount` | Divide by `10 ** fractionDigits` |

The detail adapter matches the requested slug, not unrelated recommendations.
Books are deduplicated across cumulative tiers while retaining every membership.
Missing optional metadata stays unknown rather than being invented. No fake
start date is substituted for the listing's missing date.

Legacy listing and detail adapters remain available. A Next listing requires
successful detail parsing for **every** bundle; any failure aborts the run.
Invalid schedules, missing identities, and Pydantic failures also abort instead
of discarding rows while announcing success.

## Persistence and discovery safeguards

Both CLI ETL and authenticated `POST /etl/run` validate scrape quality **before**
snapshot, archive, or upsert writes. Empty results, duplicate/blank identities,
or a loss of **more than 50%** against the larger of:

1. The latest recognizable nonempty listing snapshot, and
2. The currently dated, active, unarchived catalog

stop the operation (CLI failure / API HTTP 502). Malformed or empty stored
snapshots do not reset that baseline. Large legitimate catalog changes therefore
need operator investigation; there is deliberately no automatic retry that
weakens the guard. Capturing a failed page for diagnosis does not make it a
successful stored snapshot. Bundles absent from the feed are not deleted.

For existing bundles, omitted/null optional values do not overwrite known data;
explicit `[]`, `False`, and `0` remain authoritative. Book-list replacement keeps
incoming membership/order, filling missing/null metadata from the same retained
book key without restoring removed books.

Default API and MCP discovery additionally require stored active + unarchived +
known dates with `start <= now < end`, independent of ETL freshness. Expired
stale-active rows appear in bundle history; opt-in inactive queries and known-ID
lookups retain access without rewriting the stored flags during reads.

New snapshots use `json_version=humble-next-flight-v1` and contain `format`,
the original joined `flight` stream, and extracted `products`. Products are
enriched with the actual detail dates used by persistence; lifecycle replay
understands these fields as well as legacy snapshots. Legacy versions are tagged
`humble-landing-v1`. Existing unversioned snapshots remain readable.

## Validation and rollout

Run offline tests from the repository root:

```sh
.venv/bin/python -m pytest tests -q -p no:cacheprovider
```

`tests/fixtures/next/README.md` documents the reduced public-page captures.
Tests cover split frames, Unicode byte lengths, reference paths/cycles, malformed
pages, both source formats, missing details, pricing, catalog quality,
retained enrichment, lifecycle replay and read-time expiration.

Before deployment, back up the SQLite database, then do a live **read-only**
fetch with `HumbleSpider.fetch_bundles()` and inspect counts, IDs, dates, tiers
and book details. Do not invoke `run_spider` or `/etl/run` for a dry run: those
write the catalog. Compare identities against the existing database and run
`validate_scrape_quality` using a read-only connection or a disposable copy.
Review the results before rebuilding/restarting production and triggering ETL.
This migration needs no schema changes and no frontend changes.
