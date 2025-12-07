"""
Diagnostic script to inspect what bbox/provenance data Docling actually provides.
"""
import sys
from pathlib import Path
from docling.document_converter import DocumentConverter

def inspect_provenance(pdf_path: str):
    input_path = Path(pdf_path).resolve()
    
    if not input_path.exists():
        print(f"File not found: {input_path}")
        sys.exit(1)
    
    print(f"Converting: {input_path.name}\n")
    
    converter = DocumentConverter()
    doc = converter.convert(str(input_path)).document
    
    print(f"Found {len(doc.tables)} tables\n")
    print("=" * 70)
    
    for i, table in enumerate(doc.tables):
        print(f"\nTABLE {i}:")
        print("-" * 40)
        
        # Check provenance
        prov = getattr(table, "prov", None)
        print(f"  prov type: {type(prov)}")
        print(f"  prov value: {prov}")
        
        if prov:
            prov_list = prov if isinstance(prov, list) else [prov]
            
            for j, p in enumerate(prov_list):
                print(f"\n  Provenance item {j}:")
                print(f"    type: {type(p)}")
                print(f"    dir(): {[a for a in dir(p) if not a.startswith('_')]}")
                
                # Check for bbox in various forms
                for attr in ['bbox', 'box', 'bounding_box', 'bounds']:
                    val = getattr(p, attr, None)
                    if val is not None:
                        print(f"    {attr}: {val}")
                        print(f"    {attr} type: {type(val)}")
                        print(f"    {attr} dir(): {[a for a in dir(val) if not a.startswith('_')]}")
                        
                        # Try to get coordinates
                        for coord in ['l', 'r', 't', 'b', 'x', 'y', 'x0', 'x1', 'y0', 'y1', 
                                      'left', 'right', 'top', 'bottom', 'width', 'height']:
                            coord_val = getattr(val, coord, None)
                            if coord_val is not None:
                                print(f"      {coord}: {coord_val}")
                
                # Check page_no
                page_no = getattr(p, 'page_no', None)
                if page_no is not None:
                    print(f"    page_no: {page_no}")
        
        # Only show first 3 tables to keep output manageable
        if i >= 2:
            print(f"\n... and {len(doc.tables) - 3} more tables")
            break

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inspect_bbox.py <pdf_file>")
        sys.exit(1)
    
    inspect_provenance(" ".join(sys.argv[1:]))