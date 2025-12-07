"""
Docling Table Enricher

A robust post-processor for Docling that detects and merges tables
split across multiple pages in PDF documents.

Usage:
    from docling_table_enricher import enrich_document, MultiPageConfig
    
    doc = enrich_document(doc)  # Use defaults
    doc = enrich_document(doc, config=MultiPageConfig(max_page_gap=2))  # Custom config
"""

import time
import logging
from typing import Optional

from docling_core.types.doc import DoclingDocument

from .models import MultiPageConfig, LogicalTable
from .merger import extract_table_meta, merge_multipage_tables
from .injector import inject_merged_tables

__version__ = "0.1.0"
__all__ = ["enrich_document", "MultiPageConfig", "__version__", "TableEnricher"]


class EnrichmentError(Exception):
    """Raised when table enrichment fails."""
    pass


class TableEnricher:
    """
    Detects and merges tables split across multiple pages.
    
    This class provides the core enrichment logic with standardized logging.
    For simple usage, use the `enrich_document()` function instead.
    """
    
    def __init__(self, config: Optional[MultiPageConfig] = None):
        """
        Initialize the enricher.
        
        Args:
            config: Optional configuration. Uses sensible defaults if None.
        """
        self.logger = logging.getLogger("docling_table_enricher")
        self.config = config or MultiPageConfig()
        self._validate_config()
    
    # -------------------------------------------------------------------------
    # Logging Helpers (aligned with LoggingMixin pattern)
    # -------------------------------------------------------------------------
    
    def _log_phase_start(self, phase_num: int, phase_name: str):
        self.logger.info("=" * 70)
        self.logger.info(f"Phase {phase_num}: {phase_name}")
        self.logger.info("=" * 70)
    
    def _log_phase_complete(self, phase_num: int, count: int, 
                           duration: float, item_type: str = "items"):
        self.logger.info(
            f"Phase {phase_num} complete: {count} {item_type} [{duration:.1f}s]"
        )
    
    def _log_section(self, message: str, indent: int = 2):
        prefix = " " * indent
        self.logger.info(f"{prefix}{message}")
    
    def _log_item_progress(self, item_name: str, status: str = "success"):
        symbol = "✓" if status == "success" else "✗"
        self.logger.info(f"  {symbol} {item_name}")
    
    def _log_error(self, context: str, error: Exception):
        self.logger.error(f"  ✗ {context}: {error}")
    
    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------
    
    def _validate_config(self) -> None:
        """Validate configuration values."""
        errors = []
        cfg = self.config
        
        # Page adjacency
        if not (1 <= cfg.max_page_gap <= 10):
            errors.append(f"max_page_gap must be 1-10, got {cfg.max_page_gap}")
        
        # Width matching
        if cfg.max_width_difference < 0:
            errors.append(f"max_width_difference must be >= 0, got {cfg.max_width_difference}")
        
        # Similarity thresholds (must be 0-1)
        for name, value in [
            ("header_sim_strict", cfg.header_sim_strict),
            ("header_sim_loose", cfg.header_sim_loose),
            ("row_sim_threshold", cfg.row_sim_threshold),
            ("bottom_band_min", cfg.bottom_band_min),
            ("top_band_max", cfg.top_band_max),
        ]:
            if not (0.0 <= value <= 1.0):
                errors.append(f"{name} must be 0.0-1.0, got {value}")
        
        # Orphan detection
        if cfg.max_orphan_rows < 0:
            errors.append(f"max_orphan_rows must be >= 0, got {cfg.max_orphan_rows}")
        if cfg.max_data_orphan_rows < 0:
            errors.append(f"max_data_orphan_rows must be >= 0, got {cfg.max_data_orphan_rows}")
        
        if errors:
            raise ValueError("Invalid MultiPageConfig:\n  " + "\n  ".join(errors))
    
    # -------------------------------------------------------------------------
    # Core Processing
    # -------------------------------------------------------------------------
    
    def enrich(
        self, 
        doc: DoclingDocument, 
        raise_on_error: bool = False
    ) -> DoclingDocument:
        """
        Detect and merge tables split across multiple pages.
        
        Args:
            doc: The input DoclingDocument (already converted from PDF).
            raise_on_error: If True, raise exceptions on processing errors.
                            If False (default), log errors and return original doc.

        Returns:
            The enriched DoclingDocument with merged tables.
        """
        if doc is None:
            if raise_on_error:
                raise EnrichmentError("Input document is None")
            self.logger.error("Input document is None")
            return doc

        # --- Phase 1: Extract Metadata ---
        self._log_phase_start(1, "Extract Table Metadata")
        phase_start = time.time()
        
        try:
            tables_meta = extract_table_meta(doc, self.config)
        except Exception as e:
            self._log_error("Metadata extraction failed", e)
            if raise_on_error:
                raise EnrichmentError(f"Failed to extract table metadata: {e}") from e
            return doc
        
        if not tables_meta:
            self._log_section("No tables found in document")
            self._log_phase_complete(1, 0, time.time() - phase_start, "tables")
            return doc
        
        self._log_phase_complete(1, len(tables_meta), time.time() - phase_start, "table fragments extracted")

        # --- Phase 2: Analyze & Merge ---
        self._log_phase_start(2, "Analyze Multi-Page Merges")
        phase_start = time.time()
        
        try:
            logical_tables = merge_multipage_tables(tables_meta, self.config)
        except Exception as e:
            self._log_error("Merge analysis failed", e)
            if raise_on_error:
                raise EnrichmentError(f"Failed to merge tables: {e}") from e
            return doc
        
        # Count multi-page tables
        multi_page_tables = [lt for lt in logical_tables if len(lt.pages) > 1]
        
        if not multi_page_tables:
            self._log_section("No multi-page tables detected")
            self._log_phase_complete(2, 0, time.time() - phase_start, "merges")
            return doc
        
        for lt in multi_page_tables:
            self._log_section(f"Found: Table spanning pages {lt.pages} ({len(lt.members)} fragments)")
        
        self._log_phase_complete(2, len(multi_page_tables), time.time() - phase_start, "multi-page tables identified")

        # --- Phase 3: Inject Merged Tables ---
        self._log_phase_start(3, "Inject Merged Tables")
        phase_start = time.time()
        
        try:
            doc = inject_merged_tables(doc, logical_tables)
        except Exception as e:
            self._log_error("Injection failed", e)
            if raise_on_error:
                raise EnrichmentError(f"Failed to inject merged tables: {e}") from e
            return doc
        
        # Log success for each merged table
        for lt in multi_page_tables:
            self._log_item_progress(f"Merged pages {lt.pages} → 1 table", "success")
        
        self._log_phase_complete(3, len(multi_page_tables), time.time() - phase_start, "tables injected")
        
        return doc


# -----------------------------------------------------------------------------
# Convenience Function (maintains simple API)
# -----------------------------------------------------------------------------

def enrich_document(
    doc: DoclingDocument, 
    config: Optional[MultiPageConfig] = None,
    raise_on_error: bool = False
) -> DoclingDocument:
    """
    Detect and merge tables split across multiple pages.
    
    This is a convenience function that wraps TableEnricher for simple usage.
    
    Args:
        doc: The input DoclingDocument (already converted from PDF).
        config: Optional configuration overrides. Uses sensible defaults if None.
        raise_on_error: If True, raise exceptions on processing errors.
                        If False (default), log errors and return original doc.

    Returns:
        The enriched DoclingDocument with merged tables.
        
    Raises:
        EnrichmentError: If raise_on_error=True and processing fails.
        ValueError: If config values are invalid.
        
    Example:
        >>> from docling.document_converter import DocumentConverter
        >>> from docling_table_enricher import enrich_document
        >>> 
        >>> converter = DocumentConverter()
        >>> doc = converter.convert("report.pdf").document
        >>> doc = enrich_document(doc)
    """
    try:
        enricher = TableEnricher(config=config)
        return enricher.enrich(doc, raise_on_error=raise_on_error)
    except ValueError:
        # Config validation error
        if raise_on_error:
            raise
        logging.getLogger("docling_table_enricher").error(
            f"Invalid configuration, returning original document"
        )
        return doc