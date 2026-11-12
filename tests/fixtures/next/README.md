# Reduced Next.js fixtures

Source: public `https://www.humblebundle.com/books` and
`https://www.humblebundle.com/books/data-science-oreilly-books`, captured
2026-11-12 using the same HTTP client as the spider.

These are **reduced structural fixtures**, not full page captures:

* `books.html` retains one real `feedItems` entry and its source keys.
* `bundle.html` retains the real bundle object, its 25 item records, cumulative
  tier references and the React element path containing it. UI chrome was removed.
* Long marketing/book descriptions were replaced with short **synthetic** text,
  including non-ASCII characters. Flight `T` lengths were recomputed in UTF-8
  bytes. Item titles, identities, money, dates, and source field shapes remain.
* Each stream is split across two channel-1 scripts, deliberately mid-record.
  Resource hints and control records exercise metadata skipping.

Tests are offline. To investigate another redesign, retain a private full capture
outside the repository and reduce it in the same way; do not substitute a list of
links or infer identities/dates from rendered countdown placeholders.
