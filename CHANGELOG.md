# Changelog

All notable changes to **table-stitcher** are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.1] — 2026-09-04

### Fixed

- **Numeric singleton rows were folded into the preceding row during
  multi-page stitching** (`merger.py`). Sparse numeric rows such as financial
  subtotals and fair-value-only lines are now preserved as independent rows;
  text-only wrapped-cell continuations still fold as before.
- **Headerless fragments could duplicate their first numeric row during
  Docling injection** (`adapters/docling.py`). When extraction demotes an
  upstream reader's erroneous `column_header` flag, injection now emits that
  row exactly once as ordinary data instead of preserving it as a header and
  repeating it in the body.

## [0.5.0] — 2026-08-23

### Fixed

- **Cell bounding boxes were dropped on multi-page merge**
  (`adapters/docling.py`). When N per-page fragments merged into one logical
  table, every `TableCell` in the injected result came back with `bbox=None`,
  breaking word-level grounding for multi-page tables. `_reemit_body_row`
  rebuilt each untouched body row's cells without copying the source cell's
  `bbox`; it is now carried across, so untouched body rows — the large
  majority on a clean multi-page table — keep their geometry. Header rows
  reused from the anchor already preserved theirs; rows the merger transformed
  (stitched continuations, folded overflow) genuinely have none and stay
  `bbox=None`. The FIFO alignment between repeated identical-text rows and
  their source occurrences is now a documented invariant: it is what guarantees
  a repeated row gets its own page's boxes, not a sibling's.

### Added

- **Row-level page association for restored geometry** (`models.py`,
  `adapters/docling.py`). A merged table's `prov` lists all N source pages, but
  pages share one coordinate space, so a restored `bbox` alone cannot say which
  page it belongs to. `LogicalTable.row_pages` now maps grid row index (header
  rows included) to the resolved `page_no` the row's cell boxes are valid on —
  resolved page numbers rather than prov indices, so the map survives
  downstream prov manipulation. A missing key means the row has no single
  source page (transformed rows, which carry no geometry). Recorded during
  injection, the only point where each row's source fragment is known; a
  consumer cannot reconstruct the mapping after the fact.
- **`TableStitcher.last_logical_tables`** exposes the `LogicalTable` results of
  the most recent `stitch()` call — previously discarded after injection,
  which would have left `row_pages` unreachable through the public API.
  Consumers needing the map should instantiate `TableStitcher` directly; the
  `stitch_tables()` convenience function does not expose the stitcher instance.

## [0.4.4] — 2026-08-13

### Fixed

- **Duplicate header labels crashed the multipage merge** (`merger.py`).
  Extracted headers that repeat a label — e.g. SEC 13F voting-authority
  triplets where TableFormer emits `COLUMN 8` three times — made
  `_build_generic_merged_table` raise `ValueError: Reindexing only valid
  with uniquely valued Index objects`, because `pd.concat` cannot align
  fragments on a non-unique column Index. Downstream consumers that
  fail-soft on the error silently kept a degraded table set for the whole
  document. Fragments are now merged under positionally deduped labels
  (collision-safe against pre-existing `X.1`-style names) and the original
  duplicated labels are restored on the merged output, matching how
  single-fragment tables pass through untouched.

## [0.4.3] — 2026-06-11

### Fixed

- **Reprinted continuation-page headers appended as data rows on multi-page
  merge** (`adapters/docling.py`). When a table's column header is reprinted at
  the top of each page — especially a multi-row (hierarchical) header — the
  repeated header rows survived the merge as bogus data rows, misaligning the
  stitched table. Injection now drops a body row when it is *both* flagged
  `column_header` by Docling *and* a tokenized match (Jaccard ≥ 0.6) for the
  reconstructed header block. Both signals are required: the flag alone is
  unreliable (Docling over-flags rowspan/continuation *data* rows as headers),
  and the tokenized comparison is punctuation-agnostic, so per-cell OCR drift
  such as `(S$)` vs `($$)` is tolerated without any threshold tuning. The merged
  DataFrame (`lt.df`) is unchanged; only the injected document is de-duplicated.
  A `debug` log reports each dropped row.

## [0.4.2] — 2026-06-08

### Fixed

- **`__version__` was hardcoded and stale** (`__init__.py`). It read `"0.2.0"`
  regardless of the installed release, since nothing tied it to the version in
  `pyproject.toml`. It is now derived from the installed distribution metadata
  via `importlib.metadata.version("table-stitcher")`, so it always reflects the
  actual release (falling back to `"0.0.0+unknown"` when run from an
  uninstalled source tree). The release gate now also asserts
  `__version__` matches the `pyproject.toml` version, so the two can't drift
  again.

## [0.4.1] — 2026-06-08

### Fixed

- **Spanning body cells duplicated across columns on multi-page merge**
  (`adapters/docling.py`). Docling repeats a `col_span=N` cell's text across
  every column it covers; the merge round-trip rebuilt those as `N` separate
  `col_span=1` cells, leaking a full-width description into every value column
  and displacing the real values (a repeated `col_span` header behaved the same
  way). Injection now matches each merged row back to its source grid row and
  re-emits the original spans; rows the merger transformed (stitched
  continuations, folded overflow) fall back to the flat 1x1 rebuild. The match
  uses the original span metadata, never value equality, so coincidentally-equal
  adjacent values (e.g. two plan columns sharing a cap) stay separate cells.

## [0.4.0] — 2026-05-29

### Added

- **Intervening-content guard** (`block_on_intervening_content`, default `True`).
  Two tables that share a column schema but belong to different sections — a
  heading sits between them in reading order — are no longer stitched into one.
  A genuine page-split continuation has nothing but page furniture between its
  fragments, so a section heading between them is a reliable "separate tables"
  signal. The docling adapter computes a per-table `TableMeta.content_before`
  signal; both merge paths (`_classify_sequential_pair` and
  `should_force_orphan_merge`) consult it.
  - Running headers mislabeled as headings (e.g. a journal banner labeled
    `page_header` on one page and `section_header` on another, or a repeated
    "Summary of benefits" banner above every page of a multi-page table) are
    detected as furniture via near-identical (Jaccard ≥ 0.8) recurrence across
    pages, so they do not block legitimate continuations.
  - Only `section_header`/`title` nodes block; plain paragraphs, list items,
    captions, footnotes and figures are deliberately ignored, since real PDFs
    routinely scatter those between fragments of a single continued table.
  - Fixes over-eager merging of same-schema per-section tables (e.g. an
    insurance policy's eight `Prestige | Elite | Classic` benefit grids being
    collapsed into one).

## [0.3.0] — 2026-05-06

### Fixed

- **Category rows incorrectly folded into preceding data rows** (`merger.py`).
  `stitch_split_cells()` previously folded any row with exactly one non-empty
  cell into the row above it. Category/section-header rows (e.g. "Theme 2:
  Trust and Credibility" with text only in col 0) matched this pattern and
  were silently merged into the preceding data row, mangling participant IDs
  and destroying table structure. The fix: a non-empty col 0 in the candidate
  row signals a new record or section header — not an overflow — and folding
  is skipped. Legitimate split-cell continuations always have col 0 empty.
  Six existing fixture YAMLs updated to reflect the corrected (higher) row
  counts — the old YAMLs encoded the buggy folded output.

- **False merge of independent same-width headerless tables** (`merger.py`).
  When two adjacent tables both have `is_headerless=True` and the same column
  count, the merger now requires a layout signal (the left table must end near
  the bottom of its page, `vert_bottom >= bottom_band_min`) before merging.
  Previously, column count alone was sufficient — three independent clinical
  lab panels (each 4 columns, no header row) collapsed into one 22-row table.
  Legitimate multi-page headerless tables are unaffected: they fill their pages
  and always produce a strong layout signal.

### Added

- Parser-neutral YAML fixture layer (`tests/fixtures/tablemeta/`) plus
  `tests/test_tablemeta_fixtures.py`. New adapters can validate against the
  merger's full test surface by feeding the same YAMLs through their own
  `extract()` — no PDF or OCR involvement.
- Public-API integration coverage: every fixture now runs through both
  `merge_multipage_tables()` (parser-neutral) and `stitch_tables()`
  (full pipeline including docling injection).
- `scripts/release_gate.sh` — offline-friendly release gate that runs unit
  tests, rebuilds `dist/`, installs the wheel into a clean venv, and
  smoke-tests the installed package. `RELEASE_GATE_ONLINE=1` toggles
  isolated build/install for CI.

### Changed

- **Core merger refactored** for readability. `merge_multipage_tables()` is
  now a four-phase orchestrator (`setup → pass 1 sequential → pass 2 orphan
  repair → build`) delegating to named helpers. `_classify_sequential_pair()`
  isolates adjacent-pair merge logic for independent review. Behavior is
  unchanged — 127 tests prove equivalence.
- `align_dataframe_to_header()` dispatches to per-policy handlers
  (`_overflow_preserve_extra`, `_overflow_warn_drop`, `_overflow_fail`,
  `_overflow_merge_tail`) instead of branching inline.

### Removed

- Dead `pos_to_orig` variable in the merger setup path.

## [0.2.0]

### Added

- **Multilingual tokenization** — `tokenize()` handles Latin, CJK
  (Chinese/Japanese/Korean), Thai, Lao, Khmer, Myanmar, Tibetan, Arabic,
  Hebrew, Cyrillic, Greek, Devanagari, and others. Uses `unicodedata`-based
  script detection (zero dependencies); scripts that use whitespace word
  separators are tokenized as words, separator-less scripts as per-character
  unigrams.
- **`MergeTrace`** on every `LogicalTable` — each merge decision is now
  auditable with page gap, width diff, header/row Jaccard, orphan flags,
  layout-continuation signal, and a human-readable reason code.
  `LogicalTable.warnings` collects all non-fatal issues raised during merge.
- **`width_overflow_policy`** config: four modes for handling wider
  continuation fragments — `preserve_extra` (default, lossless),
  `warn_drop`, `fail`, `merge_tail`. Previously silently truncated.
- **`headerless_width_tolerance`** config: width-drift tolerance for the
  headerless-continuation path (±2 by default) when vertical layout
  confirms the pages are adjacent-and-stacked.
- **Transactional rollback** in the Docling adapter's `inject()`: if an
  exception is raised mid-injection, the document's tables, prov,
  body.children, and group children are restored before the exception
  propagates.
- **Structural orphan detection** — `is_header_orphan` is now determined by
  cell-shape rules (short, non-data, not auto-label) rather than a
  hardcoded vocabulary of English "headerish" tokens. Generalizes across
  domains and languages.
- Taxonomy-based integration test suite (`tests/integration/fixtures/`)
  covering 10 merge-signal categories, 24 fixtures (corporate PDFs,
  PubTables-v2 slices, synthetic reportlab PDFs, and Japanese EDINET
  filings).
- GitHub Actions CI (`unit` + `integration` jobs, model caching).
- `CONTRIBUTING.md`, `tests/README.md`, `src/table_stitcher/adapters/README.md`.

### Changed

- **Spillover detection** now requires `page_gap == 1` (the immediately
  following page), independent of `max_page_gap`. Previously a 1-column
  fragment several pages later could falsely merge under large gap
  configurations.
- **`stitch_split_cells`** uses positional indexing throughout. Fixes a
  silent miss on merged DataFrames with duplicate column names, where
  label-based indexing returned sub-DataFrames and the "single non-empty
  cell" check failed.
- **Satellite table cleanup** in docling inject: merged-away satellites
  now have their `data` and `prov` cleared to empty shells. The `Table`
  wrapper remains at its list index (docling `self_ref` values are
  position-based; removing would break every subsequent reference).
- Pinned docling to `>=2.60,<3` and docling-core to `>=2.50,<3`.

### Removed

- `headerish_tokens` and `min_headerish_tokens` config fields. Replaced by
  the structural orphan detection described above — no vocabulary lookup.
  This is a breaking change for any caller passing these fields to
  `MultiPageConfig`.

## [0.1.0]

Initial release.

[Unreleased]: https://github.com/pebbleroad/table-stitcher/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/pebbleroad/table-stitcher/releases/tag/v0.5.1
[0.5.0]: https://github.com/pebbleroad/table-stitcher/releases/tag/v0.5.0
[0.4.0]: https://github.com/pebbleroad/table-stitcher/releases/tag/v0.4.0
[0.3.0]: https://github.com/pebbleroad/table-stitcher/releases/tag/v0.3.0
[0.2.0]: https://github.com/pebbleroad/table-stitcher/releases/tag/v0.2.0
[0.1.0]: https://github.com/pebbleroad/table-stitcher/releases/tag/v0.1.0
