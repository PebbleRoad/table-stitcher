"""
Generate the .synth.pdf integration fixtures deterministically.

Run from the repo root:
    python -m tests.integration.fixtures._synth.generate

Each PDF exercises a structural merge rule that real-world PDFs surface only
sporadically (spillover, unrelated tables separated by a large page gap).
Hand-building keeps the trick reliable and the license clean.

Requires reportlab (BSD-3). Installed via the `dev` optional extra.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

FIXTURES = Path(__file__).resolve().parents[1]
STYLES = getSampleStyleSheet()


def _grid_style(header_bg=colors.lightgrey) -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), header_bg),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
    )


# ---------------------------------------------------------------------------
# 1. spillover/note-overflow.synth.pdf
#
#   Page 1: a 3-column task table ("ID", "Description", "Notes"). Final row's
#   Notes cell contains a long note that would naturally overflow the cell.
#   Page 2: a follow-on fragment carrying the overflow text.
#
#   Docling reliably extracts the page-2 fragment as a 1-column headerless
#   table, which is the exact shape is_spillover_fragment() looks for:
#     tA.width > 1, tB.width == 1, tB.is_headerless.
#   The merger's spillover path then stitches the overflow into the last
#   cell of tA instead of creating a separate logical table.
# ---------------------------------------------------------------------------


def build_spillover(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=36, bottomMargin=36)

    rows = [["ID", "Description", "Notes"]]
    for i in range(1, 22):
        rows.append([f"N-{i:03d}", f"Item {i}", f"Short note for item {i}."])

    t = Table(rows, colWidths=[60, 200, 260])
    t.setStyle(_grid_style())

    # Single-column continuation on page 2 — mimics the parser artifact where
    # cell overflow surfaces as its own 1-col fragment. Multiple rows make it
    # reliably detectable as a table by docling's structure model.
    continuation = Table(
        [
            [
                "Continuation of the final note from page 1 — this sentence "
                "belongs to the row above."
            ],
            ["Second overflow line with additional context the cell couldn't fit."],
            ["Third overflow line closing out the note."],
        ],
        colWidths=[380],
    )
    continuation.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    doc.build([t, PageBreak(), continuation])


# ---------------------------------------------------------------------------
# 2. page-gap-too-large/unrelated-tables-gap4.synth.pdf
#
#   Page 1: a small revenue table.
#   Pages 2-3: narrative text only — no tables.
#   Page 4: a completely unrelated HR table.
#   With the default max_page_gap=1 the merger must NOT link them.
# ---------------------------------------------------------------------------


def build_page_gap_too_large(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=36, bottomMargin=36)

    table_one = Table(
        [
            ["Region", "Revenue", "Growth"],
            ["North", "1,200", "12%"],
            ["South", "950", "7%"],
            ["East", "1,430", "18%"],
            ["West", "1,080", "4%"],
        ],
        colWidths=[120, 120, 120],
    )
    table_one.setStyle(_grid_style())

    filler_a = Paragraph(
        "Quarterly narrative — no tables here. " * 40,
        STYLES["BodyText"],
    )
    filler_b = Paragraph(
        "Continuing commentary across multiple paragraphs. " * 40,
        STYLES["BodyText"],
    )

    table_two = Table(
        [
            ["Employee", "Title", "Hire Date"],
            ["Alice", "Engineer", "2021-03-15"],
            ["Bob", "Designer", "2020-11-02"],
            ["Carol", "Manager", "2019-07-28"],
        ],
        colWidths=[120, 120, 120],
    )
    table_two.setStyle(_grid_style())

    doc.build(
        [
            table_one,
            PageBreak(),
            filler_a,
            PageBreak(),
            filler_b,
            PageBreak(),
            table_two,
        ]
    )


JOBS = [
    (build_spillover, FIXTURES / "spillover" / "note-overflow.synth.pdf"),
    (build_page_gap_too_large, FIXTURES / "page-gap-too-large" / "unrelated-tables-gap4.synth.pdf"),
]


def main() -> None:
    for builder, out in JOBS:
        out.parent.mkdir(parents=True, exist_ok=True)
        builder(out)
        size_kb = out.stat().st_size / 1024
        print(f"  wrote {out.relative_to(FIXTURES.parents[2])} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
