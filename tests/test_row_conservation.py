"""
Row conservation through the public ``stitch_tables`` API.

Every data row printed in the input fragments must appear in the stitched
document exactly once — never dropped, never duplicated — whatever the merger
decides about merging. Exercised end-to-end on synthetic DoclingDocuments
that vary cell kinds (identifiers, words, ISO/US dates, integers, money,
names), column count, rows per fragment, header reprinting and page count.

The all-text shape (identifier | status word | ISO date, no numeric cell) was
the one that lost rows: the continuation's first data row was promoted to a
header, the fragment classed a header orphan, and the orphan merge dropped
rows silently.
"""

import collections
import logging
import random

import pytest
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.document import DoclingDocument, ProvenanceItem, TableCell, TableData

import table_stitcher

# ---------------------------------------------------------------------------
# Synthetic document builder
# ---------------------------------------------------------------------------


def _make_doc(fragments):
    """fragments: list of (page, rows, first_row_is_header)."""
    doc = DoclingDocument(name="row-conservation")
    for p in sorted({f[0] for f in fragments}):
        doc.add_page(page_no=p, size=Size(width=612.0, height=792.0))
    for page, rows, header in fragments:
        cells, grid = [], []
        for ri, row in enumerate(rows):
            line = []
            for ci, text in enumerate(row):
                c = TableCell(
                    text=text,
                    row_span=1,
                    col_span=1,
                    start_row_offset_idx=ri,
                    end_row_offset_idx=ri + 1,
                    start_col_offset_idx=ci,
                    end_col_offset_idx=ci + 1,
                    column_header=(header and ri == 0),
                    bbox=BoundingBox(
                        l=40 + 120 * ci,
                        t=100 + 12 * ri,
                        r=150 + 120 * ci,
                        b=110 + 12 * ri,
                        coord_origin=CoordOrigin.TOPLEFT,
                    ),
                )
                cells.append(c)
                line.append(c)
            grid.append(line)
        doc.add_table(
            data=TableData(table_cells=cells, num_rows=len(rows), num_cols=len(rows[0]), grid=grid),
            prov=ProvenanceItem(
                page_no=page,
                charspan=(0, 0),
                bbox=BoundingBox(
                    l=30,
                    t=100,
                    r=580,
                    b=100 + 12 * len(rows),
                    coord_origin=CoordOrigin.TOPLEFT,
                ),
            ),
        )
    return doc


def _lost_and_duplicated(fragments):
    """Identifier column (col 0) of every printed data row, checked in the output."""
    printed = [r[0] for _, rows, h in fragments for r in (rows[1:] if h else rows)]
    out = table_stitcher.stitch_tables(_make_doc(fragments))
    got = collections.Counter(row[0].text for t in out.tables for row in t.data.grid)
    lost = [x for x in printed if got[x] == 0]
    dup = [x for x in printed if got[x] > 1]
    return lost, dup


# ---------------------------------------------------------------------------
# Named cases
# ---------------------------------------------------------------------------


def _id_status_date_rows(page):
    return [[f"R{page}{k:02d}", "Completed", f"2009-0{page % 9 + 1}-1{k}"] for k in range(3)]


def test_all_text_headerless_continuations_lose_no_rows():
    fragments = [
        (1, [["Subject Number", "Status", "Date"]] + _id_status_date_rows(1), True),
        (2, _id_status_date_rows(2), False),
        (3, _id_status_date_rows(3), False),
    ]
    lost, dup = _lost_and_duplicated(fragments)
    assert lost == [] and dup == []


def test_reprinted_header_with_one_text_row_loses_no_rows():
    # Each continuation page reprints the header above a single all-text row
    # (header + one header-shaped row). That row must not be consumed as a
    # header by the orphan merge.
    header = ["Subject Number", "Status"]
    fragments = [
        (1, [header, ["R100", "Completed"], ["R101", "Withdrawn"]], True),
        (2, [header, ["R200", "Completed"]], True),
        (3, [header, ["R300", "Pending"]], True),
    ]
    lost, dup = _lost_and_duplicated(fragments)
    assert lost == [] and dup == []


# ---------------------------------------------------------------------------
# Property check over generated fragment sets (deterministic seeds)
# ---------------------------------------------------------------------------

_KINDS = {
    "id": lambda p, k: f"R{p}{k:02d}",
    "word": lambda p, k: random.choice(["Completed", "Withdrawn", "Pending", "Active"]),
    "iso_date": lambda p, k: f"2009-0{p % 9 + 1}-1{k}",
    "us_date": lambda p, k: f"0{p % 9 + 1}/1{k}/2009",
    "int": lambda p, k: str(10 * p + k),
    "money": lambda p, k: f"${1000 * p + k:,}.00",
    "name": lambda p, k: random.choice(
        ["Smith LLP", "Jane Doe", "Acme Corp", "City Of Manchester"]
    ),
}
_HEADER_WORDS = [
    "Subject Number",
    "Status",
    "Date",
    "Study Day",
    "Amount",
    "Party",
    "Email",
    "Notes",
]


def _case(seed):
    random.seed(seed)
    ncol = random.randint(2, 6)
    kinds = ["id"] + [random.choice(list(_KINDS)) for _ in range(ncol - 1)]
    header = _HEADER_WORDS[:ncol]
    npages = random.randint(2, 4)
    reprint = random.random() < 0.5
    frags = []
    for p in range(1, npages + 1):
        nrows = random.randint(1, 8)
        rows = [[_KINDS[kd](p, k) for kd in kinds] for k in range(nrows)]
        has_header = p == 1 or reprint
        frags.append((p, ([header] if has_header else []) + rows, has_header))
    return frags


@pytest.mark.parametrize("seed_block", range(0, 200, 25), ids=lambda s: f"seeds-{s}-{s + 24}")
def test_generated_fragments_conserve_rows(seed_block, caplog):
    caplog.set_level(logging.ERROR)
    failures = {}
    for seed in range(seed_block, seed_block + 25):
        lost, dup = _lost_and_duplicated(_case(seed))
        if lost or dup:
            failures[seed] = {"lost": lost, "duplicated": dup}
    assert failures == {}
