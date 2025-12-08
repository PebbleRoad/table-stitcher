from pathlib import Path
from docling.document_converter import DocumentConverter

pdf_path = "testdocs/CTOaaS Application Maintenance Report - 11_24 - v1.pdf"

converter = DocumentConverter()
doc = converter.convert(pdf_path).document

# Export ORIGINAL Docling output (no enrichment)
with open("original_no_enrich.html", "w") as f:
    f.write(doc.export_to_html())

print("Saved original_no_enrich.html - compare with enriched version")