"""
Basic Pipeline: PDF -> DoclingDocument -> stitch_tables() -> export

Usage:
    python examples/basic_pipeline.py "your_report.pdf"
"""

import sys
import logging
from pathlib import Path
from docling.document_converter import DocumentConverter
from table_stitcher import stitch_tables, MultiPageConfig

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("BasicPipeline")


def run(pdf_filename: str):
    input_path = Path(pdf_filename)

    if not input_path.exists():
        log.error(f"File not found: {input_path}")
        sys.exit(1)

    # 1. Convert
    log.info(f"Converting: {input_path.name}")
    converter = DocumentConverter()
    doc = converter.convert(str(input_path)).document

    # 2. Stitch multi-page tables
    config = MultiPageConfig(
        max_page_gap=1,
        max_width_difference=2,
        header_sim_strict=0.6,
        row_sim_threshold=0.3,
        stitch_separator="\n",
    )
    doc = stitch_tables(doc, config=config)

    # 3. Verify JSON round-trip
    log.info("Testing JSON round-trip...")
    from docling_core.types.doc import DoclingDocument as DD
    json_str = doc.model_dump_json(exclude_none=True)
    reconstructed = DD.model_validate_json(json_str)
    log.info(f"Round-trip OK. Tables: {len(reconstructed.tables)}")

    # 4. Export
    json_path = input_path.with_suffix(".stitched.json")
    with open(json_path, "w") as f:
        f.write(doc.model_dump_json(exclude_none=True, indent=2))
    log.info(f"JSON: {json_path}")

    html_path = input_path.with_suffix(".stitched.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(doc.export_to_html())
    log.info(f"HTML: {html_path}")

    md_path = input_path.with_suffix(".stitched.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(doc.export_to_markdown())
    log.info(f"Markdown: {md_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python examples/basic_pipeline.py <pdf_file>")
        sys.exit(1)

    run(" ".join(sys.argv[1:]))
