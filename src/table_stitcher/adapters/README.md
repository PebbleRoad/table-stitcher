# Adapters

`table-stitcher`'s core is parser-agnostic. Adapters bridge a specific
document parser (docling, pdfplumber, unstructured, …) to the
`TableMeta` / `LogicalTable` contract the merger operates on.

Adapter-specific notes — version compatibility, OCR backend behavior,
known upstream workarounds — live here rather than in the main README,
so the top-level docs stay agnostic and each adapter can document its
own quirks.

## Adapter protocol

Any adapter implements two methods:

```python
class MyAdapter:
    def extract(self, doc, cfg: MultiPageConfig) -> list[TableMeta]:
        """Read tables from the parser's native document type and produce
        TableMeta records for each fragment."""

    def inject(self, doc, logical_tables: list[LogicalTable]):
        """Write merged tables back into the native document, pruning the
        now-redundant satellite fragments from the body tree and clearing
        their cell content. Return the modified document."""
```

A skeleton custom adapter appears in the top-level README. The merger
never imports parser-specific types — it only reads from the `TableMeta`
fields and writes to a pandas DataFrame, which the adapter then converts
back into the parser's native table representation during `inject`.
Each `LogicalTable` also carries `merge_reason`, `merge_traces`, and
`warnings`; adapters can ignore them, log them, or surface them in their
native document metadata.

---

## Docling adapter (`docling.py`)

### How the docling adapter prunes satellites

When `inject()` folds multiple fragments into one logical table, the
satellite fragments (members after the anchor) are handled in two places:

1. **Body tree** — references to satellite tables in `doc.body` (and any
   groups) are removed, so rendered output contains only the merged
   anchor.
2. **`doc.tables` list** — the `Table` objects at the satellite indices
   are *cleared in place*: `data` becomes an empty `TableData`
   (`num_rows=0, num_cols=0`), `prov` becomes `[]`. The `Table`
   wrapper stays at its list position because docling uses
   position-based `self_ref` strings (`#/tables/N`) — removing entries
   would invalidate every reference that points to a later index.

**If your downstream code iterates `doc.tables` directly** (instead of
traversing the body tree), skip empty-shell tables explicitly:

```python
for t in doc.tables:
    if t.data and t.data.num_rows > 0:
        ...  # real content
```

For most users the body tree is the right thing to iterate — it already
reflects the merged view.

### Version compatibility

Tested against **docling 2.64, docling-core 2.54**. The project pins a
compatible range in `pyproject.toml`:

```
docling>=2.60,<3
docling-core>=2.50,<3
```

If you need to test against a dev docling build:

```bash
pip install -e <path/to/docling-checkout>
pytest tests/integration/
```

Breaking changes in a 3.x release will need adapter updates — the adapter
touches `DoclingDocument`, `TableData`, `TableCell`, and table `prov`
entries (for page-number and bounding-box info).

### OCR backend

Docling auto-selects an OCR engine at runtime based on the host
(`ocrmac` on Apple Silicon, `easyocr` / `tesserocr` / `rapidocr`
otherwise). Cell text on image-backed PDFs (e.g. our bundled
PubTables-v2 fixtures) differs very slightly across backends.

The `first_row` / `last_row` assertions in integration fixtures have
been stable in practice but may flap if the CI host's OCR backend
differs from the one used to author the YAML. Re-running
`tests/integration/_tools/regenerate_expected.py` produces a clean
baseline for the new backend.

### Adapter detection thresholds

A few structural constants live at the top of `docling.py` rather than
in `MultiPageConfig`:

```python
_MAX_HEADER_CELL_LEN = 30    # header cells typically short; data cells longer
_DATA_PATTERNS            # regex list for "this cell is data, not header"
_AUTO_COLNAME_RE          # "Column_N" / "Unnamed: N" parser placeholders
```

These are **adapter-intrinsic** — tuning them changes how the adapter
classifies first rows as header-or-data. User-tunable thresholds
(page gap, width tolerance, Jaccard cutoffs) live in `MultiPageConfig`
instead.

### Known upstream workarounds

The adapter compensates for a handful of docling extraction patterns
that produce fragments the merger would otherwise mis-group:

- **Data-as-headers** — when docling extracts a page where the real
  header row got collapsed into the first data row, the fragment's
  "column names" look like `['Column_0', 'Am Fds Trgt Dte Rtm 2055',
  '13,085.03']`. The adapter's `_looks_like_data` regex list catches
  comma-grouped decimals, stat ranges (`280 (176, 404)`), and scientific
  notation (`7.0 x 10-7`) as data patterns, and flags the fragment
  `is_headerless=True` so the merger's width-match path handles it.
  An upstream fix that correctly identifies the collapsed header row
  would make this heuristic unnecessary.

- **Long-cell first rows** — fragments where the majority of first-row
  cells are >30 chars are flagged headerless too. Real headers are
  typically short; a row of sentence-long strings is almost certainly
  data.

- **Orphan-header fragments with truncated width** — when a new table's
  header row has empty trailing cells that the parser drops, the
  fragment's width is less than its data continuation's. The adapter
  flags these as `is_header_orphan` structurally (small + header-shaped
  cells, no data patterns), and the merger's "header-orphan → headerless
  data" path trusts the data fragment's width on the join.

These workarounds are structural, not vocabulary-based — they reason
about cell shapes (length, regex patterns, auto-label form) rather than
specific words, so they generalize across domains and languages.

### Layout data availability

The merger's layout-confirmation rules (`bottom_band_min`, `top_band_max`,
width-drift tolerance) rely on `vert_top` / `vert_bottom` from the
adapter. Docling provides these via `prov[*].bbox` on `DoclingDocument`
tables. If a document lacks layout info (e.g. a text-only extraction),
the merger falls back to structural signals only and the layout-gated
rules don't fire.

---

## Adding a new adapter

1. Create `src/table_stitcher/adapters/<parser>.py`.
2. Implement the two methods above. Read a fragment's pandas DataFrame
   plus its `prov` / layout metadata into `TableMeta`; write a merged
   DataFrame back into the parser's native table type during `inject`.
3. Add a section to this README documenting version compat, any OCR or
   extraction quirks, and workarounds.
4. Add a unit test file `tests/test_<parser>_adapter.py` exercising
   `_grid_to_dataframe`-equivalent and `_dataframe_to_*_data`-equivalent
   conversion on stub inputs.
5. (Optional but valuable) Re-run the integration fixtures through the
   new adapter. Fixtures are parser-agnostic; anything your adapter can
   convert to `TableMeta` with reasonable fidelity will exercise the
   same merger rules as docling.
