"""
System Controller: Shows how table-stitcher fits into a multi-step pipeline.

Usage:
    python examples/system_controller.py "your_report.pdf"
"""

import logging
from pathlib import Path

from docling.document_converter import DocumentConverter
from docling_core.types.doc import DoclingDocument

from table_stitcher import MultiPageConfig, stitch_tables

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("SystemController")


def step_1_base_conversion(pdf_path: Path) -> DoclingDocument:
    """Initial conversion from PDF to DoclingDocument."""
    log.info("--- Step 1: Base Conversion ---")
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    return result.document


def step_2_custom_extraction(doc: DoclingDocument) -> DoclingDocument:
    """Your custom table extraction logic (placeholder)."""
    log.info("--- Step 2: Custom Table Extraction ---")
    # doc = my_custom_extractor.process(doc)
    return doc


def step_3_multipage_stitch(doc: DoclingDocument) -> DoclingDocument:
    """
    Multi-page table stitching: detects and merges tables split
    across page boundaries.
    """
    log.info("--- Step 3: Multi-Page Table Stitching ---")

    config = MultiPageConfig(
        max_page_gap=1,
        max_width_difference=2,
        header_sim_strict=0.6,
        row_sim_threshold=0.3,
        stitch_separator="\n",
    )

    doc = stitch_tables(doc, config=config)
    return doc


def run_pipeline(pdf_path: str) -> DoclingDocument:
    """Main pipeline: PDF -> DoclingDocument -> Stitched DoclingDocument"""
    file_path = Path(pdf_path)

    doc = step_1_base_conversion(file_path)
    doc = step_2_custom_extraction(doc)
    doc = step_3_multipage_stitch(doc)

    log.info("--- Pipeline Complete ---")
    return doc


def run_pipeline_and_save(pdf_path: str):
    """Convenience: run pipeline and save to Markdown."""
    file_path = Path(pdf_path)
    doc = run_pipeline(pdf_path)

    output_path = file_path.with_suffix(".stitched.md")
    with open(output_path, "w") as f:
        f.write(doc.export_to_markdown())
    print(f"Saved to {output_path}")

    return doc


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python examples/system_controller.py <pdf_file>")
        sys.exit(1)

    run_pipeline_and_save(" ".join(sys.argv[1:]))
