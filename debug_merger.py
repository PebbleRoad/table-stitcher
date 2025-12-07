"""
Debug script to see exactly what the merger detects for each table fragment.
Run with: python debug_merger.py <your_pdf.pdf>
"""
import sys
from pathlib import Path
from docling.document_converter import DocumentConverter

# Add parent to path if needed
sys.path.insert(0, str(Path(__file__).parent))

from docling_table_enricher.models import MultiPageConfig
from docling_table_enricher.merger import extract_table_meta, jaccard, layout_suggests_continuation

def debug_extraction(pdf_path: str):
    # Convert to absolute path
    input_path = Path(pdf_path).resolve()
    
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)
    
    print(f"\n{'='*70}")
    print(f"DEBUGGING: {input_path.name}")
    print(f"{'='*70}\n")
    
    # Convert
    converter = DocumentConverter()
    doc = converter.convert(str(input_path)).document
    
    # Use same config as your inspect_results.py
    config = MultiPageConfig(
        max_page_gap=1,
        bottom_band_min=0.10,
        top_band_max=0.90,
        header_sim_strict=0.6,
        header_sim_loose=0.5,
        max_width_difference=2,
        stitch_separator="\n"
    )
    
    # Extract metadata
    tables_meta = extract_table_meta(doc, config)
    
    print(f"Found {len(tables_meta)} table fragments:\n")
    
    for t in tables_meta:
        print(f"{'─'*70}")
        print(f"TABLE INDEX: {t.idx}")
        print(f"  Pages:           {t.pages} (start: {t.start_page})")
        print(f"  Columns:         {t.width}")
        print(f"  Rows:            {t.row_count}")
        print(f"  Raw Headers:     {t.raw_columns[:5]}{'...' if len(t.raw_columns) > 5 else ''}")
        print(f"  Header Tokens:   {t.header_tokens}")
        print(f"  1st Row Tokens:  {t.first_row_tokens}")
        print(f"  ")
        print(f"  is_headerless:   {t.is_headerless}")
        print(f"  is_header_orphan:{t.is_header_orphan}")
        print(f"  is_data_orphan:  {t.is_data_orphan}")
        print(f"  ")
        print(f"  Geometry (0=top, 1=bottom):")
        print(f"    vert_top:      {t.vert_top:.3f}" if t.vert_top is not None else f"    vert_top:      None")
        print(f"    vert_bottom:   {t.vert_bottom:.3f}" if t.vert_bottom is not None else f"    vert_bottom:   None")
        print(f"    vert_center:   {t.vert_center:.3f}" if t.vert_center is not None else f"    vert_center:   None")
        print(f"  continuation:    {t.continuation_content}")
        print()
        
        # Show first few rows of actual data
        if t.df.shape[0] > 0:
            print(f"  First 3 rows of data:")
            for i, row in t.df.head(3).iterrows():
                print(f"    {i}: {row.tolist()}")
        print()
    
    # Now show potential merge pairs
    print(f"\n{'='*70}")
    print("MERGE ANALYSIS (adjacent page pairs)")
    print(f"{'='*70}\n")
    
    for i, tA in enumerate(tables_meta):
        for j, tB in enumerate(tables_meta):
            if tA.start_page is None or tB.start_page is None:
                continue
            if tB.start_page - tA.start_page != 1:
                continue
                
            # This is an adjacent pair
            header_sim = jaccard(tA.header_tokens, tB.header_tokens)
            row_sim = jaccard(tA.first_row_tokens, tB.first_row_tokens)
            layout_ok = layout_suggests_continuation(tA, tB, config)
            width_ok = abs(tA.width - tB.width) <= config.max_width_difference
            
            print(f"Pair: Table {tA.idx} (pg {tA.start_page}) → Table {tB.idx} (pg {tB.start_page})")
            print(f"  tB.is_headerless:  {tB.is_headerless}")
            print(f"  header_sim:        {header_sim:.2f} (threshold: {config.header_sim_strict})")
            print(f"  row_sim:           {row_sim:.2f} (threshold: {config.row_sim_threshold})")
            print(f"  layout_ok:         {layout_ok}")
            print(f"  width_ok:          {width_ok} ({tA.width} vs {tB.width})")
            
            # Would it merge?
            would_merge = False
            reason = ""
            if tB.is_headerless:
                if layout_ok:
                    would_merge, reason = True, "tail_merge_layout"
                elif row_sim >= config.row_sim_threshold:
                    would_merge, reason = True, "tail_merge_content"
                else:
                    reason = "REJECTED: headerless but no layout/content match"
            else:
                if header_sim >= config.header_sim_strict:
                    would_merge, reason = True, "repeated_header_match"
                else:
                    reason = "REJECTED: new_table_detected (headers don't match)"
            
            print(f"  VERDICT:           {'✅ MERGE' if would_merge else '❌ NO MERGE'} ({reason})")
            print()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_merger.py <pdf_file>")
        sys.exit(1)
    debug_extraction(sys.argv[1])