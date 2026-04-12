"""
Tests for the Docling adapter — DataFrame conversion, injection, and pruning.
"""

import pandas as pd
import pytest
from docling_core.types.doc import DoclingDocument, TableData, TableCell

from table_stitcher.adapters.docling import _dataframe_to_docling_data, DoclingAdapter
from table_stitcher.adapters.base import TableStitcherAdapter
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
        TableCell(text=header, row_span=1, col_span=1, column_header=True,
                  row_header=False, start_row_offset_idx=0, end_row_offset_idx=1,
                  start_col_offset_idx=0, end_col_offset_idx=1),
        TableCell(text=val, row_span=1, col_span=1, column_header=False,
                  row_header=False, start_row_offset_idx=1, end_row_offset_idx=2,
                  start_col_offset_idx=0, end_col_offset_idx=1),
    ]
    return TableData(num_rows=2, num_cols=1, table_cells=cells,
                     grid=[[cells[0]], [cells[1]]])


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
        """Anchor table's data should be replaced with merged DataFrame."""
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
        # Check the actual cell content
        assert doc.tables[0].data.grid[0][0].text == "Name"
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
        from table_stitcher import TableStitcher, MultiPageConfig

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
        from table_stitcher import TableStitcher, MultiPageConfig

        class BrokenAdapter:
            def extract(self, doc, cfg):
                raise RuntimeError("extraction exploded")
            def inject(self, doc, logical_tables):
                return doc

        doc = {"sentinel": True}
        stitcher = TableStitcher(adapter=BrokenAdapter(), config=MultiPageConfig())
        result = stitcher.stitch(doc)

        assert result is doc
