"""
Multi-page table merger for Docling documents.

This module detects and merges tables that were split across page boundaries
during PDF extraction. It operates on the DoclingDocument object in-place.

Key Principles:
1. Sequential merging: Headerless fragments only merge with immediate predecessor
2. Width matching: Same column count = same table structure (primary signal)
3. Spillover detection: 1-column fragments are cell overflow, not new tables
4. New table detection: Fragments with non-matching headers are separate tables
"""

import re
import logging
from collections import defaultdict
from typing import Any, List, Set, Tuple, Optional, Dict

import pandas as pd

from .models import MultiPageConfig, TableMeta, LogicalTable, DEFAULT_HEADERISH_TOKENS

log = logging.getLogger(__name__)


# -------------------------------------------------------------------
# 1. UTILITY FUNCTIONS
# -------------------------------------------------------------------

def normalize_col_name(col: Any) -> str:
    """Normalize column name for comparison."""
    return str(col).strip().lower()


def tokenize(text: str) -> Set[str]:
    """Extract lowercase alphabetic tokens from text."""
    text = str(text).lower()
    tokens = re.findall(r"[a-zA-Z]+", text)
    return set(tokens)


def jaccard(a: Set[str], b: Set[str]) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def is_numeric_like_colnames(cols: List[Any]) -> bool:
    """Check if column names look auto-generated (numeric or 'Unnamed')."""
    if not cols:
        return False
    numeric_like = 0
    for c in cols:
        s = str(c).strip().lower()
        if re.fullmatch(r"\d+", s):
            numeric_like += 1
        elif s.startswith("unnamed:"):
            numeric_like += 1
    return numeric_like / len(cols) >= 0.7


def first_row_has_number(df: pd.DataFrame) -> bool:
    """Check if the first row contains any numeric characters."""
    if df.shape[0] == 0:
        return False
    row_text = " ".join(str(x) for x in df.iloc[0].tolist())
    return bool(re.search(r"\d", row_text))


def is_empty_value(val: Any) -> bool:
    """Check if a value is empty/null."""
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    if isinstance(val, str) and val.strip() == "":
        return True
    return False


def clean_malformed_header(col: str) -> str:
    """Fix headers like 'Name.Name' -> 'Name'."""
    col = str(col).strip()
    if "." in col:
        parts = col.split(".")
        if len(parts) == 2 and parts[0].strip().lower() == parts[1].strip().lower():
            return parts[0].strip()
    return col


def clean_all_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Apply header cleaning to all columns."""
    new_cols = [clean_malformed_header(c) for c in df.columns]
    df_copy = df.copy()
    df_copy.columns = new_cols
    return df_copy


def extract_y_bounds_from_prov(prov_list: List[Any]) -> Optional[Tuple[float, float, str]]:
    """
    Extract vertical bounds from provenance data.
    
    Returns: (y_min, y_max, coord_origin) or None if not available.
    
    Docling uses BoundingBox with attributes:
    - l, r, t, b (left, right, top, bottom)
    - coord_origin: BOTTOMLEFT or TOPLEFT
    """
    for p in prov_list:
        bbox = getattr(p, "bbox", None)
        if bbox is None:
            continue
        
        # Docling's BoundingBox uses t, b, l, r
        t = getattr(bbox, "t", None)
        b = getattr(bbox, "b", None)
        
        if t is not None and b is not None:
            coord_origin = getattr(bbox, "coord_origin", None)
            origin_str = str(coord_origin) if coord_origin else "BOTTOMLEFT"
            return (float(b), float(t), origin_str)
    
    return None


def compute_vertical_positions(
    prov_list: List[Any], 
    page_height: float = 842.0  # Default A4 height in points
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Compute normalized vertical positions (0-1 scale, top=0, bottom=1).
    
    Returns: (vert_center, vert_top, vert_bottom) normalized to 0-1 scale
    where 0 = top of page, 1 = bottom of page.
    
    This normalization makes it intuitive:
    - A table at the top of the page has vert_top ≈ 0
    - A table at the bottom of the page has vert_bottom ≈ 1
    """
    bounds = extract_y_bounds_from_prov(prov_list)
    if bounds is None:
        return None, None, None
    
    y_bottom, y_top, origin_str = bounds
    
    # Handle coordinate origin
    if "BOTTOMLEFT" in origin_str.upper():
        # In BOTTOMLEFT: y=0 is page bottom, y increases upward
        # Normalize so that 0 = top of page, 1 = bottom of page
        # We need to invert: normalized = 1 - (y / page_height)
        if y_top > page_height:
            # Use actual values to estimate page height
            page_height = max(y_top * 1.1, page_height)
        
        vert_top = 1.0 - (y_top / page_height)      # Closer to 0 = higher on page
        vert_bottom = 1.0 - (y_bottom / page_height)  # Closer to 1 = lower on page
    else:
        # In TOPLEFT: y=0 is page top, y increases downward
        # Already in the right orientation
        vert_top = y_top / page_height
        vert_bottom = y_bottom / page_height
    
    # Clamp to 0-1 range
    vert_top = max(0.0, min(1.0, vert_top))
    vert_bottom = max(0.0, min(1.0, vert_bottom))
    vert_center = (vert_top + vert_bottom) / 2.0
    
    return vert_center, vert_top, vert_bottom


# -------------------------------------------------------------------
# 2. GRID-TO-DATAFRAME CONVERSION
# -------------------------------------------------------------------

def grid_to_dataframe(table, doc) -> pd.DataFrame:
    """
    Convert Docling table grid to DataFrame with intelligent header detection.
    
    This function determines whether the first row is a header or data by
    checking for numeric content, URLs, and sparseness patterns.
    """
    if not hasattr(table, 'data') or not table.data or not hasattr(table.data, 'grid'):
        return table.export_to_dataframe(doc=doc)
    
    grid = table.data.grid
    if not grid:
        return pd.DataFrame()
    
    # 1. Extract raw text from grid
    all_rows = []
    for row in grid:
        row_data = [getattr(cell, 'text', str(cell)) if cell else '' for cell in row]
        all_rows.append(row_data)
    
    if not all_rows:
        return pd.DataFrame()

    # 2. Filter to non-empty rows
    real_content_rows = [r for r in all_rows if any(c.strip() for c in r)]
    
    if not real_content_rows:
        return pd.DataFrame(columns=[f"Column_{i}" for i in range(len(all_rows[0]))])

    first_row = real_content_rows[0]
    num_cols = len(first_row)
    
    # 3. Determine if first row is header or data
    # Signal A: Looks like data values? (dates, numbers, IDs, row identifiers)
    # We check for patterns that are clearly DATA, not just "contains any digit"
    data_patterns = [
        r'^\d+$',                    # Pure number: "123"
        r'^\d+\.\d+$',               # Decimal: "123.45"
        r'^\d{1,2}/\d{1,2}',         # Date-like: "02/09", "12/31/2024"
        r'^\d{1,2}-\d{1,2}',         # Date-like: "02-09"
        r'^https?://',               # URL
        r'^[A-Z]+-\d+$',             # Ticket ID: "JIRA-123"
        r'^\$[\d,]+',                # Currency: "$1,234"
        r'^[\d,]+\s*%$',             # Percentage: "45%"
        r'^Row\s*\d+',               # Row identifier: "Row 1", "Row 3"
        r'^\d+\.\d+\.\d+',           # Version: "1.2.3"
    ]
    
    def looks_like_data(cell: str) -> bool:
        cell = str(cell).strip()
        if not cell:
            return False
        for pattern in data_patterns:
            if re.search(pattern, cell, re.IGNORECASE):
                return True
        return False
    
    has_data_values = any(looks_like_data(c) for c in first_row)
    
    # Signal B: Contains URL?
    has_url = any("http" in str(c).lower() for c in first_row)
    
    # Signal C: Row has many repeated values or generic placeholders?
    # Headers are usually unique; data often repeats (e.g., "DATA", "N/A", "0")
    non_empty_vals = [str(c).strip().upper() for c in first_row if str(c).strip()]
    if len(non_empty_vals) >= 3:
        unique_vals = set(non_empty_vals)
        repetition_ratio = len(unique_vals) / len(non_empty_vals)
        # If more than 50% of values are duplicates, it's probably data
        has_repeated_values = repetition_ratio < 0.5
        # Common placeholder values indicate data rows
        placeholder_vals = {'DATA', 'N/A', 'NA', 'NULL', '-', '0', 'TBD', 'NONE', 'YES', 'NO'}
        has_placeholders = len(unique_vals & placeholder_vals) > 0
    else:
        has_repeated_values = False
        has_placeholders = False
    
    # Signal D: Sparse row? (First col empty, mostly empty = continuation)
    non_empty_count = sum(1 for v in first_row if v and v.strip())
    is_sparse = (non_empty_count < num_cols / 2) and (not first_row[0].strip())

    # Decision
    is_headerless = False
    
    if has_data_values or has_url or is_sparse or has_repeated_values or has_placeholders:
        # It's data, not headers
        is_headerless = True
        header = [f"Column_{i}" for i in range(num_cols)]
        
        if is_sparse and len(real_content_rows) > 1:
            pre_header_rows = [first_row]
            data_rows = real_content_rows[1:]
        else:
            pre_header_rows = []
            data_rows = real_content_rows
    else:
        # It's a header row
        is_headerless = False
        pre_header_rows = []
        header = first_row
        data_rows = real_content_rows[1:]
    
    # 4. Build DataFrame
    clean_header = []
    for h in header:
        h_str = str(h).strip()
        if '.' in h_str:
            parts = h_str.split('.')
            if len(parts) == 2 and parts[0] == parts[1]:
                h_str = parts[0]
        clean_header.append(h_str if h_str else f"Column_{len(clean_header)}")
    
    if data_rows:
        normalized_rows = []
        for row in data_rows:
            row_copy = list(row)
            while len(row_copy) < len(clean_header):
                row_copy.append('')
            normalized_rows.append(row_copy[:len(clean_header)])
        df = pd.DataFrame(normalized_rows, columns=clean_header)
    else:
        df = pd.DataFrame(columns=clean_header)
    
    # Store metadata for later use
    df.attrs['pre_header_rows'] = pre_header_rows
    df.attrs['is_headerless'] = is_headerless
    return df


# -------------------------------------------------------------------
# 3. METADATA EXTRACTION
# -------------------------------------------------------------------

def extract_table_meta(doc, cfg: MultiPageConfig) -> List[TableMeta]:
    """
    Extract metadata from all tables in the document.
    
    This metadata is used to determine which tables should be merged.
    """
    tables_meta: List[TableMeta] = []
    headerish_tokens = cfg.headerish_tokens or DEFAULT_HEADERISH_TOKENS
    
    for idx, table in enumerate(doc.tables):
        try:
            df = grid_to_dataframe(table, doc)
        except Exception as e:
            log.warning(f"Failed to extract table {idx}: {e}")
            continue

        # Extract attributes from DF construction
        continuation_content = []
        pre_header_rows = df.attrs.get('pre_header_rows', [])
        is_headerless = df.attrs.get('is_headerless', False)
        
        if pre_header_rows:
            for row in pre_header_rows:
                non_empty = [(i, v) for i, v in enumerate(row) if v and v.strip()]
                for col_idx, val in non_empty:
                    continuation_content.append({'col_idx': col_idx, 'value': val})
        
        # Extract page info from provenance
        prov = getattr(table, "prov", None) or []
        pages = sorted({p.page_no for p in prov}) if prov else []
        start_page = pages[0] if pages else None

        # Tokenize headers
        header_tokens: Set[str] = set()
        for col in df.columns:
            header_tokens |= tokenize(normalize_col_name(col))

        # Tokenize first row
        first_row_tokens: Set[str] = set()
        if df.shape[0] > 0:
            row_text = " ".join(str(x) for x in df.iloc[0].tolist())
            first_row_tokens = tokenize(row_text)

        # Compute vertical positions (often None due to missing bbox data)
        vert_center, vert_top, vert_bottom = None, None, None
        if cfg.use_layout_hint and prov:
            vert_center, vert_top, vert_bottom = compute_vertical_positions(prov)

        # Analyze structure
        raw_columns = [str(c) for c in df.columns]
        numeric_like_cols = is_numeric_like_colnames(raw_columns)
        headerish_in_first_row = len(first_row_tokens & headerish_tokens)
        headerish_in_headers = len(header_tokens & headerish_tokens)
        
        # Classify as header orphan (has headers but no/few data rows)
        is_header_orphan = (
            df.shape[0] <= cfg.max_orphan_rows and 
            df.shape[0] >= 0 and
            (
                (numeric_like_cols and headerish_in_first_row >= cfg.min_headerish_tokens) or
                (headerish_in_headers >= 2 and df.shape[0] <= 1) or 
                df.shape[0] == 0
            )
        )
        
        # Classify as data orphan (has data but no real headers)
        is_data_orphan = (
            df.shape[0] > 0 and 
            df.shape[0] <= cfg.max_data_orphan_rows and 
            first_row_has_number(df)
        )

        tables_meta.append(TableMeta(
            idx=idx,
            df=df,
            start_page=start_page,
            pages=pages,
            width=df.shape[1],
            header_tokens=header_tokens,
            first_row_tokens=first_row_tokens,
            raw_columns=raw_columns,
            vert_center=vert_center,
            vert_top=vert_top,
            vert_bottom=vert_bottom,
            is_header_orphan=is_header_orphan,
            is_data_orphan=is_data_orphan,
            numeric_like_cols=numeric_like_cols,
            row_count=df.shape[0],
            continuation_content=continuation_content,
            is_headerless=is_headerless
        ))
    
    return tables_meta


# -------------------------------------------------------------------
# 4. UNION-FIND DATA STRUCTURE
# -------------------------------------------------------------------

class UnionFind:
    """Union-Find (Disjoint Set) for grouping table fragments."""
    
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n
    
    def find(self, x: int) -> int:
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]
    
    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


# -------------------------------------------------------------------
# 5. MERGE DECISION LOGIC
# -------------------------------------------------------------------

def layout_suggests_continuation(tA: TableMeta, tB: TableMeta, cfg: MultiPageConfig) -> bool:
    """
    Check if vertical positions suggest tB continues tA.
    
    Uses normalized coordinates where 0 = top of page, 1 = bottom of page.
    
    For continuation:
    - Table A should be near the BOTTOM of its page (vert_bottom >= bottom_band_min)
    - Table B should be near the TOP of its page (vert_top <= top_band_max)
    """
    if not cfg.use_layout_hint:
        return False
    if tA.vert_bottom is None or tB.vert_top is None:
        return False
    
    # tA.vert_bottom close to 1.0 = table A ends near bottom of page
    # tB.vert_top close to 0.0 = table B starts near top of page
    a_near_bottom = tA.vert_bottom >= cfg.bottom_band_min
    b_near_top = tB.vert_top <= cfg.top_band_max
    
    return a_near_bottom and b_near_top


def should_force_orphan_merge(h: TableMeta, d: TableMeta, cfg: MultiPageConfig) -> Tuple[bool, str]:
    """Check if header orphan + data orphan should merge."""
    if h.start_page is None or d.start_page is None:
        return False, ""
    if (d.start_page - h.start_page) > cfg.max_page_gap:
        return False, ""
    if abs(h.width - d.width) > cfg.max_width_difference:
        return False, ""
    
    layout = layout_suggests_continuation(h, d, cfg)
    if h.is_header_orphan and d.is_data_orphan:
        return True, "orphans" + ("+layout" if layout else "")
    return False, ""


def is_spillover_fragment(tA: TableMeta, tB: TableMeta, cfg: MultiPageConfig) -> bool:
    """
    Detect if tB is a spillover fragment (cell overflow from tA).
    
    A spillover fragment is characterized by:
    - 1 column (content got dumped into a single cell)
    - Headerless (no structure, just content)
    - Follows a multi-column table
    - On the immediately following page
    
    By default, the structural signal is strong enough. Content checking
    (URLs, ticket patterns) is optional via spillover_require_content_check.
    """
    if not (tB.is_headerless and tB.width == 1 and tA.width > 1):
        return False
    
    if not cfg.spillover_require_content_check:
        # Structural signal is sufficient
        return True
    
    # Optional: Verify content looks like continuation
    if tB.df.shape[0] == 0:
        return False
    
    first_cell = str(tB.df.iloc[0, 0]).lower()
    looks_like_continuation = (
        "http" in first_cell or
        "://" in first_cell or
        bool(re.search(r'[A-Z]+-\d+', str(tB.df.iloc[0, 0]))) or  # JIRA-style
        tB.row_count <= 2
    )
    return looks_like_continuation


# -------------------------------------------------------------------
# 6. TABLE BUILDING (Post-Merge)
# -------------------------------------------------------------------

def stitch_split_cells(df: pd.DataFrame, separator: str = "\n") -> pd.DataFrame:
    """
    Merge rows that are actually split cells.
    
    Detects patterns where a row has only one non-empty cell, which is
    likely continuation content from the previous row.
    """
    if df.shape[0] <= 1:
        return df
    
    cols = list(df.columns)
    stitched_rows = []
    i = 0
    n = df.shape[0]
    
    while i < n:
        row = df.iloc[i].tolist()
        j = i + 1
        
        while j < n:
            next_row = df.iloc[j]
            nonempty = [c for c in cols if not is_empty_value(next_row[c])]
            
            if len(nonempty) != 1:
                break
            
            cont_col = nonempty[0]
            cont_val = str(next_row[cont_col]).strip()
            
            # Smart target selection: URLs belong in content/reference columns
            target_idx = cols.index(cont_col)
            is_url = "://" in cont_val or cont_val.lower().startswith("http")
            
            if is_url:
                candidates = [
                    k for k, c in enumerate(cols) 
                    if any(x in c.lower() for x in ['content', 'ref', 'desc', 'link', 'url'])
                ]
                if candidates:
                    target_idx = candidates[-1]
                else:
                    target_idx = len(cols) - 1  # Fallback to last column
            
            prev_val = row[target_idx]
            if not is_empty_value(prev_val):
                row[target_idx] = str(prev_val).rstrip() + separator + cont_val.lstrip()
            else:
                row[target_idx] = cont_val
            j += 1
        
        stitched_rows.append(row)
        i = j
    
    return pd.DataFrame(stitched_rows, columns=cols)


def align_dataframe_to_header(df: pd.DataFrame, canonical_cols: List[str], source_meta: TableMeta, cfg: MultiPageConfig) -> pd.DataFrame:
    """Align a DataFrame to a canonical column structure."""
    df_copy = df.copy()
    if df.shape[1] < len(canonical_cols):
        for k in range(df.shape[1], len(canonical_cols)):
            df_copy[f"_pad_{k}"] = ""
    elif df.shape[1] > len(canonical_cols):
        df_copy = df_copy.iloc[:, :len(canonical_cols)]
    df_copy.columns = canonical_cols
    return df_copy


def _build_orphan_merged_table(
    header_idx: int, 
    all_members: List[int], 
    meta_by_idx: Dict[int, TableMeta]
) -> Tuple[pd.DataFrame, Set[int]]:
    """Build merged table when the anchor is a header orphan."""
    h_meta = meta_by_idx[header_idx]
    
    if h_meta.df.shape[0] == 0:
        header_cells = [str(c) for c in h_meta.df.columns]
    else:
        header_cells = [str(x) for x in h_meta.df.iloc[0].tolist()]
    
    data_members = [m for m in all_members if m != header_idx]
    max_w = max([len(header_cells)] + [meta_by_idx[m].width for m in data_members])
    canonical_cols = header_cells + [f"col_{k}" for k in range(len(header_cells), max_w)]
    
    rows = []
    prev = h_meta
    
    for m_idx in data_members:
        m = meta_by_idx[m_idx]
        
        # Handle continuation content
        if m.continuation_content and not rows and prev.is_header_orphan:
            for cc in m.continuation_content:
                if cc['col_idx'] < len(canonical_cols):
                    canonical_cols[cc['col_idx']] += " " + cc['value']
        elif m.continuation_content and rows:
            for cc in m.continuation_content:
                if cc['col_idx'] < max_w:
                    rows[-1][cc['col_idx']] += "\n" + cc['value']
        
        # Add data rows
        for _, r in m.df.iterrows():
            vals = [str(v) for v in r.tolist()]
            vals += [""] * (max_w - len(vals))
            rows.append(vals[:max_w])
        
        prev = m
    
    return pd.DataFrame(rows, columns=canonical_cols), set().union(*(meta_by_idx[i].pages for i in all_members))


def _build_generic_merged_table(
    members: List[int], 
    meta_by_idx: Dict[int, TableMeta], 
    cfg: MultiPageConfig
) -> Tuple[pd.DataFrame, Set[int]]:
    """Build merged table for the general case."""
    base = meta_by_idx[members[0]]
    merged_df = base.df.copy()
    canonical_cols = [str(c) for c in base.df.columns]
    merged_pages = set(base.pages)
    prev = base
    
    for idx in members[1:]:
        m = meta_by_idx[idx]
        
        # Handle continuation content
        if m.continuation_content and merged_df.shape[0] > 0:
            if (min(m.pages or [0]) - max(prev.pages or [0])) <= cfg.max_page_gap:
                for cc in m.continuation_content:
                    if cc['col_idx'] < merged_df.shape[1]:
                        curr = str(merged_df.iloc[-1, cc['col_idx']])
                        if curr and not is_empty_value(curr):
                            merged_df.iloc[-1, cc['col_idx']] += cfg.stitch_separator + cc['value']
        
        # Align and append
        aligned = align_dataframe_to_header(m.df, canonical_cols, m, cfg)
        merged_df = pd.concat([merged_df, aligned], ignore_index=True)
        merged_pages.update(m.pages)
        prev = m
    
    return merged_df, merged_pages


# -------------------------------------------------------------------
# 7. MAIN MERGE FUNCTION
# -------------------------------------------------------------------

def merge_multipage_tables(tables_meta: List[TableMeta], cfg: MultiPageConfig) -> List[LogicalTable]:
    """
    Merge table fragments into logical tables.
    
    This function implements three key principles:
    1. Sequential merging: Headerless fragments only merge with immediate predecessor
    2. Spillover detection: 1-column fragments are cell overflow, stitched into last cell
    3. Width matching: Same column count = same table structure
    
    Returns a list of LogicalTable objects representing merged tables.
    """
    n = len(tables_meta)
    if n == 0:
        return []
    
    uf = UnionFind(n)
    meta_by_idx = {t.idx: t for t in tables_meta}
    
    # Track spillover fragments for special handling during build
    spillover_targets: Dict[int, int] = {}  # spillover_idx -> target_table_idx
    
    # Sort tables by document order (page, then index)
    sorted_tables = sorted(tables_meta, key=lambda t: (t.start_page or 0, t.idx))
    
    # --- PASS 1: Sequential merging ---
    for i in range(1, len(sorted_tables)):
        tA = sorted_tables[i - 1]  # Previous table
        tB = sorted_tables[i]      # Current table
        
        if tA.start_page is None or tB.start_page is None:
            continue
        
        page_gap = tB.start_page - tA.start_page
        if page_gap < 1 or page_gap > cfg.max_page_gap:
            continue
        
        # --- SPILLOVER: 1-column fragment = cell overflow ---
        if is_spillover_fragment(tA, tB, cfg):
            spillover_targets[tB.idx] = tA.idx
            uf.union(tA.idx, tB.idx)
            log.debug(f"Spillover: Table {tB.idx} -> Table {tA.idx}")
            continue
        
        # --- WIDTH CHECK ---
        if abs(tA.width - tB.width) > cfg.max_width_difference:
            continue
        
        # --- HEADERLESS CONTINUATION ---
        if tB.is_headerless:
            if tA.width == tB.width:
                uf.union(tA.idx, tB.idx)
                log.debug(f"Width match: Table {tB.idx} -> Table {tA.idx}")
                continue
            
            # Fallback: row similarity
            row_sim = jaccard(tA.first_row_tokens, tB.first_row_tokens)
            if row_sim >= cfg.row_sim_threshold:
                uf.union(tA.idx, tB.idx)
                log.debug(f"Row similarity: Table {tB.idx} -> Table {tA.idx}")
                continue
        
        # --- REPEATED HEADER ---
        else:
            header_sim = jaccard(tA.header_tokens, tB.header_tokens)
            if header_sim >= cfg.header_sim_strict:
                uf.union(tA.idx, tB.idx)
                log.debug(f"Header match: Table {tB.idx} -> Table {tA.idx}")
                continue
    
    # --- PASS 2: Orphan repair ---
    page_map = defaultdict(list)
    for t in tables_meta:
        if t.start_page:
            page_map[t.start_page].append(t.idx)
    
    for p in page_map:
        for off in range(1, cfg.max_page_gap + 1):
            if (p + off) not in page_map:
                continue
            for i in page_map[p]:
                for j in page_map[p + off]:
                    if uf.find(i) == uf.find(j):
                        continue
                    tA, tB = meta_by_idx[i], meta_by_idx[j]
                    should, reason = should_force_orphan_merge(tA, tB, cfg)
                    if should:
                        uf.union(i, j)
                        log.debug(f"Orphan merge ({reason}): Table {j} -> Table {i}")

    # --- BUILD RESULTS ---
    groups = defaultdict(list)
    for t in tables_meta:
        groups[uf.find(t.idx)].append(t.idx)
    
    results = []
    for idx, members in enumerate(groups.values()):
        members = sorted(members, key=lambda x: (meta_by_idx[x].start_page or 0, x))
        
        # Separate spillover from normal members
        normal_members = [m for m in members if m not in spillover_targets]
        spillover_members = [m for m in members if m in spillover_targets]
        
        # Skip if no normal members (shouldn't happen)
        if not normal_members:
            continue
        
        has_header_orphan = any(meta_by_idx[m].is_header_orphan for m in normal_members)
        
        # Build the base merged table
        if has_header_orphan:
            df, pgs = _build_orphan_merged_table(normal_members[0], normal_members, meta_by_idx)
        else:
            df, pgs = _build_generic_merged_table(normal_members, meta_by_idx, cfg)
        
        # --- APPLY SPILLOVER CONTENT ---
        for spill_idx in spillover_members:
            spill_meta = meta_by_idx[spill_idx]
            if spill_meta.df.shape[0] > 0 and df.shape[0] > 0:
                # Collect all content from spillover fragment
                spill_content = cfg.stitch_separator.join(
                    str(spill_meta.df.iloc[r, 0])
                    for r in range(spill_meta.df.shape[0])
                    if str(spill_meta.df.iloc[r, 0]).strip()
                )
                
                if spill_content:
                    # Append to last row's last column
                    last_row = df.shape[0] - 1
                    last_col = df.shape[1] - 1
                    current_val = str(df.iloc[last_row, last_col])
                    df.iloc[last_row, last_col] = current_val + cfg.stitch_separator + spill_content
                    pgs.update(spill_meta.pages)
        
        # Final cleanup
        if len(pgs) > 1:
            df = stitch_split_cells(df, cfg.stitch_separator)
        df = clean_all_headers(df)
        
        results.append(LogicalTable(idx, members, sorted(pgs), df))
    
    return results