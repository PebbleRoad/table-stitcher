"""
Tests for the Docling adapter — DataFrame conversion, injection, and pruning.
"""

from types import SimpleNamespace

import pandas as pd
import pytest
from docling_core.types.doc import DoclingDocument, TableCell, TableData

from table_stitcher.adapters.base import TableStitcherAdapter
from table_stitcher.adapters.docling import (
    DoclingAdapter,
    _dataframe_to_docling_data,
    _detect_header_orphan,
    _grid_to_dataframe,
)
from table_stitcher.models import LogicalTable


class TestEmptyDataFrame:
    """
    dataframe_to_docling_data() must produce a valid header-only table
    when df.empty, not grid=[[]] with no cells.
    """

    def test_empty_df_with_columns_produces_valid_grid(self):
        df = pd.DataFrame(columns=["Name", "Age", "Status"])
        td = _dataframe_to_docling_data(df)

        assert td.num_rows == 1
        assert td.num_cols == 3
        assert len(td.table_cells) == 3
        assert len(td.grid) == 1
        assert len(td.grid[0]) == 3

    def test_empty_df_cells_are_column_headers(self):
        df = pd.DataFrame(columns=["X", "Y"])
        td = _dataframe_to_docling_data(df)

        for cell in td.table_cells:
            assert cell.column_header is True
        assert td.table_cells[0].text == "X"
        assert td.table_cells[1].text == "Y"

    def test_empty_df_no_columns_fallback(self):
        df = pd.DataFrame()
        td = _dataframe_to_docling_data(df)

        assert td.num_rows == 1
        assert td.num_cols == 1
        assert len(td.table_cells) == 1
        assert td.table_cells[0].text == "Column_0"

    def test_empty_df_cell_offsets_are_valid(self):
        df = pd.DataFrame(columns=["A", "B"])
        td = _dataframe_to_docling_data(df)

        for j, cell in enumerate(td.table_cells):
            assert cell.start_row_offset_idx == 0
            assert cell.end_row_offset_idx == 1
            assert cell.start_col_offset_idx == j
            assert cell.end_col_offset_idx == j + 1


class TestNonEmptyDataFrame:
    def test_basic_conversion(self):
        df = pd.DataFrame({"Name": ["Alice", "Bob"], "Age": ["30", "25"]})
        td = _dataframe_to_docling_data(df)

        assert td.num_rows == 3
        assert td.num_cols == 2
        assert len(td.table_cells) == 6
        assert len(td.grid) == 3

    def test_header_row_is_column_header(self):
        df = pd.DataFrame({"X": [1]})
        td = _dataframe_to_docling_data(df)

        header_cell = td.grid[0][0]
        assert header_cell.column_header is True
        assert header_cell.text == "X"

        data_cell = td.grid[1][0]
        assert data_cell.column_header is False


class TestHeaderPreservation:
    """_dataframe_to_docling_data() should preserve multi-row headers with spans."""

    def _make_multirow_header_data(self) -> TableData:
        """Build a TableData with a 2-row header (rowspan + colspan)."""
        # Row 0: [rowspan=2 ""], [rowspan=2 "Claim"], [colspan=3 "Amount"]
        # Row 1: [Basic], [Classic], [Elite]
        h00 = TableCell(
            text="",
            row_span=2,
            col_span=1,
            column_header=True,
            row_header=False,
            start_row_offset_idx=0,
            end_row_offset_idx=2,
            start_col_offset_idx=0,
            end_col_offset_idx=1,
        )
        h01 = TableCell(
            text="Claim event(s)",
            row_span=2,
            col_span=1,
            column_header=True,
            row_header=False,
            start_row_offset_idx=0,
            end_row_offset_idx=2,
            start_col_offset_idx=1,
            end_col_offset_idx=2,
        )
        h02 = TableCell(
            text="Amount payable (S$)",
            row_span=1,
            col_span=3,
            column_header=True,
            row_header=False,
            start_row_offset_idx=0,
            end_row_offset_idx=1,
            start_col_offset_idx=2,
            end_col_offset_idx=5,
        )
        h10 = TableCell(
            text="Basic",
            row_span=1,
            col_span=1,
            column_header=True,
            row_header=False,
            start_row_offset_idx=1,
            end_row_offset_idx=2,
            start_col_offset_idx=2,
            end_col_offset_idx=3,
        )
        h11 = TableCell(
            text="Classic",
            row_span=1,
            col_span=1,
            column_header=True,
            row_header=False,
            start_row_offset_idx=1,
            end_row_offset_idx=2,
            start_col_offset_idx=3,
            end_col_offset_idx=4,
        )
        h12 = TableCell(
            text="Elite",
            row_span=1,
            col_span=1,
            column_header=True,
            row_header=False,
            start_row_offset_idx=1,
            end_row_offset_idx=2,
            start_col_offset_idx=4,
            end_col_offset_idx=5,
        )
        # Data row
        d0 = TableCell(
            text="A",
            row_span=1,
            col_span=1,
            column_header=False,
            row_header=False,
            start_row_offset_idx=2,
            end_row_offset_idx=3,
            start_col_offset_idx=0,
            end_col_offset_idx=1,
        )
        d1 = TableCell(
            text="Death",
            row_span=1,
            col_span=1,
            column_header=False,
            row_header=False,
            start_row_offset_idx=2,
            end_row_offset_idx=3,
            start_col_offset_idx=1,
            end_col_offset_idx=2,
        )
        d2 = TableCell(
            text="200,000",
            row_span=1,
            col_span=1,
            column_header=False,
            row_header=False,
            start_row_offset_idx=2,
            end_row_offset_idx=3,
            start_col_offset_idx=2,
            end_col_offset_idx=3,
        )
        d3 = TableCell(
            text="500,000",
            row_span=1,
            col_span=1,
            column_header=False,
            row_header=False,
            start_row_offset_idx=2,
            end_row_offset_idx=3,
            start_col_offset_idx=3,
            end_col_offset_idx=4,
        )
        d4 = TableCell(
            text="1,000,000",
            row_span=1,
            col_span=1,
            column_header=False,
            row_header=False,
            start_row_offset_idx=2,
            end_row_offset_idx=3,
            start_col_offset_idx=4,
            end_col_offset_idx=5,
        )

        all_cells = [h00, h01, h02, h10, h11, h12, d0, d1, d2, d3, d4]
        grid = [
            [h00, h01, h02],  # header row 0
            [h10, h11, h12],  # header row 1
            [d0, d1, d2, d3, d4],  # data row
        ]
        return TableData(num_rows=3, num_cols=5, table_cells=all_cells, grid=grid)

    def test_multirow_header_preserved(self):
        """When original_data has a 2-row header with spans, it should be reused."""
        original = self._make_multirow_header_data()
        merged_df = pd.DataFrame(
            {
                "col0": ["A", "D"],
                "col1": ["Death", "Medical"],
                "col2": ["200,000", "3,000"],
                "col3": ["500,000", "4,000"],
                "col4": ["1,000,000", "5,000"],
            }
        )

        td = _dataframe_to_docling_data(merged_df, original_data=original)

        # Should have 2 header rows + 2 data rows = 4 total
        assert td.num_rows == 4
        assert len(td.grid) == 4

        # Header row 0 preserved with spans
        assert td.grid[0][0].text == ""
        assert td.grid[0][0].row_span == 2
        assert td.grid[0][2].text == "Amount payable (S$)"
        assert td.grid[0][2].col_span == 3

        # Header row 1 preserved — Docling normalizes the grid by filling
        # rowspan cells into spanned positions, so row 1 has 5 cells:
        # [""(rowspan), "Claim"(rowspan), "Basic", "Classic", "Elite"]
        assert td.grid[1][2].text == "Basic"
        assert td.grid[1][3].text == "Classic"
        assert td.grid[1][4].text == "Elite"

        # Data rows from DataFrame
        assert td.grid[2][0].text == "A"
        assert td.grid[3][0].text == "D"
        assert td.grid[3][2].text == "3,000"

        # Data row offsets should account for 2 header rows
        assert td.grid[2][0].start_row_offset_idx == 2
        assert td.grid[3][0].start_row_offset_idx == 3

    def test_no_original_data_falls_back_to_flat_header(self):
        """Without original_data, should build flat 1x1 headers from DataFrame columns."""
        df = pd.DataFrame({"Name": ["Alice"], "Age": ["30"]})
        td = _dataframe_to_docling_data(df, original_data=None)

        assert td.num_rows == 2
        assert len(td.grid) == 2
        assert td.grid[0][0].text == "Name"
        assert td.grid[0][0].row_span == 1
        assert td.grid[0][0].col_span == 1

    def test_single_row_header_original_still_reused(self):
        """Even a simple 1-row header from original_data should be reused."""
        cells = [
            TableCell(
                text="Score",
                row_span=1,
                col_span=1,
                column_header=True,
                row_header=False,
                start_row_offset_idx=0,
                end_row_offset_idx=1,
                start_col_offset_idx=0,
                end_col_offset_idx=1,
            ),
        ]
        original = TableData(num_rows=1, num_cols=1, table_cells=cells, grid=[cells])

        df = pd.DataFrame({"x": ["10", "20"]})
        td = _dataframe_to_docling_data(df, original_data=original)

        # Should use "Score" from original, not "x" from DataFrame
        assert td.grid[0][0].text == "Score"
        assert td.num_rows == 3  # 1 header + 2 data


class TestAdapterProtocol:
    """Verify DoclingAdapter satisfies the protocol."""

    def test_docling_adapter_is_instance_of_protocol(self):
        adapter = DoclingAdapter()
        assert isinstance(adapter, TableStitcherAdapter)

    def test_adapter_has_required_methods(self):
        adapter = DoclingAdapter()
        assert hasattr(adapter, "extract")
        assert hasattr(adapter, "inject")
        assert callable(adapter.extract)
        assert callable(adapter.inject)


# ---------------------------------------------------------------------------
# Helpers for injection tests
# ---------------------------------------------------------------------------


def _make_table_data(header: str, val: str) -> TableData:
    """Build a minimal 1-column, 1-row TableData."""
    cells = [
        TableCell(
            text=header,
            row_span=1,
            col_span=1,
            column_header=True,
            row_header=False,
            start_row_offset_idx=0,
            end_row_offset_idx=1,
            start_col_offset_idx=0,
            end_col_offset_idx=1,
        ),
        TableCell(
            text=val,
            row_span=1,
            col_span=1,
            column_header=False,
            row_header=False,
            start_row_offset_idx=1,
            end_row_offset_idx=2,
            start_col_offset_idx=0,
            end_col_offset_idx=1,
        ),
    ]
    return TableData(num_rows=2, num_cols=1, table_cells=cells, grid=[[cells[0]], [cells[1]]])


def _build_doc_with_tables(n: int) -> DoclingDocument:
    """Create a DoclingDocument with n simple tables registered in body."""
    doc = DoclingDocument(name="test")
    for i in range(n):
        doc.add_table(data=_make_table_data(f"H{i}", f"V{i}"))
    return doc


# ---------------------------------------------------------------------------
# Injection & pruning tests
# ---------------------------------------------------------------------------


class TestInjection:
    """Test DoclingAdapter.inject() — data replacement, provenance, pruning."""

    def test_single_member_table_not_modified(self):
        """Tables with a single member should be left untouched."""
        doc = _build_doc_with_tables(2)
        original_data_0 = doc.tables[0].data
        original_data_1 = doc.tables[1].data

        logical_tables = [
            LogicalTable(0, [0], [1], pd.DataFrame({"X": ["new"]})),
            LogicalTable(1, [1], [2], pd.DataFrame({"Y": ["new"]})),
        ]

        adapter = DoclingAdapter()
        adapter.inject(doc, logical_tables)

        # Data should be unchanged — single-member tables are skipped
        assert doc.tables[0].data is original_data_0
        assert doc.tables[1].data is original_data_1

    def test_merged_table_data_replaced(self):
        """Anchor table's data should be replaced with merged DataFrame,
        preserving original header rows from the anchor's grid."""
        doc = _build_doc_with_tables(3)

        merged_df = pd.DataFrame({"Name": ["Alice", "Bob", "Charlie"]})
        logical_tables = [
            LogicalTable(0, [0, 1], [1, 2], merged_df),
            LogicalTable(1, [2], [3], pd.DataFrame({"X": ["solo"]})),
        ]

        adapter = DoclingAdapter()
        adapter.inject(doc, logical_tables)

        # Anchor (table 0) should have new data
        assert doc.tables[0].data.num_rows == 4  # 1 header + 3 data rows
        assert doc.tables[0].data.num_cols == 1
        # Header is preserved from the anchor's original grid (not DataFrame columns)
        assert doc.tables[0].data.grid[0][0].text == "H0"
        assert doc.tables[0].data.grid[0][0].column_header is True
        # Data rows come from the merged DataFrame
        assert doc.tables[0].data.grid[1][0].text == "Alice"

    def test_satellite_refs_pruned_from_body(self):
        """Satellite table references should be removed from doc.body.children."""
        doc = _build_doc_with_tables(3)
        assert len(doc.body.children) == 3

        merged_df = pd.DataFrame({"A": ["merged"]})
        logical_tables = [
            LogicalTable(0, [0, 1], [1, 2], merged_df),  # table 1 is satellite
            LogicalTable(1, [2], [3], pd.DataFrame({"B": ["solo"]})),
        ]

        adapter = DoclingAdapter()
        adapter.inject(doc, logical_tables)

        # Satellite (table 1) should be pruned from body
        remaining_refs = [c.cref for c in doc.body.children]
        assert "#/tables/0" in remaining_refs  # anchor kept
        assert "#/tables/1" not in remaining_refs  # satellite pruned
        assert "#/tables/2" in remaining_refs  # unrelated kept
        assert len(remaining_refs) == 2

    def test_provenance_merged_from_satellite(self):
        """Satellite provenance should be appended to anchor's prov."""
        doc = _build_doc_with_tables(2)

        # Manually set provenance on both tables
        from types import SimpleNamespace

        prov_a = SimpleNamespace(page_no=1, bbox=None)
        prov_b = SimpleNamespace(page_no=2, bbox=None)
        doc.tables[0].prov = [prov_a]
        doc.tables[1].prov = [prov_b]

        merged_df = pd.DataFrame({"X": ["merged"]})
        logical_tables = [
            LogicalTable(0, [0, 1], [1, 2], merged_df),
        ]

        adapter = DoclingAdapter()
        adapter.inject(doc, logical_tables)

        # Anchor should now have both provenance records
        assert len(doc.tables[0].prov) == 2
        assert doc.tables[0].prov[0].page_no == 1
        assert doc.tables[0].prov[1].page_no == 2

    def test_multiple_satellites_all_pruned(self):
        """When 3+ fragments merge, all satellites should be pruned."""
        doc = _build_doc_with_tables(4)
        assert len(doc.body.children) == 4

        merged_df = pd.DataFrame({"A": ["x", "y", "z"]})
        logical_tables = [
            LogicalTable(0, [0, 1, 2], [1, 2, 3], merged_df),  # 0=anchor, 1,2=satellites
            LogicalTable(1, [3], [4], pd.DataFrame({"B": ["solo"]})),
        ]

        adapter = DoclingAdapter()
        adapter.inject(doc, logical_tables)

        remaining_refs = [c.cref for c in doc.body.children]
        assert "#/tables/0" in remaining_refs
        assert "#/tables/1" not in remaining_refs
        assert "#/tables/2" not in remaining_refs
        assert "#/tables/3" in remaining_refs
        assert len(remaining_refs) == 2

    def test_satellite_table_data_is_cleared(self):
        """
        Satellite Table objects stay in doc.tables (removing would invalidate
        position-based self_refs), but their .data and .prov must be cleared
        so downstream code iterating doc.tables directly doesn't see stale
        fragment content that's already been merged into the anchor.
        """
        from types import SimpleNamespace

        doc = _build_doc_with_tables(3)
        doc.tables[1].prov = [SimpleNamespace(page_no=2, bbox=None)]

        merged_df = pd.DataFrame({"A": ["merged"]})
        logical_tables = [
            LogicalTable(0, [0, 1], [1, 2], merged_df),  # table 1 is satellite
            LogicalTable(1, [2], [3], pd.DataFrame({"B": ["solo"]})),
        ]

        adapter = DoclingAdapter()
        adapter.inject(doc, logical_tables)

        # Satellite is still present (preserving self_refs), but emptied.
        assert len(doc.tables) == 3
        assert doc.tables[1].self_ref == "#/tables/1"
        assert doc.tables[1].data.num_rows == 0
        assert doc.tables[1].data.num_cols == 0
        assert doc.tables[1].data.table_cells == []
        assert doc.tables[1].prov == []
        # Anchor still has content.
        assert doc.tables[0].data.num_rows > 0
        # Unrelated table untouched.
        assert doc.tables[2].data is not None

    def test_injection_failure_restores_partial_mutations(self, monkeypatch):
        """
        If injection fails after mutating an earlier logical table, the adapter
        must restore the table data/provenance and body references it touched.
        """
        import table_stitcher.adapters.docling as docling_adapter

        doc = _build_doc_with_tables(4)
        original_data = [t.data for t in doc.tables]
        original_body_children = list(doc.body.children)

        calls = {"count": 0}
        real_convert = docling_adapter._dataframe_to_docling_data

        def fail_on_second_conversion(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("synthetic injection failure")
            return real_convert(*args, **kwargs)

        monkeypatch.setattr(
            docling_adapter,
            "_dataframe_to_docling_data",
            fail_on_second_conversion,
        )

        logical_tables = [
            LogicalTable(0, [0, 1], [1, 2], pd.DataFrame({"A": ["first"]})),
            LogicalTable(1, [2, 3], [3, 4], pd.DataFrame({"B": ["second"]})),
        ]

        with pytest.raises(RuntimeError, match="synthetic injection failure"):
            DoclingAdapter().inject(doc, logical_tables)

        assert [t.data for t in doc.tables] == original_data
        assert list(doc.body.children) == original_body_children
        assert [c.cref for c in doc.body.children] == [
            "#/tables/0",
            "#/tables/1",
            "#/tables/2",
            "#/tables/3",
        ]


# ---------------------------------------------------------------------------
# Pass-through guarantee tests
# ---------------------------------------------------------------------------


class TestPassThrough:
    """
    Table-stitcher must never lose data. If stitching can't process a table,
    the original must pass through unchanged.
    """

    def test_no_merge_candidates_returns_doc_unchanged(self):
        """When no tables can merge, the doc comes back identical."""
        doc = _build_doc_with_tables(2)
        original_data_0 = doc.tables[0].data
        original_data_1 = doc.tables[1].data

        # No multi-member logical tables = nothing to inject
        logical_tables = [
            LogicalTable(0, [0], [1], pd.DataFrame({"A": ["x"]})),
            LogicalTable(1, [1], [2], pd.DataFrame({"B": ["y"]})),
        ]

        adapter = DoclingAdapter()
        result = adapter.inject(doc, logical_tables)

        assert result.tables[0].data is original_data_0
        assert result.tables[1].data is original_data_1
        assert len(result.body.children) == 2  # nothing pruned

    def test_empty_logical_tables_returns_doc_unchanged(self):
        """Empty logical table list should not modify the doc."""
        doc = _build_doc_with_tables(3)
        original_children = len(doc.body.children)

        adapter = DoclingAdapter()
        result = adapter.inject(doc, [])

        assert len(result.tables) == 3
        assert len(result.body.children) == original_children

    def test_stitch_orchestrator_returns_original_on_no_tables(self):
        """TableStitcher.stitch() returns original doc when adapter finds no tables."""
        from table_stitcher import MultiPageConfig, TableStitcher

        class EmptyAdapter:
            def extract(self, doc, cfg):
                return []

            def inject(self, doc, logical_tables):
                return doc

        doc = {"sentinel": True}  # any object
        stitcher = TableStitcher(adapter=EmptyAdapter(), config=MultiPageConfig())
        result = stitcher.stitch(doc)

        assert result is doc  # exact same object returned

    def test_stitch_orchestrator_returns_original_on_extract_failure(self):
        """If extract() blows up, the original doc is returned."""
        from table_stitcher import MultiPageConfig, TableStitcher

        class BrokenAdapter:
            def extract(self, doc, cfg):
                raise RuntimeError("extraction exploded")

            def inject(self, doc, logical_tables):
                return doc

        doc = {"sentinel": True}
        stitcher = TableStitcher(adapter=BrokenAdapter(), config=MultiPageConfig())
        result = stitcher.stitch(doc)

        assert result is doc


# ---------------------------------------------------------------------------
# Header-detection heuristics in _grid_to_dataframe
#
# When a fragment's real header is eaten into data on a given page (parser
# artifact that shows up across retirement-portfolio and several PubTables-v2
# medical docs), the adapter's first-row classifier must recognise the data
# shape so the merger's headerless-continuation path can do its job.
# ---------------------------------------------------------------------------


def _mk_table(rows):
    """Build a docling-shaped table stub from a list-of-lists of strings."""
    grid = [[SimpleNamespace(text=c) for c in row] for row in rows]
    return SimpleNamespace(data=SimpleNamespace(grid=grid))


class TestHeaderlessDetection:
    def test_comma_separated_decimal_flags_headerless(self):
        # Retirement-portfolio pattern: first row is data but one cell is a
        # comma-grouped dollar amount.
        table = _mk_table(
            [
                ["", "Am Fds Trgt Dte Rtm 2055 R6 Fd", "13,085.03"],
                ["ELEC DEF", "Am Fds Trgt Dte Rtm 2045 R6 Fd", "4,759.09"],
            ]
        )
        df = _grid_to_dataframe(table, doc=None)
        assert df.attrs["is_headerless"] is True

    def test_value_with_paren_range_flags_headerless(self):
        # Medical-stats pattern: "280 (176, 404)" median with IQR.
        table = _mk_table(
            [
                ["Platelets #/nL", "Platelets #/nL", "280 (176, 404)"],
                ["Platelets #/nL", "Platelets #/nL", "158 (123, 240)"],
            ]
        )
        df = _grid_to_dataframe(table, doc=None)
        assert df.attrs["is_headerless"] is True

    def test_scientific_notation_flags_headerless(self):
        table = _mk_table(
            [
                ["Result", "p-value", "7.0 x 10-7"],
                ["A", "B", "1.0 x 10-3"],
            ]
        )
        df = _grid_to_dataframe(table, doc=None)
        assert df.attrs["is_headerless"] is True

    def test_long_cell_majority_flags_headerless(self):
        # Lit-review pattern: >half the first-row cells are sentence-long.
        long_a = "Changes in choroidal thickness after cataract surgery"
        long_b = "Prospective observational study of 80 eyes"
        long_c = "Manual tracing of RPE and choroidal-scleral interface"
        table = _mk_table(
            [
                ["Column_0", long_a, long_b, long_c, "Spectral domain"],
                ["data1", "data2", "data3", "data4", "data5"],
            ]
        )
        df = _grid_to_dataframe(table, doc=None)
        assert df.attrs["is_headerless"] is True

    def test_legitimate_short_headers_stay_header(self):
        # Regression guard: ordinary headers (all short, not data-shaped)
        # must NOT be flagged headerless.
        table = _mk_table(
            [
                ["Contribution Type", "Investment Name", "Total"],
                ["ELEC DEF", "Am Fds 2055", "110.12"],
            ]
        )
        df = _grid_to_dataframe(table, doc=None)
        assert df.attrs["is_headerless"] is False
        assert list(df.columns) == ["Contribution Type", "Investment Name", "Total"]

    def test_year_columns_promoted_to_header(self):
        # Reported in #4: row 1 of bare 4-digit years contradicted by a
        # currency-shaped body row → row 1 is a column-axis header, not data.
        table = _mk_table(
            [
                ["2020", "2021", "2022", "2023"],
                ["$13,085", "$14,200", "$15,300", "$16,800"],
            ]
        )
        df = _grid_to_dataframe(table, doc=None)
        assert df.attrs["is_headerless"] is False
        assert list(df.columns) == ["2020", "2021", "2022", "2023"]

    def test_uniform_int_row_over_uniform_int_row_stays_headerless(self):
        # Counter-example: lottery / ID rows. Row 1 and body share the same
        # subshape (bare_int) → no contrast → must NOT be promoted to header.
        table = _mk_table(
            [
                ["7", "13", "22", "41", "58"],
                ["12", "19", "33", "47", "62"],
            ]
        )
        df = _grid_to_dataframe(table, doc=None)
        assert df.attrs["is_headerless"] is True

    def test_ordinal_int_columns_with_text_body(self):
        # `[1, 2, 3, 4]` ordinal column labels over a text body → header.
        table = _mk_table(
            [
                ["1", "2", "3", "4"],
                ["red", "blue", "green", "yellow"],
            ]
        )
        df = _grid_to_dataframe(table, doc=None)
        assert df.attrs["is_headerless"] is False
        assert list(df.columns) == ["1", "2", "3", "4"]


class TestHeaderOrphanWithDataShapedColumns:
    def test_year_only_fragment_detected_as_orphan(self):
        # A standalone fragment of just `[2020, 2021, 2022, 2023]` (no body
        # rows) is a header that the parser separated from its data — must
        # still register as a header orphan despite the cells matching a
        # data pattern, because the columns are uniformly one subshape.
        df = pd.DataFrame(columns=["2020", "2021", "2022", "2023"])
        assert _detect_header_orphan(df, is_headerless=False, max_orphan_rows=2) is True

    def test_mixed_data_columns_not_an_orphan(self):
        # Columns that mix subshapes (currency + bare int) are real data,
        # not a uniform header axis.
        df = pd.DataFrame(columns=["$1,000", "2020", "Notes"])
        assert _detect_header_orphan(df, is_headerless=False, max_orphan_rows=2) is False
