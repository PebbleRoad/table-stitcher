"""
Shared machinery for integration tests: PDF → stitched DataFrames → compare
against a YAML description of the expected merge.

Fixtures live under tests/integration/fixtures/<category>/<slug>.<provenance>.pdf
paired with <slug>.<provenance>.expected.yaml. Tests discover and parametrize
over every expected.yaml in the tree (see test_fixtures.py).

Docling conversion is expensive (model load + per-PDF parse), so the converter
and each converted document are session-cached.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Session-level caches
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def docling_converter():
    """One DocumentConverter per session — model load is expensive."""
    from docling.document_converter import DocumentConverter
    return DocumentConverter()


@pytest.fixture(scope="session")
def doc_cache() -> Dict[Path, Any]:
    """Cache converted DoclingDocuments by PDF path within a session."""
    return {}


def _convert(pdf_path: Path, converter, cache: Dict[Path, Any]):
    pdf_path = pdf_path.resolve()
    if pdf_path not in cache:
        cache[pdf_path] = converter.convert(str(pdf_path)).document
    return cache[pdf_path]


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FixtureCase:
    yaml_path: Path
    pdf_path: Path

    @property
    def id(self) -> str:
        # e.g. "repeated-header/4-page-substance-list.corp"
        rel = self.pdf_path.relative_to(FIXTURES_DIR)
        return str(rel.with_suffix(""))


def discover_fixtures() -> List[FixtureCase]:
    cases = []
    for yaml_path in sorted(FIXTURES_DIR.rglob("*.expected.yaml")):
        pdf_path = yaml_path.parent / yaml_path.name.replace(".expected.yaml", ".pdf")
        if not pdf_path.exists():
            raise FileNotFoundError(
                f"Expected YAML {yaml_path} has no sibling PDF at {pdf_path}"
            )
        cases.append(FixtureCase(yaml_path=yaml_path, pdf_path=pdf_path))
    return cases


# ---------------------------------------------------------------------------
# Assertion helper
# ---------------------------------------------------------------------------

def _normalize_cell(v: Any) -> str:
    """Stringify for comparison — YAML scalars and DataFrame cells must match."""
    import pandas as pd
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


def assert_stitched_matches(pdf_path: Path, expected_path: Path, converter, cache) -> None:
    """Run the pipeline on pdf_path and assert the merge matches expected.yaml."""
    from table_stitcher import MultiPageConfig, extract_table_meta
    from table_stitcher.merger import merge_multipage_tables

    spec = yaml.safe_load(expected_path.read_text())
    cfg_overrides = spec.get("config") or {}
    cfg = MultiPageConfig(**cfg_overrides)

    doc = _convert(pdf_path, converter, cache)
    metas = extract_table_meta(doc, config=cfg)
    logicals = merge_multipage_tables(metas, cfg)

    # Sort both sides by (first_page, first_member) for stable comparison.
    actual = sorted(
        logicals,
        key=lambda lt: ((lt.pages or [0])[0], (lt.members or [0])[0]),
    )
    expected = sorted(
        spec["logical_tables"],
        key=lambda e: (e["pages"][0], e["members"][0]),
    )

    assert len(actual) == len(expected), (
        f"Expected {len(expected)} logical tables, got {len(actual)}. "
        f"Actual pages: {[lt.pages for lt in actual]}"
    )

    for i, (lt, exp) in enumerate(zip(actual, expected)):
        ctx = f"logical table #{i} (expected members={exp['members']}, pages={exp['pages']})"

        assert lt.members == exp["members"], f"{ctx}: members {lt.members} != {exp['members']}"
        assert lt.pages == exp["pages"], f"{ctx}: pages {lt.pages} != {exp['pages']}"

        if "shape" in exp:
            assert list(lt.df.shape) == exp["shape"], (
                f"{ctx}: shape {list(lt.df.shape)} != {exp['shape']}"
            )

        if "columns" in exp:
            actual_cols = [str(c) for c in lt.df.columns]
            assert actual_cols == exp["columns"], (
                f"{ctx}: columns {actual_cols} != {exp['columns']}"
            )

        if "first_row" in exp and lt.df.shape[0] > 0:
            actual_first = [_normalize_cell(v) for v in lt.df.iloc[0].tolist()]
            exp_first = [str(v) for v in exp["first_row"]]
            assert actual_first == exp_first, (
                f"{ctx}: first_row {actual_first} != {exp_first}"
            )

        if "last_row" in exp and lt.df.shape[0] > 0:
            actual_last = [_normalize_cell(v) for v in lt.df.iloc[-1].tolist()]
            exp_last = [str(v) for v in exp["last_row"]]
            assert actual_last == exp_last, (
                f"{ctx}: last_row {actual_last} != {exp_last}"
            )
