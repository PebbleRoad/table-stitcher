import sys
import shutil
import pandas as pd
from pathlib import Path
from docling.document_converter import DocumentConverter
from docling_table_enricher import enrich_document, MultiPageConfig

# Adjust pandas display for checking
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

def docling_table_to_dataframe(doc_table):
    grid = doc_table.data.grid
    if not grid: return pd.DataFrame()
    data = [[cell.text if cell else "" for cell in row] for row in grid]
    if data:
        header = data[0]
        rows = data[1:]
        return pd.DataFrame(rows, columns=header)
    return pd.DataFrame()

def save_merged_tables(pdf_path: str):
    input_path = Path(pdf_path)
    if not input_path.exists():
        print(f"Error: File {input_path} not found.")
        return

    print(f"\n🔎 Processing: {input_path.name}")
    
    # 2. CONFIGURATION: Stricter
    config = MultiPageConfig(
        max_page_gap=1,
        
        # Geometry: Keep it flexible for short data rows
        bottom_band_min=0.10,  
        top_band_max=0.90,     
        
        # Similarity: RAISED THRESHOLD
        # We demand at least 50% match for headers.
        # This prevents Table 1 (Versions) merging with Table 2 (Config)
        header_sim_strict=0.6,
        header_sim_loose=0.5, 
        
        # Width: Tighten up
        # Don't merge if column counts are wildly different
        max_width_difference=2,
        
        stitch_separator="\n"
    )

    # 3. Run Pipeline
    converter = DocumentConverter()
    doc = converter.convert(str(input_path)).document
    doc = enrich_document(doc, config=config)

    # 4. Save Output
    output_dir = Path("debug_tables")
    if output_dir.exists(): shutil.rmtree(output_dir)
    output_dir.mkdir()

    print("\n" + "="*60)
    print(f"   SAVING MERGED TABLES TO: {output_dir}/")
    print("="*60)

    merged_count = 0
    for i, table in enumerate(doc.tables):
        if not table.prov: continue
        prov_list = table.prov if isinstance(table.prov, list) else [table.prov]
        pages = sorted(list(set(p.page_no for p in prov_list)))
        
        if len(pages) > 1:
            merged_count += 1
            pages_str = "_".join(map(str, pages))
            filename = f"table_{i}_pages_{pages_str}.csv"
            save_path = output_dir / filename
            df = docling_table_to_dataframe(table)
            df.to_csv(save_path, index=False)
            print(f"✅ Saved Table {i} (Pages {pages}) -> {filename}")

    if merged_count == 0:
        print("No merged tables found.")
    else:
        print(f"\nDone! Checked 'debug_tables/' folder.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 inspect_results.py <pdf_filename>")
        sys.exit(1)
    filename = " ".join(sys.argv[1:])
    save_merged_tables(filename)