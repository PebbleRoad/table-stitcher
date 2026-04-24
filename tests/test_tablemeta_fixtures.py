"""
Parser-neutral merger fixtures.

These tests exercise the core TableMeta contract directly, without Docling,
OCR, PDF parsing, or model downloads. They are the fast compatibility suite
for future adapters.
"""

from pathlib import Path

import pandas as pd
import pytest
import yaml

from table_stitcher import MultiPageConfig, TableMeta, merge_multipage_tables
from table_stitcher.merger import (
    first_row_has_number,
    is_numeric_like_colnames,
    normalize_col_name,
    tokenize,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "tablemeta"


def _make_meta(spec: dict) -> TableMeta:
    df = pd.DataFrame(spec.get("rows", []), columns=spec.get("columns", []))
    header_tokens = set()
    for col in df.columns:
        header_tokens |= tokenize(normalize_col_name(col))

    first_row_tokens = set()
    if df.shape[0] > 0:
        first_row_tokens = tokenize(" ".join(str(v) for v in df.iloc[0].tolist()))

    page = spec.get("page")
    raw_columns = [str(c) for c in df.columns]
    return TableMeta(
        idx=spec["idx"],
        df=df,
        start_page=page,
        pages=[page] if page is not None else [],
        width=df.shape[1],
        header_tokens=header_tokens,
        first_row_tokens=first_row_tokens,
        raw_columns=raw_columns,
        vert_center=spec.get("vert_center"),
        vert_top=spec.get("vert_top"),
        vert_bottom=spec.get("vert_bottom"),
        is_header_orphan=spec.get("is_header_orphan", False),
        is_data_orphan=spec.get("is_data_orphan", first_row_has_number(df)),
        numeric_like_cols=is_numeric_like_colnames(raw_columns),
        row_count=df.shape[0],
        continuation_content=spec.get("continuation_content", []),
        is_headerless=spec.get("is_headerless", False),
    )


def _fixture_paths():
    return sorted(FIXTURES_DIR.glob("*.yaml"))


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
def test_tablemeta_fixture(fixture_path: Path):
    spec = yaml.safe_load(fixture_path.read_text())
    cfg = MultiPageConfig(**(spec.get("config") or {}))
    metas = [_make_meta(t) for t in spec["tables"]]

    actual = merge_multipage_tables(metas, cfg)
    expected = spec["expected"]["logical_tables"]

    assert len(actual) == len(expected)
    for lt, exp in zip(actual, expected):
        assert lt.members == exp["members"]
        assert lt.pages == exp["pages"]
        assert list(lt.df.shape) == exp["shape"]
        if "merge_reason" in exp:
            assert lt.merge_reason == exp["merge_reason"]
        if "columns" in exp:
            assert [str(c) for c in lt.df.columns] == exp["columns"]
        if "last_row" in exp:
            assert [str(v) for v in lt.df.iloc[-1].tolist()] == [
                str(v) for v in exp["last_row"]
            ]
