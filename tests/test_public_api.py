"""
Public-API stability tests.

`__all__` in `table_stitcher/__init__.py` is the contract with downstream
users. These tests fail loudly when the contract drifts:

- A symbol is removed or renamed without a deprecation cycle.
- `__all__` lists a name that doesn't actually resolve (typo, dead re-export).
- A function in the public surface gets its signature changed in a
  backwards-incompatible way (positional args removed/reordered).

This is not a behavioral test — it's a structural smoke test. Behavioral
coverage lives in `test_merger.py` and the integration suite.
"""

from __future__ import annotations

import inspect

import table_stitcher

# Frozen snapshot of the public surface. Adding a name here is fine;
# removing or renaming one is a breaking change and requires a major
# version bump + CHANGELOG entry.
EXPECTED_PUBLIC_API = {
    "stitch_tables",
    "extract_table_meta",
    "merge_multipage_tables",
    "TableStitcher",
    "MultiPageConfig",
    "LogicalTable",
    "TableMeta",
    "MergeTrace",
    "StitchingError",
    "TableStitcherAdapter",
    "__version__",
}


def test_all_matches_expected_surface():
    """`__all__` is exactly the set we've committed to."""
    assert set(table_stitcher.__all__) == EXPECTED_PUBLIC_API


def test_every_exported_name_resolves():
    """Every name in `__all__` actually exists on the module."""
    missing = [name for name in table_stitcher.__all__ if not hasattr(table_stitcher, name)]
    assert not missing, f"names in __all__ that don't resolve: {missing}"


def test_version_is_pep440_ish():
    """`__version__` is a non-empty string. Sanity check, not a strict PEP 440 parse."""
    assert isinstance(table_stitcher.__version__, str)
    assert table_stitcher.__version__
    assert table_stitcher.__version__[0].isdigit()


def test_stitch_tables_signature():
    """
    `stitch_tables(doc, config=None, raise_on_error=False)` is the documented
    entry point. Reordering or renaming these parameters breaks every existing
    caller silently — pin the signature.
    """
    sig = inspect.signature(table_stitcher.stitch_tables)
    params = list(sig.parameters)
    assert params == ["doc", "config", "raise_on_error"], params


def test_extract_table_meta_signature():
    sig = inspect.signature(table_stitcher.extract_table_meta)
    params = list(sig.parameters)
    assert params == ["doc", "config"], params


def test_table_stitcher_class_signature():
    """`TableStitcher(adapter, config=None)` — the parser-agnostic entry point."""
    sig = inspect.signature(table_stitcher.TableStitcher)
    params = list(sig.parameters)
    assert params == ["adapter", "config"], params


def test_stitching_error_is_exception():
    assert issubclass(table_stitcher.StitchingError, Exception)


def test_last_logical_tables_exposed_after_stitch():
    """`TableStitcher.last_logical_tables` surfaces the merge results.

    `stitch()` returns only the document, but grounding consumers need the
    per-table merge results — notably `LogicalTable.row_pages`, filled in
    during injection. The stitcher keeps its most recent results reachable.
    """
    import pandas as pd

    from table_stitcher.merger import is_numeric_like_colnames, tokenize
    from table_stitcher.models import TableMeta

    def _meta(idx: int, start_page: int, *, headerless: bool) -> TableMeta:
        df = pd.DataFrame({"Name": ["Alice"], "Age": ["30"]})
        return TableMeta(
            idx=idx,
            df=df,
            start_page=start_page,
            pages=[start_page],
            width=df.shape[1],
            header_tokens=tokenize(" ".join(str(c) for c in df.columns)),
            first_row_tokens=tokenize(" ".join(str(x) for x in df.iloc[0].tolist())),
            raw_columns=[str(c) for c in df.columns],
            vert_center=None,
            vert_top=None,
            vert_bottom=None,
            is_header_orphan=False,
            is_data_orphan=False,
            numeric_like_cols=is_numeric_like_colnames([str(c) for c in df.columns]),
            row_count=df.shape[0],
            is_headerless=headerless,
        )

    class _StubAdapter:
        def extract(self, doc, config):
            return [_meta(0, 1, headerless=False), _meta(1, 2, headerless=True)]

        def inject(self, doc, logical_tables):
            for lt in logical_tables:
                lt.row_pages = {0: 1}
            return doc

    stitcher = table_stitcher.TableStitcher(_StubAdapter())
    assert stitcher.last_logical_tables == []

    stitcher.stitch(object())

    assert len(stitcher.last_logical_tables) == 1
    lt = stitcher.last_logical_tables[0]
    assert lt.members == [0, 1]
    assert lt.row_pages == {0: 1}
