"""
Table Stitcher — Reassemble tables split across page boundaries.

A parser-agnostic library that detects and merges table fragments produced
by PDF extraction tools. Ships with a Docling adapter out of the box.

Usage (Docling):
    from table_stitcher import stitch_tables, MultiPageConfig

    doc = stitch_tables(doc)  # Use defaults
    doc = stitch_tables(doc, config=MultiPageConfig(max_page_gap=2))

Usage (custom parser):
    from table_stitcher import TableStitcher
    from table_stitcher.adapters.base import TableStitcherAdapter

    class MyAdapter:
        def extract(self, doc, cfg): ...
        def inject(self, doc, logical_tables): ...

    stitcher = TableStitcher(adapter=MyAdapter())
    doc = stitcher.stitch(doc)
"""

import time
import logging
from typing import Any, Optional

from .models import MultiPageConfig, LogicalTable, TableMeta
from .merger import merge_multipage_tables
from .adapters.base import TableStitcherAdapter

__version__ = "0.2.0"
__all__ = [
    "stitch_tables",
    "extract_table_meta",
    "TableStitcher",
    "MultiPageConfig",
    "TableMeta",
    "StitchingError",
    "TableStitcherAdapter",
    "__version__",
]


class StitchingError(Exception):
    """Raised when table stitching fails."""
    pass


class TableStitcher:
    """
    Detects and merges tables split across multiple pages.

    This class is parser-agnostic. Pass any adapter that implements the
    ``TableStitcherAdapter`` protocol (two methods: ``extract`` and ``inject``).

    For simple Docling usage, use the ``stitch_tables()`` function instead.
    """

    def __init__(
        self,
        adapter: TableStitcherAdapter,
        config: Optional[MultiPageConfig] = None,
    ):
        self.logger = logging.getLogger("table_stitcher")
        self.adapter = adapter
        self.config = config or MultiPageConfig()
        self._validate_config()

    # -------------------------------------------------------------------------
    # Logging
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
        symbol = "+" if status == "success" else "x"
        self.logger.info(f"  [{symbol}] {item_name}")

    def _log_error(self, context: str, error: Exception):
        self.logger.error(f"  [x] {context}: {error}")

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def _validate_config(self) -> None:
        """Validate configuration values."""
        errors = []
        cfg = self.config

        if not (1 <= cfg.max_page_gap <= 10):
            errors.append(f"max_page_gap must be 1-10, got {cfg.max_page_gap}")

        if cfg.max_width_difference < 0:
            errors.append(f"max_width_difference must be >= 0, got {cfg.max_width_difference}")

        for name, value in [
            ("header_sim_strict", cfg.header_sim_strict),
            ("header_sim_loose", cfg.header_sim_loose),
            ("row_sim_threshold", cfg.row_sim_threshold),
            ("bottom_band_min", cfg.bottom_band_min),
            ("top_band_max", cfg.top_band_max),
        ]:
            if not (0.0 <= value <= 1.0):
                errors.append(f"{name} must be 0.0-1.0, got {value}")

        if cfg.max_orphan_rows < 0:
            errors.append(f"max_orphan_rows must be >= 0, got {cfg.max_orphan_rows}")
        if cfg.max_data_orphan_rows < 0:
            errors.append(f"max_data_orphan_rows must be >= 0, got {cfg.max_data_orphan_rows}")

        if errors:
            raise ValueError("Invalid MultiPageConfig:\n  " + "\n  ".join(errors))

    # -------------------------------------------------------------------------
    # Core Processing
    # -------------------------------------------------------------------------

    def stitch(
        self,
        doc: Any,
        raise_on_error: bool = False,
    ) -> Any:
        """
        Detect and merge tables split across multiple pages.

        Args:
            doc: The parser-native document object.
            raise_on_error: If True, raise exceptions on processing errors.
                            If False (default), log errors and return original doc.

        Returns:
            The document with merged tables.
        """
        if doc is None:
            if raise_on_error:
                raise StitchingError("Input document is None")
            self.logger.error("Input document is None")
            return doc

        # --- Phase 1: Extract Metadata ---
        self._log_phase_start(1, "Extract Table Metadata")
        phase_start = time.time()

        try:
            tables_meta = self.adapter.extract(doc, self.config)
        except Exception as e:
            self._log_error("Metadata extraction failed", e)
            if raise_on_error:
                raise StitchingError(f"Failed to extract table metadata: {e}") from e
            return doc

        if not tables_meta:
            self._log_section("No tables found in document")
            self._log_phase_complete(1, 0, time.time() - phase_start, "tables")
            return doc

        # Report extraction coverage — tables that failed extraction
        # are silently preserved in the original doc (pass-through).
        total_tables = len(getattr(doc, 'tables', []) or [])
        if total_tables and len(tables_meta) < total_tables:
            skipped = total_tables - len(tables_meta)
            self._log_section(
                f"Extracted {len(tables_meta)}/{total_tables} tables "
                f"({skipped} skipped — originals preserved)"
            )

        self._log_phase_complete(1, len(tables_meta), time.time() - phase_start, "table fragments extracted")

        # --- Phase 2: Analyze & Merge ---
        self._log_phase_start(2, "Analyze Multi-Page Merges")
        phase_start = time.time()

        try:
            logical_tables = merge_multipage_tables(tables_meta, self.config)
        except Exception as e:
            self._log_error("Merge analysis failed", e)
            if raise_on_error:
                raise StitchingError(f"Failed to merge tables: {e}") from e
            return doc

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
            doc = self.adapter.inject(doc, logical_tables)
        except Exception as e:
            self._log_error("Injection failed", e)
            if raise_on_error:
                raise StitchingError(f"Failed to inject merged tables: {e}") from e
            return doc

        for lt in multi_page_tables:
            self._log_item_progress(f"Merged pages {lt.pages} -> 1 table", "success")

        self._log_phase_complete(3, len(multi_page_tables), time.time() - phase_start, "tables injected")

        return doc


# -----------------------------------------------------------------------------
# Convenience Function
# -----------------------------------------------------------------------------

def stitch_tables(
    doc: Any,
    config: Optional[MultiPageConfig] = None,
    raise_on_error: bool = False,
) -> Any:
    """
    Detect and merge tables split across multiple pages.

    Convenience function that uses the Docling adapter by default.
    For other parsers, use ``TableStitcher`` with a custom adapter.

    Args:
        doc: The input DoclingDocument (already converted from PDF).
        config: Optional configuration overrides. Uses sensible defaults if None.
        raise_on_error: If True, raise exceptions on processing errors.
                        If False (default), log errors and return original doc.

    Returns:
        The document with merged tables.

    Raises:
        StitchingError: If raise_on_error=True and processing fails.
        ValueError: If config values are invalid.

    Example:
        >>> from docling.document_converter import DocumentConverter
        >>> from table_stitcher import stitch_tables
        >>>
        >>> converter = DocumentConverter()
        >>> doc = converter.convert("report.pdf").document
        >>> doc = stitch_tables(doc)
    """
    try:
        from .adapters.docling import DoclingAdapter
    except ModuleNotFoundError as e:
        if "docling_core" in str(e):
            raise ImportError(
                "The Docling adapter requires docling-core. "
                "Install it with: pip install table-stitcher[docling]"
            ) from e
        raise  # genuine bug inside docling.py — don't mask it

    try:
        stitcher = TableStitcher(adapter=DoclingAdapter(), config=config)
        return stitcher.stitch(doc, raise_on_error=raise_on_error)
    except ValueError:
        if raise_on_error:
            raise
        logging.getLogger("table_stitcher").error(
            "Invalid configuration, returning original document"
        )
        return doc


def extract_table_meta(
    doc: Any,
    config: Optional[MultiPageConfig] = None,
) -> list:
    """
    Extract table metadata without merging — useful for analysis.

    Convenience function that uses the Docling adapter by default.
    Returns a list of ``TableMeta`` objects describing each table fragment.

    Args:
        doc: The input DoclingDocument (already converted from PDF).
        config: Optional configuration overrides. Uses sensible defaults if None.

    Returns:
        List of TableMeta objects for each table in the document.

    Example:
        >>> from table_stitcher import extract_table_meta
        >>> metas = extract_table_meta(doc)
        >>> for m in metas:
        ...     print(f"Table {m.idx}: {m.width} cols, page {m.start_page}")
    """
    try:
        from .adapters.docling import DoclingAdapter
    except ModuleNotFoundError as e:
        if "docling_core" in str(e):
            raise ImportError(
                "The Docling adapter requires docling-core. "
                "Install it with: pip install table-stitcher[docling]"
            ) from e
        raise

    return DoclingAdapter().extract(doc, config or MultiPageConfig())
