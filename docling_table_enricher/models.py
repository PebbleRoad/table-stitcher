from dataclasses import dataclass, field
from typing import List, Set, Optional, Dict

import pandas as pd


# Default tokens that suggest a row is a header (not data)
DEFAULT_HEADERISH_TOKENS: Set[str] = {
    # Common table headers
    "no", "sno", "s", "id", "name", "type", "status", "description",
    "date", "time", "period", "version", "environment", "reference",
    "comment", "remarks", "notes", "action", "category", "item",
    "value", "unit", "total", "count", "number",
    # Business/reporting terms
    "severity", "level", "availability", "downtime", "scheduled",
    "operation", "monthly", "weekly", "daily", "target", "actual",
    "variance", "met", "sla", "system", "application",
}


@dataclass
class MultiPageConfig:
    """
    Configuration for multi-page table merging.
    
    The merger uses three main signals to decide if tables should merge:
    1. Sequential adjacency: Tables must be consecutive in document order
    2. Width matching: Same column count suggests same table structure
    3. Header analysis: Headerless fragments continue the previous table
    
    Geometry-based signals (vert_top, vert_bottom) are defined but currently
    have no effect because Docling doesn't provide bounding box data.
    """
    
    # --- Page Adjacency ---
    max_page_gap: int = 1
    """Maximum number of pages between fragments to consider merging."""
    
    # --- Width Matching ---
    require_same_width: bool = False
    """If True, only merge tables with identical column counts."""
    
    max_width_difference: int = 4
    """Maximum allowed difference in column count for merging."""
    
    # --- Similarity Thresholds ---
    header_sim_strict: float = 0.6
    """Jaccard similarity threshold for 'repeated header' detection."""
    
    header_sim_loose: float = 0.3
    """Lower threshold used when other signals are strong."""
    
    row_sim_threshold: float = 0.3
    """Similarity threshold for first-row content matching (fallback)."""
    
    # --- Geometry/Layout Hints ---
    use_layout_hint: bool = True
    """Whether to use vertical position for merge decisions."""
    
    bottom_band_min: float = 0.60
    """
    Table A must end at or below this position to be a continuation candidate.
    Uses normalized coordinates: 0 = top of page, 1 = bottom of page.
    Default 0.6 means table must be in the bottom 40% of the page.
    """
    
    top_band_max: float = 0.40
    """
    Table B must start at or above this position to be a continuation candidate.
    Uses normalized coordinates: 0 = top of page, 1 = bottom of page.
    Default 0.4 means table must be in the top 40% of the page.
    """
    
    # --- Header/Orphan Detection ---
    headerish_tokens: Set[str] = field(default_factory=lambda: DEFAULT_HEADERISH_TOKENS.copy())
    """Tokens that suggest a cell contains header text, not data."""
    
    min_headerish_tokens: int = 1
    """Minimum header-like tokens required to consider a row as headers."""
    
    max_orphan_rows: int = 2
    """Maximum rows for a table to be considered a 'header orphan'."""
    
    max_data_orphan_rows: int = 5
    """Maximum rows for a table to be considered a 'data orphan'."""
    
    # --- Spillover Detection ---
    spillover_require_content_check: bool = False
    """
    If True, 1-column fragments must contain URL/ticket patterns to be spillover.
    If False (default), any 1-column headerless fragment is treated as spillover.
    The structural signal (1 col following N cols) is strong enough for most cases.
    """
    
    # --- Cell Stitching ---
    stitch_separator: str = "\n"
    """Character(s) used to join split cell content."""


@dataclass
class TableMeta:
    """Metadata for a single extracted table fragment."""
    idx: int
    df: pd.DataFrame
    start_page: Optional[int]
    pages: List[int]
    width: int
    header_tokens: Set[str]
    first_row_tokens: Set[str]
    raw_columns: List[str]
    vert_center: Optional[float]
    vert_top: Optional[float]
    vert_bottom: Optional[float]
    is_header_orphan: bool
    is_data_orphan: bool
    numeric_like_cols: bool
    row_count: int
    continuation_content: List[Dict] = field(default_factory=list)
    is_headerless: bool = False


@dataclass 
class LogicalTable:
    """A merged logical table spanning potentially multiple pages."""
    logical_index: int
    members: List[int]
    pages: List[int]
    df: pd.DataFrame
    merge_reason: str = ""