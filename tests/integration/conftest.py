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

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config, items):
    """
    Auto-tag every test under tests/integration/ with the `integration`
    marker. The default addopts in pyproject excludes this marker, so the
    heavy OCR-dependent suite is opt-in via `pytest -m integration`.
    """
    integration_dir = Path(__file__).parent.resolve()
    mark = pytest.mark.integration
    for item in items:
        if Path(item.fspath).resolve().is_relative_to(integration_dir):
            item.add_marker(mark)


# ---------------------------------------------------------------------------
# Session-level caches
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docling_converter():
    """One DocumentConverter per session — model load is expensive."""
    from docling.document_converter import DocumentConverter

    return DocumentConverter()


@pytest.fixture(scope="session")
def doc_cache() -> dict[Path, Any]:
    """Cache converted DoclingDocuments by PDF path within a session."""
    return {}


def _convert(pdf_path: Path, converter, cache: dict[Path, Any]):
    pdf_path = pdf_path.resolve()
    if pdf_path not in cache:
        cache[pdf_path] = converter.convert(str(pdf_path)).document
    return cache[pdf_path]


def _copy_doc(doc: Any) -> Any:
    """Deep-copy a Docling document without assuming one exact Docling version."""
    if hasattr(doc, "model_copy"):
        return doc.model_copy(deep=True)
    if hasattr(doc, "copy"):
        return doc.copy(deep=True)
    return copy.deepcopy(doc)


def _ref_pointer(ref_obj: Any) -> str:
    if hasattr(ref_obj, "ref"):
        return ref_obj.ref
    if hasattr(ref_obj, "cref"):
        return ref_obj.cref
    if hasattr(ref_obj, "model_dump"):
        data = ref_obj.model_dump(by_alias=True)
        return data.get("$ref", "")
    if isinstance(ref_obj, dict):
        return ref_obj.get("$ref", "")
    return ""


def _body_table_refs(doc: Any) -> list[str]:
    refs: list[str] = []

    def visit(node: Any):
        for child_ref in getattr(node, "children", []) or []:
            ptr = _ref_pointer(child_ref)
            if ptr.startswith("#/tables/"):
                refs.append(ptr)
            elif ptr.startswith("#/groups/"):
                try:
                    group_idx = int(ptr.split("/")[-1])
                    groups = getattr(doc, "groups", []) or []
                    if group_idx < len(groups):
                        visit(groups[group_idx])
                except (ValueError, IndexError):
                    continue

    if getattr(doc, "body", None) is not None:
        visit(doc.body)
    return refs


def _header_texts(table: Any) -> list[str]:
    data = getattr(table, "data", None)
    grid = getattr(data, "grid", None) if data else None
    if not grid:
        return []
    header_texts: list[str] = []
    for row in grid:
        if row and any(getattr(c, "column_header", False) for c in row if c):
            header_texts.extend(str(getattr(c, "text", "")) for c in row if c)
        else:
            break
    return header_texts


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


def discover_fixtures() -> list[FixtureCase]:
    cases = []
    for yaml_path in sorted(FIXTURES_DIR.rglob("*.expected.yaml")):
        pdf_path = yaml_path.parent / yaml_path.name.replace(".expected.yaml", ".pdf")
        if not pdf_path.exists():
            raise FileNotFoundError(f"Expected YAML {yaml_path} has no sibling PDF at {pdf_path}")
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
            assert actual_first == exp_first, f"{ctx}: first_row {actual_first} != {exp_first}"

        if "last_row" in exp and lt.df.shape[0] > 0:
            actual_last = [_normalize_cell(v) for v in lt.df.iloc[-1].tolist()]
            exp_last = [str(v) for v in exp["last_row"]]
            assert actual_last == exp_last, f"{ctx}: last_row {actual_last} != {exp_last}"


def assert_public_stitch_injects_docling_doc(
    pdf_path: Path,
    expected_path: Path,
    converter,
    cache,
) -> None:
    """
    Run the public stitch_tables() API and assert the resulting DoclingDocument
    reflects the expected merged tables, not just the parser-neutral DataFrames.
    """
    from table_stitcher import MultiPageConfig, stitch_tables

    spec = yaml.safe_load(expected_path.read_text())
    cfg = MultiPageConfig(**(spec.get("config") or {}))
    merged_specs = [exp for exp in spec["logical_tables"] if len(exp.get("members", [])) > 1]
    if not merged_specs:
        pytest.skip("fixture has no merged logical tables to inject")

    original_doc = _convert(pdf_path, converter, cache)
    doc = _copy_doc(original_doc)
    original_headers = {
        exp["members"][0]: _header_texts(doc.tables[exp["members"][0]]) for exp in merged_specs
    }

    stitched = stitch_tables(doc, config=cfg, raise_on_error=True)
    body_refs = set(_body_table_refs(stitched))

    for exp in merged_specs:
        members = exp["members"]
        anchor_idx = members[0]
        anchor = stitched.tables[anchor_idx]
        ctx = f"public stitch for members={members}, pages={exp['pages']}"

        assert getattr(anchor.data, "num_rows", 0) > 0, f"{ctx}: anchor has no data"
        if "shape" in exp:
            # +1 or more for header rows; this guards that merged data was injected.
            assert anchor.data.num_rows >= exp["shape"][0] + 1, (
                f"{ctx}: anchor rows {anchor.data.num_rows} do not contain merged body"
            )

        if original_headers[anchor_idx]:
            assert (
                _header_texts(anchor)[: len(original_headers[anchor_idx])]
                == (original_headers[anchor_idx])
            ), f"{ctx}: anchor header text was not preserved"

        for satellite_idx in members[1:]:
            satellite = stitched.tables[satellite_idx]
            satellite_ref = f"#/tables/{satellite_idx}"
            assert satellite_ref not in body_refs, (
                f"{ctx}: satellite ref {satellite_ref} still appears in body tree"
            )
            assert satellite.data.num_rows == 0, (
                f"{ctx}: satellite {satellite_idx} data was not cleared"
            )
            assert satellite.data.num_cols == 0, (
                f"{ctx}: satellite {satellite_idx} columns were not cleared"
            )
            assert satellite.prov == [], (
                f"{ctx}: satellite {satellite_idx} provenance was not cleared"
            )
