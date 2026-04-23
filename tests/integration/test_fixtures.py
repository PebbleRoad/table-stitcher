"""
Integration tests parametrized over every fixture with an expected.yaml file.

Add a new category by creating tests/integration/fixtures/<category>/ with a
<slug>.<provenance>.pdf and its <slug>.<provenance>.expected.yaml — no Python
edit required.

A fixture YAML may declare `xfail: "<reason>"` at the top level; the parametrized
case is then marked xfail, so the assertion is allowed to fail (useful for
known-broken cases that document the intended behavior).
"""
from __future__ import annotations

import pytest
import yaml

from tests.integration.conftest import (
    FixtureCase,
    assert_stitched_matches,
    discover_fixtures,
)


def _params():
    for case in discover_fixtures():
        spec = yaml.safe_load(case.yaml_path.read_text()) or {}
        marks = []
        if spec.get("xfail"):
            marks.append(pytest.mark.xfail(reason=spec["xfail"], strict=True))
        yield pytest.param(case, id=case.id, marks=marks)


@pytest.mark.parametrize("case", list(_params()))
def test_fixture_stitches_as_expected(
    case: FixtureCase, docling_converter, doc_cache
) -> None:
    assert_stitched_matches(case.pdf_path, case.yaml_path, docling_converter, doc_cache)
