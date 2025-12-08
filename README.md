# Docling Multipage Tables

A robust, heuristic-based post-processor for [Docling](https://github.com/DS4SD/docling) that detects and merges tables split across multiple pages in PDF documents.

## The Problem

PDF extraction libraries often treat a single logical table as multiple fragmented tables when it spans page boundaries. This leads to:

* **Data Orphans:** A table body continues on Page N+1 without headers.
* **Header Orphans:** A table header appears at the bottom of Page N, but data starts on Page N+1.
* **Spillover Content:** Long text (URLs, references) gets cut off at the page margin and appears as a separate 1-column "table" on the next page.
* **Split Cells:** Content from a single cell is fragmented across page breaks.

**Docling Table Enricher** fixes these issues by analyzing the document *after* conversion and modifying the `DoclingDocument` object in-place to unify these fragments.

---

## Installation

This is a local Python module. Ensure your project structure looks like this:

```text
/your_project/
├── docling_table_enricher/
│   ├── __init__.py
│   ├── merger.py
│   ├── injector.py
│   └── models.py
├── main.py
└── requirements.txt
```

**Dependencies:**

* `docling`
* `docling-core`
* `pandas`

---

## Usage

### Basic Usage

The enricher fits seamlessly into a standard Docling pipeline:

```python
from docling.document_converter import DocumentConverter
from docling_table_enricher import enrich_document

# 1. Standard Docling Conversion
converter = DocumentConverter()
result = converter.convert("report.pdf")
doc = result.document

# 2. Enrich Tables
doc = enrich_document(doc)

# 3. Export as usual
print(doc.export_to_markdown())
```

### Configuration

You can tune the merging behavior using `MultiPageConfig`:

```python
from docling_table_enricher import enrich_document, MultiPageConfig

config = MultiPageConfig(
    # Page adjacency: only merge tables on consecutive pages
    max_page_gap=1,
    
    # Width tolerance: how much column counts can differ
    max_width_difference=2,
    
    # Header similarity for "repeated header" detection
    header_sim_strict=0.6,
    
    # Row similarity fallback threshold
    row_sim_threshold=0.3,
    
    # Character used to join split content
    stitch_separator="\n",
    
    # Spillover detection: if False (default), any 1-column headerless
    # fragment is treated as spillover. If True, requires URL/ticket patterns.
    spillover_require_content_check=False,
)

doc = enrich_document(doc, config=config)
```

### Custom Header Tokens

For domain-specific documents, you can customize the tokens used to identify header rows:

```python
config = MultiPageConfig(
    headerish_tokens={
        "name", "date", "status", "amount", "description",
        "your", "custom", "domain", "terms"
    }
)
```

---

## How It Works

The library operates on three key principles:

### Principle 1: Sequential Merging

A headerless table fragment can only continue the **immediately preceding table** in document order. This prevents false merges between unrelated tables that happen to have the same column count.

```
Table A (page 2) → Table B (page 3, headerless) → Table C (page 4, headerless)
        └──────────────────┴────────────────────────────┘
                     Only adjacent pairs merge
```

### Principle 2: Width Matching

Same column count = same table structure. This is the primary signal for determining if two fragments belong together.

| Fragment A | Fragment B | Decision |
|------------|------------|----------|
| 5 columns  | 5 columns  | ✅ Likely same table |
| 5 columns  | 4 columns  | ⚠️ Check other signals |
| 5 columns  | 1 column   | 🔄 Spillover detection |

### Principle 3: Spillover Detection

When a multi-column table is followed by a 1-column headerless fragment, that fragment is almost certainly "spillover" — content that overflowed from the last cell. This content is stitched back into the appropriate cell.

```
Page 3:                          Page 4:
┌────┬────┬──────────────┐      ┌─────────────────────────┐
│Date│Vers│ Content      │      │ https://continued.url   │
├────┼────┼──────────────┤      └─────────────────────────┘
│1/1 │1.0 │ https://url1 │           ↓ Spillover
│1/2 │1.1 │ https://url2 │      Stitched into Content cell
└────┴────┴──────────────┘      of the 1/2 row
```

---

## Pipeline Stages

### Stage 1: Metadata Extraction

Extract structural information from each table fragment:
- Column count and headers
- Page location
- Header vs. data classification
- Orphan detection

### Stage 2: Merge Decision

Using Union-Find, group fragments that should merge based on:
1. Sequential adjacency (document order)
2. Width matching
3. Spillover detection
4. Header similarity (for repeated headers)

### Stage 3: Table Building

Reconstruct merged tables:
- Combine data rows
- Stitch spillover content into appropriate cells
- Clean up malformed headers

### Stage 4: Document Injection

Update the `DoclingDocument` in-place:
- Replace anchor table's data with merged content
- Merge provenance from all fragments
- Remove satellite table references from the document tree

---

## API Integration

The enricher works with Docling's JSON serialization:

```python
from docling_core.types.doc import DoclingDocument

# Receive JSON payload
doc = DoclingDocument.model_validate_json(json_payload)

# Enrich
doc = enrich_document(doc)

# Return JSON payload
return doc.model_dump_json()
```

---

## Configuration Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_page_gap` | int | 1 | Maximum pages between fragments |
| `max_width_difference` | int | 4 | Column count tolerance |
| `header_sim_strict` | float | 0.6 | Threshold for repeated header detection |
| `header_sim_loose` | float | 0.3 | Lower threshold when other signals are strong |
| `row_sim_threshold` | float | 0.3 | First-row similarity fallback |
| `headerish_tokens` | Set[str] | (see code) | Tokens indicating header content |
| `spillover_require_content_check` | bool | False | Require URL/ticket patterns for spillover |
| `stitch_separator` | str | "\n" | Join character for split content |
| `min_headerish_tokens` | int | 1 | Minimum header-like tokens required |
| `max_orphan_rows` | int | 2 | Max rows for header orphan classification |
| `max_data_orphan_rows` | int | 5 | Max rows for data orphan classification |
| `use_layout_hint` | bool | True | Use vertical position for merge decisions |
| `bottom_band_min` | float | 0.6 | Table A must end below this (0=top, 1=bottom) |
| `top_band_max` | float | 0.4 | Table B must start above this (0=top, 1=bottom) |

---

## Project Structure

```
docling_table_enricher/
├── __init__.py      # Public API: enrich_document(), MultiPageConfig
├── models.py        # Data classes: MultiPageConfig, TableMeta, LogicalTable
├── merger.py        # Core merge logic: detection, grouping, building
└── injector.py      # DoclingDocument surgery: content replacement, tree pruning
```

---

## Integration Notes

### Error Handling

```python
from docling_table_enricher import enrich_document, EnrichmentError

# Default: fails gracefully, logs error, returns original doc
doc = enrich_document(doc)

# Strict mode: raises EnrichmentError on failure
try:
    doc = enrich_document(doc, raise_on_error=True)
except EnrichmentError as e:
    handle_error(e)
```

### Logging

The module logs to `docling_table_enricher`. Your logging config will capture it automatically:

```python
import logging
logging.getLogger("docling_table_enricher").setLevel(logging.INFO)
```

### Version

```python
from docling_table_enricher import __version__
print(__version__)  # "0.1.0"
```

---

## Limitations

- **Complex header structures:** Tables with multi-row headers or merged cells may not be detected correctly (upstream Docling limitation).
- **Non-tabular spillover:** Very large text blocks that span pages may not be handled if they exceed typical spillover patterns.
- **Page size estimation:** For geometry hints, we assume A4 page height (842 points) for normalization. This works for most documents but may be slightly off for non-standard page sizes.

---
