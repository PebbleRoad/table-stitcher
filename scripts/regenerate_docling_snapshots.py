"""
Regenerate the *.docling.json snapshots used by the default integration test
lane. Each snapshot is the JSON-serialized output of running docling against
the fixture PDF.

The snapshot lane (default `pytest -m integration`) loads these JSONs and runs
table-stitcher against them — no PDF parsing, no OCR, no model downloads at
test time. That makes the suite deterministic across platforms regardless of
which OCR engine docling auto-selects (ocrmac on macOS, easyocr/tesseract on
Linux).

Usage (from repo root):
    python -m scripts.regenerate_docling_snapshots                # all fixtures
    python -m scripts.regenerate_docling_snapshots --only repeated-header
    python -m scripts.regenerate_docling_snapshots path/to/file.pdf
    python -m scripts.regenerate_docling_snapshots --force        # overwrite existing

Notes:
- Snapshots reflect the OCR output of whichever engine docling picked on the
  machine that ran this script. Regenerate from the same environment you'd
  expect a contributor to debug from (typically a maintainer's macOS box).
- Pair with `tests/integration/_tools/regenerate_expected.py` to refresh the
  YAML expectations after a snapshot regen — the YAMLs encode cell text from
  the snapshot, so the two must move together.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "integration" / "fixtures"


def _snapshot_path_for(pdf: Path) -> Path:
    return pdf.parent / (pdf.name[: -len(".pdf")] + ".docling.json")


def _discover_pdfs(only: str | None, explicit: list[Path]) -> list[Path]:
    if explicit:
        return [p.resolve() for p in explicit]
    pdfs = sorted(FIXTURES_DIR.rglob("*.pdf"))
    if only:
        pdfs = [p for p in pdfs if only in str(p.relative_to(FIXTURES_DIR))]
    return pdfs


def regenerate(pdfs: list[Path], force: bool) -> int:
    from docling.document_converter import DocumentConverter

    converter: DocumentConverter | None = None  # lazy: skip the load if nothing to do
    written = 0
    skipped = 0
    total_bytes = 0

    for pdf in pdfs:
        snap = _snapshot_path_for(pdf)
        rel = pdf.relative_to(REPO_ROOT) if pdf.is_relative_to(REPO_ROOT) else pdf

        if snap.exists() and not force:
            print(f"skip   {rel}  (snapshot exists; pass --force to overwrite)")
            skipped += 1
            continue

        if converter is None:
            print("loading docling models…", flush=True)
            converter = DocumentConverter()

        t0 = time.monotonic()
        doc = converter.convert(str(pdf)).document
        payload = doc.model_dump_json()  # no indent — these can get large
        snap.write_text(payload)
        size_kb = len(payload.encode("utf-8")) / 1024
        total_bytes += len(payload.encode("utf-8"))
        written += 1
        print(f"wrote  {rel}  → {snap.name} ({size_kb:.1f} KB, {time.monotonic() - t0:.1f}s)")

    print(
        f"\n{written} written, {skipped} skipped. "
        f"Total snapshot bytes (this run): {total_bytes / 1024:.1f} KB"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "pdfs",
        nargs="*",
        type=Path,
        help="Specific PDFs to regenerate. If omitted, all fixtures are processed.",
    )
    p.add_argument(
        "--only",
        type=str,
        default=None,
        help="Substring filter on relative fixture path (e.g. 'repeated-header').",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing snapshots. Default is to skip them.",
    )
    args = p.parse_args(argv)

    pdfs = _discover_pdfs(args.only, args.pdfs)
    if not pdfs:
        print("no PDFs matched", file=sys.stderr)
        return 1
    return regenerate(pdfs, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
