"""
Tests for the core merger module.
"""

import pandas as pd
import pytest

from table_stitcher.models import MultiPageConfig, TableMeta, LogicalTable
from table_stitcher.merger import (
    UnionFind,
    merge_multipage_tables,
    jaccard,
    tokenize,
    is_numeric_like_colnames,
    is_spillover_fragment,
    layout_suggests_continuation,
    stitch_split_cells,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_meta(
    idx: int,
    df: pd.DataFrame,
    start_page: int = 1,
    is_header_orphan: bool = False,
    is_data_orphan: bool = False,
    is_headerless: bool = False,
    vert_top: float = None,
    vert_bottom: float = None,
) -> TableMeta:
    """Build a minimal TableMeta for testing."""
    return TableMeta(
        idx=idx,
        df=df,
        start_page=start_page,
        pages=[start_page],
        width=df.shape[1],
        header_tokens=tokenize(" ".join(str(c) for c in df.columns)),
        first_row_tokens=(
            tokenize(" ".join(str(x) for x in df.iloc[0].tolist()))
            if df.shape[0] > 0
            else set()
        ),
        raw_columns=[str(c) for c in df.columns],
        vert_center=None,
        vert_top=vert_top,
        vert_bottom=vert_bottom,
        is_header_orphan=is_header_orphan,
        is_data_orphan=is_data_orphan,
        numeric_like_cols=is_numeric_like_colnames([str(c) for c in df.columns]),
        row_count=df.shape[0],
        is_headerless=is_headerless,
    )


# ---------------------------------------------------------------------------
# Bug 1: IndexError when table extraction skips indices
# ---------------------------------------------------------------------------

class TestUnionFindIndexMapping:
    """
    When extraction fails for some tables, tables_meta contains entries
    whose .idx values are non-contiguous (e.g. 0, 2, 5).  The union-find
    must still work without IndexError.
    """

    def test_non_contiguous_indices_no_crash(self):
        """Simulate tables 0, 2, 4 surviving extraction (1, 3 failed)."""
        df = pd.DataFrame({"A": [1], "B": [2]})
        metas = [
            _make_meta(idx=0, df=df, start_page=1),
            _make_meta(idx=2, df=df, start_page=2, is_headerless=True),
            _make_meta(idx=4, df=df, start_page=3, is_headerless=True),
        ]
        cfg = MultiPageConfig()
        results = merge_multipage_tables(metas, cfg)
        assert len(results) >= 1

    def test_single_table_large_idx(self):
        """A single surviving table with a large idx should not crash."""
        df = pd.DataFrame({"X": [10]})
        metas = [_make_meta(idx=99, df=df, start_page=5)]
        cfg = MultiPageConfig()
        results = merge_multipage_tables(metas, cfg)
        assert len(results) == 1
        assert results[0].members == [99]

    def test_gap_indices_blocked_by_continuity_guard(self):
        """Tables with skipped indices between them should NOT merge."""
        df = pd.DataFrame({"Name": ["Alice"], "Age": ["30"]})
        metas = [
            _make_meta(idx=0, df=df, start_page=1),
            _make_meta(idx=5, df=df, start_page=2, is_headerless=True),
        ]
        cfg = MultiPageConfig()
        results = merge_multipage_tables(metas, cfg)
        # idx 1-4 were not extracted — unknown tables sit between them
        assert len(results) == 2

    def test_orphan_repair_respects_gap_guard(self):
        """
        Pass 2 (orphan repair) must also refuse to merge across an
        unextracted table index, mirroring the Pass 1 guard.
        """
        # A header orphan on page 1 + a data orphan on page 2 would
        # normally trigger should_force_orphan_merge in Pass 2.  Here
        # idx 1..3 are missing (not extracted), so the merge must be
        # blocked.
        header_df = pd.DataFrame(columns=["Name", "Age"])
        data_df = pd.DataFrame({"Name": ["Alice"], "Age": ["30"]})
        metas = [
            _make_meta(idx=0, df=header_df, start_page=1, is_header_orphan=True),
            _make_meta(idx=4, df=data_df, start_page=2, is_data_orphan=True),
        ]
        cfg = MultiPageConfig()
        results = merge_multipage_tables(metas, cfg)
        assert len(results) == 2, (
            "Pass 2 must not merge across unextracted indices 1..3"
        )


# ---------------------------------------------------------------------------
# Bug 3: Orphan-merge anchor picks wrong header source
# ---------------------------------------------------------------------------

class TestOrphanAnchorSelection:
    def test_header_orphan_used_as_anchor(self):
        """Header orphan should anchor, not data fragment."""
        data_df = pd.DataFrame(
            {"Column_0": ["100"], "Column_1": ["200"], "Column_2": ["300"]}
        )
        header_df = pd.DataFrame(columns=["Name", "Value", "Status"])

        metas = [
            _make_meta(idx=1, df=data_df, start_page=1, is_data_orphan=True, is_headerless=True),
            _make_meta(idx=3, df=header_df, start_page=1, is_header_orphan=True),
        ]
        cfg = MultiPageConfig()
        results = merge_multipage_tables(metas, cfg)

        merged = results[0] if len(results) == 1 else None
        if merged is not None:
            col_names = [str(c).lower() for c in merged.df.columns]
            assert "name" in col_names or "value" in col_names


# ---------------------------------------------------------------------------
# Bug 4: Unused config options (require_same_width, header_sim_loose)
# ---------------------------------------------------------------------------

class TestRequireSameWidth:
    def test_blocks_different_width_merge(self):
        """require_same_width should block merges when widths differ."""
        df3 = pd.DataFrame({"Name": ["Alice"], "Status": ["OK"], "Age": ["30"]})
        df4 = pd.DataFrame({"Name": ["Bob"], "Status": ["OK"], "Age": ["25"], "Extra": ["x"]})
        metas = [
            _make_meta(idx=0, df=df3, start_page=1),
            _make_meta(idx=1, df=df4, start_page=2),
        ]
        cfg_loose = MultiPageConfig(require_same_width=False)
        results_loose = merge_multipage_tables(metas, cfg_loose)
        assert len(results_loose) == 1

        cfg_strict = MultiPageConfig(require_same_width=True)
        results_strict = merge_multipage_tables(metas, cfg_strict)
        assert len(results_strict) == 2


class TestHeaderSimLoose:
    def test_loose_with_layout_merges(self):
        df_a = pd.DataFrame({"Name": [1], "Status": [2], "Extra": [3]})
        df_b = pd.DataFrame({"Name": [4], "Status": [5], "Other": [6]})

        metas = [
            _make_meta(idx=0, df=df_a, start_page=1, vert_bottom=0.95),
            _make_meta(idx=1, df=df_b, start_page=2, vert_top=0.05),
        ]
        cfg = MultiPageConfig(
            header_sim_strict=0.6,
            header_sim_loose=0.3,
            use_layout_hint=True,
            bottom_band_min=0.6,
            top_band_max=0.4,
        )
        results = merge_multipage_tables(metas, cfg)
        assert len(results) == 1

    def test_loose_without_layout_does_not_merge(self):
        df_a = pd.DataFrame({"Name": [1], "Status": [2], "Extra": [3]})
        df_b = pd.DataFrame({"Name": [4], "Status": [5], "Other": [6]})

        metas = [
            _make_meta(idx=0, df=df_a, start_page=1),
            _make_meta(idx=1, df=df_b, start_page=2),
        ]
        cfg = MultiPageConfig(
            header_sim_strict=0.6,
            header_sim_loose=0.3,
            use_layout_hint=True,
        )
        results = merge_multipage_tables(metas, cfg)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------

class TestUtilities:
    def test_jaccard_identical(self):
        assert jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_jaccard_disjoint(self):
        assert jaccard({"a"}, {"b"}) == 0.0

    def test_jaccard_empty(self):
        assert jaccard(set(), set()) == 0.0

    def test_tokenize(self):
        assert tokenize("Hello World 123") == {"hello", "world"}

    def test_is_numeric_like_colnames(self):
        assert is_numeric_like_colnames(["0", "1", "2"]) is True
        assert is_numeric_like_colnames(["Name", "Age"]) is False
        assert is_numeric_like_colnames(["Unnamed: 0", "Unnamed: 1"]) is True


class TestUnionFind:
    def test_basic_union_find(self):
        uf = UnionFind(5)
        uf.union(0, 1)
        uf.union(2, 3)
        assert uf.find(0) == uf.find(1)
        assert uf.find(2) == uf.find(3)
        assert uf.find(0) != uf.find(2)

    def test_transitive(self):
        uf = UnionFind(3)
        uf.union(0, 1)
        uf.union(1, 2)
        assert uf.find(0) == uf.find(2)


# ---------------------------------------------------------------------------
# Merge decision signal tests (inspired by debug_merger.py pairwise analysis)
# ---------------------------------------------------------------------------

class TestMergeDecisionSignals:
    """
    Test each merge signal path independently to ensure the correct
    decision is made for each type of adjacent pair.
    """

    def test_headerless_width_match_merges(self):
        """Headerless fragment with same width → merge via width match."""
        df = pd.DataFrame({"Name": ["Alice"], "Age": ["30"], "Status": ["OK"]})
        metas = [
            _make_meta(idx=0, df=df, start_page=1),
            _make_meta(idx=1, df=df, start_page=2, is_headerless=True),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig())
        assert len(results) == 1

    def test_headerless_width_mismatch_no_merge(self):
        """Headerless fragment with very different width → no merge."""
        df3 = pd.DataFrame({"A": [1], "B": [2], "C": [3]})
        df7 = pd.DataFrame({f"C{i}": [i] for i in range(7)})
        metas = [
            _make_meta(idx=0, df=df3, start_page=1),
            _make_meta(idx=1, df=df7, start_page=2, is_headerless=True),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig(max_width_difference=2))
        assert len(results) == 2

    def test_repeated_header_merges(self):
        """Same headers on consecutive pages → merge."""
        df_a = pd.DataFrame({"Name": ["Alice"], "Age": ["30"]})
        df_b = pd.DataFrame({"Name": ["Bob"], "Age": ["25"]})
        metas = [
            _make_meta(idx=0, df=df_a, start_page=1),
            _make_meta(idx=1, df=df_b, start_page=2),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig())
        assert len(results) == 1

    def test_different_headers_no_merge(self):
        """Different headers on consecutive pages → separate tables."""
        df_a = pd.DataFrame({"Name": ["Alice"], "Age": ["30"]})
        df_b = pd.DataFrame({"Product": ["Widget"], "Price": ["9.99"]})
        metas = [
            _make_meta(idx=0, df=df_a, start_page=1),
            _make_meta(idx=1, df=df_b, start_page=2),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig())
        assert len(results) == 2

    def test_spillover_one_column_merges(self):
        """1-column headerless fragment after multi-column table → spillover merge."""
        df_main = pd.DataFrame({"Name": ["Alice"], "Ref": ["link1"], "Notes": ["n1"]})
        df_spill = pd.DataFrame({"Column_0": ["https://continued.url"]})
        metas = [
            _make_meta(idx=0, df=df_main, start_page=1),
            _make_meta(idx=1, df=df_spill, start_page=2, is_headerless=True),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig())
        assert len(results) == 1

    def test_page_gap_too_large_no_merge(self):
        """Tables more than max_page_gap apart → no merge."""
        df = pd.DataFrame({"A": [1], "B": [2]})
        metas = [
            _make_meta(idx=0, df=df, start_page=1),
            _make_meta(idx=1, df=df, start_page=5, is_headerless=True),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig(max_page_gap=1))
        assert len(results) == 2

    def test_same_page_no_merge(self):
        """Two tables on the same page → no merge (page_gap < 1)."""
        df = pd.DataFrame({"A": [1], "B": [2]})
        metas = [
            _make_meta(idx=0, df=df, start_page=3),
            _make_meta(idx=1, df=df, start_page=3, is_headerless=True),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig())
        assert len(results) == 2

    def test_three_page_chain_merges(self):
        """Table spanning 3 consecutive pages merges transitively."""
        df = pd.DataFrame({"X": ["a"], "Y": ["b"]})
        metas = [
            _make_meta(idx=0, df=df, start_page=1),
            _make_meta(idx=1, df=df, start_page=2, is_headerless=True),
            _make_meta(idx=2, df=df, start_page=3, is_headerless=True),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig())
        assert len(results) == 1
        assert len(results[0].members) == 3

    def test_skipped_table_blocks_false_merge(self):
        """If a table between two fragments was skipped, they should not merge."""
        df = pd.DataFrame({"Name": ["x"], "Age": ["1"]})
        # idx=0 and idx=2 extracted, idx=1 was skipped during extraction.
        # Even though both are on consecutive pages, the gap in idx means
        # an unknown table sits between them — merging would be unsafe.
        metas = [
            _make_meta(idx=0, df=df, start_page=1),
            _make_meta(idx=2, df=df, start_page=2, is_headerless=True),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig())
        assert len(results) == 2

    def test_contiguous_indices_still_merge(self):
        """Contiguous indices (no gap) should still merge normally."""
        df = pd.DataFrame({"Name": ["x"], "Age": ["1"]})
        metas = [
            _make_meta(idx=3, df=df, start_page=1),
            _make_meta(idx=4, df=df, start_page=2, is_headerless=True),
        ]
        results = merge_multipage_tables(metas, MultiPageConfig())
        assert len(results) == 1


# ---------------------------------------------------------------------------
# stitch_split_cells — post-merge row folding
#
# Triggered after a multi-page merge when a row has only one non-empty cell;
# that content is treated as continuation of the previous row and folded in.
# ---------------------------------------------------------------------------

class TestStitchSplitCells:

    def test_single_nonempty_cell_folds_into_previous_row(self):
        df = pd.DataFrame([
            ["A-1", "Alpha", "first line"],
            ["", "", "second line"],  # only col 2 populated → continuation
        ], columns=["ID", "Name", "Notes"])
        out = stitch_split_cells(df)
        assert out.shape == (1, 3)
        assert out.iloc[0, 2] == "first line\nsecond line"

    def test_two_consecutive_continuation_rows_both_fold(self):
        df = pd.DataFrame([
            ["A-1", "Alpha", "line1"],
            ["", "", "line2"],
            ["", "", "line3"],
        ], columns=["ID", "Name", "Notes"])
        out = stitch_split_cells(df)
        assert out.shape == (1, 3)
        assert out.iloc[0, 2] == "line1\nline2\nline3"

    def test_custom_separator_is_respected(self):
        df = pd.DataFrame([
            ["A-1", "Alpha", "first"],
            ["", "", "second"],
        ], columns=["ID", "Name", "Notes"])
        out = stitch_split_cells(df, separator=" | ")
        assert out.iloc[0, 2] == "first | second"

    def test_row_with_two_nonempty_cells_is_not_folded(self):
        df = pd.DataFrame([
            ["A-1", "Alpha", "first"],
            ["A-2", "", "second"],  # 2 non-empty cells → not a continuation
        ], columns=["ID", "Name", "Notes"])
        out = stitch_split_cells(df)
        assert out.shape == (2, 3)

    def test_single_row_df_returned_unchanged(self):
        df = pd.DataFrame([["x", "y", "z"]], columns=["A", "B", "C"])
        out = stitch_split_cells(df)
        assert out.shape == (1, 3)
        assert out.iloc[0].tolist() == ["x", "y", "z"]

    def test_empty_df_returned_unchanged(self):
        df = pd.DataFrame(columns=["A", "B", "C"])
        out = stitch_split_cells(df)
        assert out.shape == (0, 3)

    def test_url_continuation_routes_to_url_named_column(self):
        # When the continuation cell contains a URL and there's a column
        # named for links/refs, the URL lands there even if it originally
        # appeared under a different column.
        df = pd.DataFrame([
            ["A-1", "Alpha", "prev-link"],
            ["", "https://example.com/continuation", ""],
        ], columns=["ID", "Name", "Link"])
        out = stitch_split_cells(df)
        assert out.shape == (1, 3)
        assert "https://example.com/continuation" in out.iloc[0, 2]

    def test_continuation_into_empty_previous_cell(self):
        df = pd.DataFrame([
            ["A-1", "Alpha", ""],        # previous row's Notes is empty
            ["", "", "continuation"],
        ], columns=["ID", "Name", "Notes"])
        out = stitch_split_cells(df)
        assert out.shape == (1, 3)
        assert out.iloc[0, 2] == "continuation"
