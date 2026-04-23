"""
Regenerate a fixture's expected.yaml by running the current pipeline against
its PDF and capturing the output.

Usage (from repo root):
    python -m tests.integration._tools.regenerate_expected \\
        tests/integration/fixtures/repeated-header/study-sample-7pg.pt2.pdf

Optional flags:
    --description "..."   Preserve/override the description. If omitted and the
                          YAML already exists, the old description is kept.
    --xfail "..."         Add/update an xfail marker (describes the known bug).
                          Omit to leave xfail unset (or to clear an existing one
                          on a now-passing case, pair with --clear-xfail).
    --clear-xfail         Remove any existing xfail marker.

Emits the regenerated YAML next to the PDF, preserving the `<slug>.<provenance>
.expected.yaml` convention. The YAML captures: description, config (defaults
unless overridden), logical_tables (members, pages, shape, columns,
first_row, last_row).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from docling.document_converter import DocumentConverter

from table_stitcher import MultiPageConfig, extract_table_meta
from table_stitcher.merger import merge_multipage_tables


def _cell(v) -> str:
    import pandas as pd
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


def _expected_path_for(pdf: Path) -> Path:
    # replace final ".pdf" with ".expected.yaml"
    return pdf.parent / (pdf.name[: -len(".pdf")] + ".expected.yaml")


def regenerate(pdf_path: Path,
               description: str | None = None,
               xfail: str | None = None,
               clear_xfail: bool = False) -> Path:
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    yaml_path = _expected_path_for(pdf_path)

    # Preserve old description / xfail if not explicitly overridden.
    old_description = ""
    old_xfail: str | None = None
    if yaml_path.exists():
        try:
            old = yaml.safe_load(yaml_path.read_text()) or {}
        except yaml.YAMLError as e:
            print(f"warning: existing YAML unreadable ({e}); proceeding fresh", file=sys.stderr)
            old = {}
        old_description = old.get("description", "") or ""
        old_xfail = old.get("xfail")

    effective_description = description if description is not None else old_description
    effective_xfail: str | None
    if clear_xfail:
        effective_xfail = None
    elif xfail is not None:
        effective_xfail = xfail
    else:
        effective_xfail = old_xfail

    cfg = MultiPageConfig()
    doc = DocumentConverter().convert(str(pdf_path)).document
    metas = extract_table_meta(doc, config=cfg)
    logicals = merge_multipage_tables(metas, cfg)

    entries = []
    for lt in sorted(logicals, key=lambda x: ((x.pages or [0])[0], (x.members or [0])[0])):
        e = {
            "members": list(lt.members),
            "pages": list(lt.pages),
            "shape": list(lt.df.shape),
            "columns": [str(c) for c in lt.df.columns],
        }
        if lt.df.shape[0] > 0:
            e["first_row"] = [_cell(v) for v in lt.df.iloc[0].tolist()]
            e["last_row"] = [_cell(v) for v in lt.df.iloc[-1].tolist()]
        entries.append(e)

    spec: dict = {"description": effective_description, "config": {}}
    if effective_xfail:
        spec["xfail"] = effective_xfail
    spec["logical_tables"] = entries

    yaml_path.write_text(yaml.safe_dump(
        spec, sort_keys=False, allow_unicode=True, width=100,
    ))
    return yaml_path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pdf", type=Path, help="Path to the fixture PDF")
    p.add_argument("--description", type=str, default=None,
                   help="Override the description (default: keep existing or blank)")
    p.add_argument("--xfail", type=str, default=None,
                   help="Set/update the xfail marker")
    p.add_argument("--clear-xfail", action="store_true",
                   help="Remove any existing xfail marker")
    args = p.parse_args(argv)

    out = regenerate(args.pdf, args.description, args.xfail, args.clear_xfail)
    print(f"wrote {out.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
