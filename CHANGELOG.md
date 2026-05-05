# Changelog

All notable changes to **table-stitcher** are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/pebbleroad/table-stitcher/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/pebbleroad/table-stitcher/releases/tag/v0.2.0
[0.1.0]: https://github.com/pebbleroad/table-stitcher/releases/tag/v0.1.0
