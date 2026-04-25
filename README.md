# Table Stitcher

[![CI](https://github.com/pebbleroad/table-stitcher/actions/workflows/ci.yml/badge.svg)](https://github.com/pebbleroad/table-stitcher/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/table-stitcher.svg)](https://pypi.org/project/table-stitcher/)
[![Python](https://img.shields.io/pypi/pyversions/table-stitcher.svg)](https://pypi.org/project/table-stitcher/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Reassemble tables split across page boundaries in PDF extraction.

PDF extraction tools often fragment a single logical table into multiple pieces when it spans pages. **Table Stitcher** detects these fragments and merges them back into coherent tables.

**Parser-agnostic core** with a clean adapter interface. Ships with a [Docling](https://github.com/DS4SD/docling) adapter out of the box.

---

## What It Fixes

- **Data orphans** -- table body continues on the next page without headers
- **Header orphans** -- headers at the bottom of one page, data on the next
- **Spillover content** -- URLs or long text cut at page margins, appearing as separate 1-column "tables"
- **Split cells** -- cell content fragmented across page breaks
- **Width drift** -- same table extracted with slightly different column counts across pages
- **Multilingual headers** -- merge rules work on Latin, CJK, Thai, Arabic, Cyrillic, and more — no language model or dictionary required, purely structural signals

## How It Fits in Your Pipeline

Table-stitcher is **parser-agnostic at the table-fragment level** — it doesn't parse PDFs, HTML, or anything else. It assumes your upstream pipeline already extracted tables and knows which page each came from.

```
your parser          adapter.extract()      merger          adapter.inject()     your format
(Docling, VLM,  ──>  List[TableMeta]   ──>  List[Logical ──>  write merged   ──>  (DoclingDocument,
 Camelot, HTML…)                            Table]            results back         HTML, JSON…)
```

The core engine only ever speaks `TableMeta` — a small dataclass carrying a DataFrame, page number, column count, header tokens, and optional bbox. It returns `LogicalTable` objects with merged data plus `MergeTrace` explanations for the decisions it made. Your job is a thin adapter with two methods:

- `extract(doc, cfg) -> List[TableMeta]` — translate your parser's native table objects into `TableMeta`
- `inject(doc, logical_tables) -> doc` — write merged results back into your native format

Ships with a `DoclingAdapter` out of the box. Writing an `HTMLAdapter`, `CamelotAdapter`, or one for your own pipeline is ~50 lines — see [Writing a Custom Adapter](#writing-a-custom-adapter).

## Installation

**From PyPI** (once published):
```bash
pip install table-stitcher[docling]    # With Docling support
pip install table-stitcher             # Core only (for custom adapters)
```

**From source:**
```bash
git clone https://github.com/pebbleroad/table-stitcher.git
cd table-stitcher
pip install -e ".[docling]"            # Editable install with Docling
```

## Quick Start

### Docling (one-liner)

```python
from docling.document_converter import DocumentConverter
from table_stitcher import stitch_tables

converter = DocumentConverter()
doc = converter.convert("report.pdf").document
doc = stitch_tables(doc)                  # merged tables; ready for
                                          # export_to_markdown() / HTML / LLM
```

`stitch_tables()` mutates `doc` in place and returns the same object. If you
need the pre-stitch original (e.g. for diffing), snapshot first:

```python
original = doc.model_copy(deep=True)
doc = stitch_tables(doc)
```

Tables that aren't merged pass through byte-for-byte — multi-row headers,
rowspan/colspan, cell bboxes, and prov entries are preserved exactly as
Docling produced them. Only merged tables get their data rows rebuilt from
the merged DataFrame; anchor headers are reused verbatim. See
[Adapter Design Principle: Respect the Incoming Structure](#adapter-design-principle-respect-the-incoming-structure).

Runnable end-to-end scripts live in [`examples/`](examples/):

- [`basic_pipeline.py`](examples/basic_pipeline.py) — minimal Docling → stitch → markdown export
- [`system_controller.py`](examples/system_controller.py) — drop-in integration for a larger pipeline

### With Configuration

```python
from table_stitcher import stitch_tables, MultiPageConfig

config = MultiPageConfig(
    max_page_gap=1,              # Only merge tables on consecutive pages
    max_width_difference=2,      # Column count tolerance
    header_sim_strict=0.6,       # Threshold for repeated header detection
    stitch_separator="\n",       # Join character for split content
)

doc = stitch_tables(doc, config=config)
```

### Custom Parser (adapter pattern)

```python
from typing import Any, List
from table_stitcher import TableStitcher, MultiPageConfig, TableMeta, LogicalTable
from table_stitcher.adapters.base import TableStitcherAdapter

class MyParserAdapter:
    def extract(self, doc, cfg: MultiPageConfig) -> List[TableMeta]:
        """Read tables from your document format into TableMeta objects."""
        ...

    def inject(self, doc, logical_tables: List[LogicalTable]):
        """Write merged results back into your document format."""
        ...

stitcher = TableStitcher(adapter=MyParserAdapter())
doc = stitcher.stitch(doc)
```

## How It Works

The merge engine uses three principles:

### 1. Sequential Merging

A headerless fragment only merges with its immediate predecessor in document order. This prevents false merges between unrelated tables that happen to share column counts.

### 2. Width Matching

Same column count = same table structure. This is the primary merge signal.

| Fragment A | Fragment B | Decision |
|---|---|---|
| 5 columns | 5 columns | Likely same table |
| 5 columns | 4 columns | Check other signals |
| 5 columns | 1 column | Spillover detection |

When a continuation fragment is wider than the anchor, the default policy is
data-preserving: extra trailing cells are kept in explicit `_extra_N` columns.
Use `width_overflow_policy="warn_drop"` for the older lossy behavior,
`"fail"` when you want strict no-overflow enforcement, or `"merge_tail"` when
overflow cells should be appended into the final canonical column.

### 3. Spillover Detection

A 1-column headerless fragment following a multi-column table is almost certainly content that overflowed from the last cell. It gets stitched back automatically.

## Architecture

```
table_stitcher/
  __init__.py         # Public API: stitch_tables(), extract_table_meta(), TableStitcher
  models.py           # MultiPageConfig, TableMeta, LogicalTable
  merger.py           # Core engine (parser-agnostic)
  adapters/
    base.py           # TableStitcherAdapter protocol
    docling.py        # Docling implementation
```

The adapter protocol has exactly **two methods**:

| Method | Purpose |
|---|---|
| `extract(doc, cfg)` | Read table fragments from your document -> `List[TableMeta]` |
| `inject(doc, logical_tables)` | Write merged results back into your document |

The merge engine (`merger.py`) never sees parser-native objects. It works entirely with `TableMeta` (pandas DataFrames + page metadata), and each `LogicalTable` includes `merge_reason`, `merge_traces`, and `warnings` so downstream integrations can audit why fragments merged and whether any risky alignment happened.

### Adapter Design Principle: Respect the Incoming Structure

> **Adapters must preserve the native structure of tables they don't modify, and preserve as much native structure as possible for tables they do modify.**

`TableMeta` is intentionally lossy — it reduces a rich table (with rowspan, colspan, multi-row headers, cell styles, bboxes) into a pandas DataFrame plus metadata, because the merger only needs that much to make merge decisions.

When `inject()` writes results back, the temptation is to rebuild the native structure from the DataFrame alone. **Don't.** That throws away everything `TableMeta` didn't capture.

Two rules for `inject()`:

1. **Pass-through unchanged.** If a logical table has only one member (nothing merged), leave the original native table object untouched. Do not round-trip it through the DataFrame.
2. **Partial reuse on merge.** For merged tables, reuse the anchor's native structure where possible (e.g. header rows with their spans) and only rebuild the parts the merger actually changed (the data rows, formed by concatenation).

The Docling adapter illustrates this: `_dataframe_to_docling_data()` reuses the anchor's original header rows verbatim (preserving rowspan/colspan) and only builds fresh 1x1 cells for the merged data rows. An earlier version rebuilt the entire grid from the DataFrame and destroyed multi-row headers — that was a bug, not a limitation of the architecture.

## Configuration Reference

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_page_gap` | int | 1 | Maximum pages between fragments |
| `require_same_width` | bool | False | Require identical column counts |
| `max_width_difference` | int | 4 | Column count tolerance |
| `width_overflow_policy` | str | "preserve_extra" | How to handle continuation fragments wider than the anchor: "preserve_extra", "warn_drop", "fail", or "merge_tail" |
| `headerless_width_tolerance` | int | 2 | Width-drift tolerance for headerless pairs when layout confirms continuation |
| `header_sim_strict` | float | 0.6 | Header similarity threshold |
| `header_sim_loose` | float | 0.3 | Lower threshold (with layout confirmation) |
| `row_sim_threshold` | float | 0.3 | First-row similarity fallback |
| `use_layout_hint` | bool | True | Use vertical position signals |
| `bottom_band_min` | float | 0.6 | Table A must end below this (0=top, 1=bottom) |
| `top_band_max` | float | 0.4 | Table B must start above this |
| `spillover_require_content_check` | bool | False | Require URL/ticket patterns for spillover |
| `stitch_separator` | str | "\n" | Join character for split content |
| `max_orphan_rows` | int | 2 | Max rows for header orphan classification |
| `max_data_orphan_rows` | int | 5 | Max rows for data orphan classification |

## Writing a Custom Adapter

For the adapter protocol in detail and notes on the Docling adapter's
version compatibility and known workarounds, see
[`src/table_stitcher/adapters/README.md`](src/table_stitcher/adapters/README.md).

To integrate a new parser, implement two methods. Here's a working skeleton:

```python
from typing import Any, List
import pandas as pd
from table_stitcher import TableStitcher, MultiPageConfig, TableMeta, LogicalTable
from table_stitcher.adapters.base import TableStitcherAdapter
from table_stitcher.merger import tokenize, normalize_col_name, is_numeric_like_colnames, first_row_has_number

class MyParserAdapter:
    def extract(self, doc: Any, cfg: MultiPageConfig) -> List[TableMeta]:
        tables_meta = []
        for idx, table in enumerate(doc.tables):
            # 1. Convert your table to a DataFrame
            #    - First row as header if it looks like headers
            #    - Set df.attrs['is_headerless'] = True if no real headers
            df = pd.DataFrame(table.rows, columns=table.headers)

            # 2. Get page info
            pages = [table.page_number]
            start_page = pages[0]

            # 3. Tokenize headers for similarity matching
            header_tokens = set()
            for col in df.columns:
                header_tokens |= tokenize(normalize_col_name(col))

            # 4. Tokenize first row (fallback similarity signal)
            first_row_tokens = set()
            if df.shape[0] > 0:
                first_row_tokens = tokenize(
                    " ".join(str(x) for x in df.iloc[0].tolist())
                )

            # 5. Classify: is_headerless, is_header_orphan, is_data_orphan
            raw_columns = [str(c) for c in df.columns]
            is_headerless = df.attrs.get('is_headerless', False)

            tables_meta.append(TableMeta(
                idx=idx,
                df=df,
                start_page=start_page,
                pages=pages,
                width=df.shape[1],
                header_tokens=header_tokens,
                first_row_tokens=first_row_tokens,
                raw_columns=raw_columns,
                vert_center=None,       # Set if bbox available
                vert_top=None,          # Normalized 0-1, 0=top of page
                vert_bottom=None,       # Normalized 0-1, 1=bottom of page
                is_header_orphan=False, # True if headers-only, no/few data rows
                is_data_orphan=False,   # True if data-only, no real headers
                numeric_like_cols=is_numeric_like_colnames(raw_columns),
                row_count=df.shape[0],
                is_headerless=is_headerless,
            ))
        return tables_meta

    def inject(self, doc: Any, logical_tables: List[LogicalTable]) -> Any:
        for lt in logical_tables:
            if len(lt.members) <= 1:
                continue  # Nothing merged, skip

            anchor_idx = lt.members[0]
            # Replace the anchor table's data with lt.df
            doc.tables[anchor_idx].data = lt.df
            doc.tables[anchor_idx].pages = lt.pages

            # Mark or remove satellite tables
            for sat_idx in lt.members[1:]:
                doc.tables[sat_idx].merged_into = anchor_idx

        return doc

# Use it:
stitcher = TableStitcher(adapter=MyParserAdapter())
doc = stitcher.stitch(doc)
```

### Key `TableMeta` fields the merger relies on

| Field | What the merger uses it for |
|---|---|
| `idx` | Original table index in `doc.tables` — used for result mapping |
| `df` | The table content as a DataFrame — used for row stitching |
| `start_page`, `pages` | Page adjacency checks — must be populated |
| `width` | Column count matching — primary merge signal |
| `header_tokens` | Jaccard similarity for repeated-header detection |
| `is_headerless` | If `True`, table is a continuation candidate |
| `is_header_orphan` | If `True`, eligible for orphan+data merge |
| `is_data_orphan` | If `True`, eligible for header+orphan merge |
| `vert_top`, `vert_bottom` | Layout hints (0-1 normalized) — optional, set to `None` if unavailable |

## Pass-Through Guarantee

Table-stitcher follows a **no-data-loss** principle:

- If extraction fails for a table, the **original table is preserved unchanged** in the document. It is not removed or modified.
- If the entire stitching pipeline fails, the **original document is returned as-is**.
- Tables that don't match any merge criteria pass through untouched.
- Skipped tables are logged with a count (e.g., `"Extracted 5/7 tables (2 skipped — originals preserved)"`).

This means you can safely call `stitch_tables()` on any document — the worst case is that nothing changes, never that data is lost.

## Error Handling

```python
from table_stitcher import stitch_tables, StitchingError

# Default: fails gracefully, returns original doc
doc = stitch_tables(doc)

# Strict: raises on failure
try:
    doc = stitch_tables(doc, raise_on_error=True)
except StitchingError as e:
    handle_error(e)
```

## Logging

```python
import logging
logging.getLogger("table_stitcher").setLevel(logging.INFO)
```

## Testing and Contributing

- [`tests/README.md`](tests/README.md) — test layout, running instructions, timings, and what the integration harness actually asserts
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, fixture workflow, naming convention, how to regenerate an `expected.yaml` after a merger change
- [`src/table_stitcher/adapters/README.md`](src/table_stitcher/adapters/README.md) — adapter protocol, the Docling adapter's version compatibility and known workarounds, how to write a new adapter

The library ships with a taxonomy-based integration suite: every merge rule
has at least one fixture exercising it, and every category that surfaced
a real bug has a fixture pinning the fix.

## License

MIT
