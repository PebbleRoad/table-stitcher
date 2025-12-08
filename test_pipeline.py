"""
Test Pipeline: Verifies that enriched documents serialize correctly.

This tests the full round-trip:
  PDF → DoclingDocument → enrich_document() → JSON/HTML serialization

Usage:
  python test_pipeline.py "your_report.pdf"
"""
import sys
import logging
from pathlib import Path
from docling.document_converter import DocumentConverter
from docling_table_enricher import enrich_document, MultiPageConfig

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("TestPipeline")


def run_test(pdf_filename: str):
    input_path = Path(pdf_filename)
    
    if not input_path.exists():
        log.error(f"File not found: {input_path}")
        sys.exit(1)

    # 1. Convert
    log.info(f"Converting: {input_path.name}")
    converter = DocumentConverter()
    doc = converter.convert(str(input_path)).document 

    # 2. Enrich
    config = MultiPageConfig(
        max_page_gap=1,
        max_width_difference=2,
        header_sim_strict=0.6,
        row_sim_threshold=0.3,
        stitch_separator="\n"
    )
    doc = enrich_document(doc, config=config)

    # 3. Verify JSON serialization works
    log.info("Testing JSON serialization...")
    doc_dict = doc.model_dump(exclude_none=True)
    
    # 4. Find and report merged tables (tables with multi-page provenance)
    merged_tables = []
    for i, tbl in enumerate(doc_dict.get("tables", [])):
        prov = tbl.get("prov", [])
        if isinstance(prov, list) and len(prov) > 1:
            pages = sorted(set(p.get("page_no", 0) for p in prov))
            if len(pages) > 1:
                merged_tables.append({
                    "index": i,
                    "pages": pages,
                    "rows": tbl["data"]["num_rows"],
                    "cols": tbl["data"]["num_cols"],
                    "ref": tbl.get("self_ref", "")
                })
    
    # 5. Report results
    if merged_tables:
        log.info(f"[OK] Found {len(merged_tables)} merged table(s) in JSON output:")
        for mt in merged_tables:
            log.info(f"  - Table {mt['index']}: pages {mt['pages']}, "
                     f"{mt['rows']} rows x {mt['cols']} cols")
    else:
        log.warning("[WARN] No merged tables found in JSON output.")

    # 6. Verify round-trip: JSON -> DoclingDocument
    try:
        from docling_core.types.doc import DoclingDocument as DD
        json_str = doc.model_dump_json(exclude_none=True)
        reconstructed = DD.model_validate_json(json_str)
        log.info(f"[OK] JSON round-trip successful. "
                 f"Tables: {len(reconstructed.tables)}")
    except Exception as e:
        log.error(f"[FAIL] JSON round-trip failed: {e}")

    # 7. Save full JSON for inspection
    json_path = input_path.with_suffix(".enriched.json")
    with open(json_path, "w") as f:
        f.write(doc.model_dump_json(exclude_none=True, indent=2))
    log.info(f"Full JSON saved to: {json_path}")
    
    # 8. Export to HTML using Docling's native serialization
    html_path = input_path.with_suffix(".enriched.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(doc.export_to_html())
    log.info(f"HTML saved to: {html_path}")
    
    # 9. Export to Markdown for comparison
    md_path = input_path.with_suffix(".enriched.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(doc.export_to_markdown())
    log.info(f"Markdown saved to: {md_path}")
    
    return doc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_pipeline.py <pdf_file>")
        sys.exit(1)
    
    run_test(" ".join(sys.argv[1:]))