import logging
from pathlib import Path
from docling.document_converter import DocumentConverter
from docling_core.types.doc import DoclingDocument

# Import your module
from docling_table_enricher import enrich_document, MultiPageConfig

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("SystemController")


def step_1_base_conversion(pdf_path: Path) -> DoclingDocument:
    """
    Initial conversion from PDF to DoclingDocument.
    """
    log.info("--- Step 1: Base Conversion ---")
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    return result.document


def step_2_custom_extraction(doc: DoclingDocument) -> DoclingDocument:
    """
    Your existing custom code that improves table extraction 
    on a per-page basis.
    """
    log.info("--- Step 2: Custom Table Extraction (Mock) ---")
    # ... Your custom logic here ...
    # doc = my_custom_extractor.process(doc)
    return doc


def step_3_multipage_merge(doc: DoclingDocument) -> DoclingDocument:
    """
    Multi-page table enrichment: Detects and merges tables that were 
    split across page boundaries by Docling.
    
    Handles three scenarios:
    1. Normal continuation: Headerless fragment with same column count
    2. Spillover content: 1-column fragment (URLs/tickets) stitched into last cell
    3. Repeated headers: Same headers on next page = continuation
    
    Returns the DoclingDocument with merged tables.
    """
    log.info("--- Step 3: Multi-Page Table Enrichment ---")
    
    config = MultiPageConfig(
        # Page adjacency: Only merge tables on consecutive pages
        max_page_gap=1,
        
        # Width tolerance: How much column count can differ and still merge
        # Set to 0 for strict matching, higher for more lenient merging
        max_width_difference=2,
        
        # Header similarity: Threshold for "repeated header" detection
        # Only used when the continuation fragment HAS headers
        header_sim_strict=0.6,
        
        # Row similarity: Fallback threshold when width doesn't match exactly
        # Used for edge cases like split cells
        row_sim_threshold=0.3,
        
        # Cell stitching: Character used to join split content
        stitch_separator="\n"
        
        # Note: Geometry settings (bottom_band_min, top_band_max) are defined
        # but have no effect because Docling doesn't provide bbox data.
        # The merger relies on width matching and document order instead.
    )
    
    doc = enrich_document(doc, config=config)
    return doc


def run_pipeline(pdf_path: str) -> DoclingDocument:
    """
    Main pipeline: PDF → DoclingDocument → Enriched DoclingDocument
    
    Returns the enriched DoclingDocument for further processing.
    """
    file_path = Path(pdf_path)
    
    # The payload stays as a DoclingDocument object throughout the chain
    doc = step_1_base_conversion(file_path)
    doc = step_2_custom_extraction(doc)
    doc = step_3_multipage_merge(doc)
    
    log.info("--- Pipeline Complete ---")
    return doc


def run_pipeline_and_save(pdf_path: str):
    """
    Convenience function: Runs pipeline and saves to Markdown file.
    """
    file_path = Path(pdf_path)
    doc = run_pipeline(pdf_path)
    
    # Save output
    output_path = file_path.with_suffix(".enriched.md")
    with open(output_path, "w") as f:
        f.write(doc.export_to_markdown())
    print(f"Saved to {output_path}")
    
    return doc


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python system_controller.py <pdf_file>")
        sys.exit(1)
    
    run_pipeline_and_save(" ".join(sys.argv[1:]))