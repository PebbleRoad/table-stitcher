"""
Adapter-level tests for the intervening-content guard.

These build real ``DoclingDocument`` objects (via the docling-core builder API)
so they exercise the *producer* side — ``_detect_running_furniture`` and
``_compute_content_before`` in the docling adapter — not just the merger's
consumption of ``content_before``.

Intent (why these matter):
- Two tables that share a column schema but belong to *different sections*
  (a heading sits between them) must NOT be stitched into one. This is the
  GreatEastern COVID-endorsement bug: eight per-benefit plan grids, all with a
  ``Prestige | Elite | Classic`` header, were merged across page breaks.
- A genuine continuation whose only intervening content is a *running header*
  (the same heading repeated atop each page) must STILL merge.
"""

from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)

from table_stitcher import stitch_tables
from table_stitcher.adapters.docling import DoclingAdapter, _compute_content_before
from table_stitcher.models import MultiPageConfig

PLAN_HEADER = ["", "Prestige plan", "Elite plan", "Classic plan"]


def _table_data(header, rows):
    cells, grid = [], []
    hrow = []
    for j, h in enumerate(header):
        c = TableCell(
            text=str(h), row_span=1, col_span=1, column_header=True, row_header=False,
            start_row_offset_idx=0, end_row_offset_idx=1,
            start_col_offset_idx=j, end_col_offset_idx=j + 1,
        )
        hrow.append(c)
        cells.append(c)
    grid.append(hrow)
    for i, row in enumerate(rows):
        grow = []
        for j, v in enumerate(row):
            c = TableCell(
                text=str(v), row_span=1, col_span=1, column_header=False, row_header=False,
                start_row_offset_idx=i + 1, end_row_offset_idx=i + 2,
                start_col_offset_idx=j, end_col_offset_idx=j + 1,
            )
            grow.append(c)
            cells.append(c)
        grid.append(grow)
    return TableData(num_rows=len(rows) + 1, num_cols=len(header), table_cells=cells, grid=grid)


def _prov(page, top):
    return ProvenanceItem(
        page_no=page,
        bbox=BoundingBox(l=50, t=top, r=550, b=top + 18, coord_origin=CoordOrigin.TOPLEFT),
        charspan=(0, 0),
    )


def _new_doc(pages=2):
    doc = DoclingDocument(name="synthetic")
    for p in range(1, pages + 1):
        doc.add_page(page_no=p, size=Size(width=600, height=800))
    return doc


def _is_blanked(table):
    """A satellite merged away by inject() becomes num_rows=0 with empty prov."""
    return (getattr(table.data, "num_rows", 0) or 0) == 0 and not (table.prov or [])


def test_heading_between_same_schema_tables_blocks_merge():
    """The endorsement bug: a unique heading between two plan grids => separate."""
    doc = _new_doc()
    doc.add_table(data=_table_data(PLAN_HEADER, [["Repatriation", "S$5,000", "S$5,000", "S$5,000"]]),
                  prov=_prov(1, 600))
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text="38d - Trip cancellation", prov=_prov(2, 80))
    doc.add_text(label=DocItemLabel.TEXT, text="Cover under section 15 is extended ...", prov=_prov(2, 110))
    doc.add_table(data=_table_data(PLAN_HEADER, [["Trip cancellation", "S$8,000", "S$5,000", "S$3,000"]]),
                  prov=_prov(2, 300))

    cmap = _compute_content_before(doc, MultiPageConfig())
    assert cmap.get(1) is True, "heading before the 2nd table should be detected as a boundary"

    stitch_tables(doc)
    assert not _is_blanked(doc.tables[0]) and not _is_blanked(doc.tables[1]), \
        "tables in different sections must not be merged"


def test_running_header_between_fragments_still_merges():
    """A repeated heading atop each page is furniture, not a boundary => merge."""
    doc = _new_doc()
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text="Summary of benefits", prov=_prov(1, 60))
    doc.add_table(data=_table_data(PLAN_HEADER, [["Death", "100%", "100%", "100%"]]),
                  prov=_prov(1, 600))
    # Same heading repeated at the top of page 2 — a running header.
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text="Summary of benefits", prov=_prov(2, 60))
    doc.add_table(data=_table_data(PLAN_HEADER, [["Disability", "100%", "100%", "100%"]]),
                  prov=_prov(2, 90))

    cmap = _compute_content_before(doc, MultiPageConfig())
    assert cmap.get(1) is False, "a repeated running header must not count as a boundary"

    stitch_tables(doc)
    assert _is_blanked(doc.tables[1]), "a true continuation should still be stitched"


def test_guard_disabled_restores_legacy_merge():
    """With the flag off, the heading no longer blocks (legacy behaviour)."""
    doc = _new_doc()
    doc.add_table(data=_table_data(PLAN_HEADER, [["Repatriation", "S$5,000", "S$5,000", "S$5,000"]]),
                  prov=_prov(1, 600))
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text="38d - Trip cancellation", prov=_prov(2, 80))
    doc.add_table(data=_table_data(PLAN_HEADER, [["Trip cancellation", "S$8,000", "S$5,000", "S$3,000"]]),
                  prov=_prov(2, 300))

    stitch_tables(doc, config=MultiPageConfig(block_on_intervening_content=False))
    assert _is_blanked(doc.tables[1]), "legacy header-similarity merge should still happen when disabled"
