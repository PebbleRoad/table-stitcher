"""
Core merge engine for table-stitcher.

This module is parser-agnostic. It operates exclusively on TableMeta objects
and pandas DataFrames — it never touches parser-native document objects.

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

from .models import MultiPageConfig, TableMeta, LogicalTable

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


# -------------------------------------------------------------------
# 2. UNION-FIND DATA STRUCTURE
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
# 3. MERGE DECISION LOGIC
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
        return True

    if tB.df.shape[0] == 0:
        return False

    first_cell = str(tB.df.iloc[0, 0]).lower()
    looks_like_continuation = (
        "http" in first_cell or
        "://" in first_cell or
        bool(re.search(r'[A-Z]+-\d+', str(tB.df.iloc[0, 0]))) or
        tB.row_count <= 2
    )
    return looks_like_continuation


# -------------------------------------------------------------------
# 4. TABLE BUILDING (Post-Merge)
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
                    target_idx = len(cols) - 1

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

        if m.continuation_content and not rows and prev.is_header_orphan:
            for cc in m.continuation_content:
                if cc['col_idx'] < len(canonical_cols):
                    canonical_cols[cc['col_idx']] += " " + cc['value']
        elif m.continuation_content and rows:
            for cc in m.continuation_content:
                if cc['col_idx'] < max_w:
                    rows[-1][cc['col_idx']] += "\n" + cc['value']

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

        if m.continuation_content and merged_df.shape[0] > 0:
            if (min(m.pages or [0]) - max(prev.pages or [0])) <= cfg.max_page_gap:
                for cc in m.continuation_content:
                    if cc['col_idx'] < merged_df.shape[1]:
                        curr = str(merged_df.iloc[-1, cc['col_idx']])
                        if curr and not is_empty_value(curr):
                            merged_df.iloc[-1, cc['col_idx']] += cfg.stitch_separator + cc['value']

        aligned = align_dataframe_to_header(m.df, canonical_cols, m, cfg)
        merged_df = pd.concat([merged_df, aligned], ignore_index=True)
        merged_pages.update(m.pages)
        prev = m

    return merged_df, merged_pages


# -------------------------------------------------------------------
# 5. MAIN MERGE FUNCTION
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

    # Build bidirectional mapping between original doc indices and
    # positional indices (0..n-1).  Original t.idx values may be
    # non-contiguous when table extraction fails for some tables.
    orig_to_pos: Dict[int, int] = {}
    pos_to_orig: Dict[int, int] = {}
    for pos, t in enumerate(tables_meta):
        orig_to_pos[t.idx] = pos
        pos_to_orig[pos] = t.idx

    uf = UnionFind(n)
    meta_by_idx = {t.idx: t for t in tables_meta}

    # Track spillover fragments for special handling during build
    spillover_targets: Dict[int, int] = {}  # spillover_idx -> target_table_idx

    # Sort tables by document order (page, then index)
    sorted_tables = sorted(tables_meta, key=lambda t: (t.start_page or 0, t.idx))

    # Build set of extracted indices for continuity checks.
    # If a table between tA and tB was skipped during extraction,
    # we cannot safely assume tB continues tA.
    extracted_indices = {t.idx for t in tables_meta}

    # --- PASS 1: Sequential merging ---
    for i in range(1, len(sorted_tables)):
        tA = sorted_tables[i - 1]
        tB = sorted_tables[i]

        if tA.start_page is None or tB.start_page is None:
            continue

        page_gap = tB.start_page - tA.start_page
        if page_gap < 1 or page_gap > cfg.max_page_gap:
            continue

        # Guard: skip if any table index between tA and tB was not extracted.
        # A skipped table means an unknown fragment sits between them in
        # document order — merging across it risks false positives.
        if tB.idx - tA.idx > 1:
            gap_indices = set(range(tA.idx + 1, tB.idx))
            if not gap_indices.issubset(extracted_indices):
                log.debug(f"Skipping pair {tA.idx}->{tB.idx}: "
                          f"unextracted table(s) {gap_indices - extracted_indices} between them")
                continue

        posA, posB = orig_to_pos[tA.idx], orig_to_pos[tB.idx]

        # --- SPILLOVER: 1-column fragment = cell overflow ---
        if is_spillover_fragment(tA, tB, cfg):
            spillover_targets[tB.idx] = tA.idx
            uf.union(posA, posB)
            log.debug(f"Spillover: Table {tB.idx} -> Table {tA.idx}")
            continue

        # --- ORPHAN HEADER starts a new table: don't merge into tA ---
        # A header-orphan fragment is structurally a lone header row for the
        # NEXT table, not a continuation of the previous one. Skip the merge
        # attempt; later passes pair it with its own data fragment.
        if tB.is_header_orphan:
            continue

        # --- WIDTH CHECK ---
        width_diff = abs(tA.width - tB.width)
        if cfg.require_same_width and width_diff > 0:
            continue
        if width_diff > cfg.max_width_difference:
            continue

        # --- HEADER ORPHAN → HEADERLESS DATA ---
        # Header orphans often have truncated width (empty cells dropped by
        # the parser); trust the data fragment's width when the two are
        # consecutive and within the general width-diff tolerance.
        if tA.is_header_orphan and tB.is_headerless:
            uf.union(posA, posB)
            log.debug(f"Header orphan → headerless: Table {tB.idx} -> Table {tA.idx}")
            continue

        # --- HEADERLESS CONTINUATION ---
        if tB.is_headerless:
            if tA.width == tB.width:
                uf.union(posA, posB)
                log.debug(f"Width match: Table {tB.idx} -> Table {tA.idx}")
                continue

            # Width tolerance when layout confirms continuation —
            # real-world parser drift on headerless fragments often runs to
            # a couple of columns (empty cells collapsed, stray single
            # cells added). Layout confirmation prevents false positives
            # across unrelated tables with coincidentally close widths.
            if (width_diff <= cfg.headerless_width_tolerance
                    and layout_suggests_continuation(tA, tB, cfg)):
                uf.union(posA, posB)
                log.debug(f"Width-drift headerless "
                          f"(±{cfg.headerless_width_tolerance} + layout): "
                          f"Table {tB.idx} -> Table {tA.idx}")
                continue

            row_sim = jaccard(tA.first_row_tokens, tB.first_row_tokens)
            if row_sim >= cfg.row_sim_threshold:
                uf.union(posA, posB)
                log.debug(f"Row similarity: Table {tB.idx} -> Table {tA.idx}")
                continue

        # --- REPEATED HEADER ---
        else:
            header_sim = jaccard(tA.header_tokens, tB.header_tokens)
            if header_sim >= cfg.header_sim_strict:
                uf.union(posA, posB)
                log.debug(f"Header match: Table {tB.idx} -> Table {tA.idx}")
                continue

            # Fallback: accept looser similarity when layout confirms continuation
            if header_sim >= cfg.header_sim_loose and layout_suggests_continuation(tA, tB, cfg):
                uf.union(posA, posB)
                log.debug(f"Header match (loose+layout): Table {tB.idx} -> Table {tA.idx}")
                continue

    # --- PASS 2: Orphan repair ---
    page_map = defaultdict(list)
    for t in tables_meta:
        if t.start_page is not None:
            page_map[t.start_page].append(t.idx)

    for p in page_map:
        for off in range(1, cfg.max_page_gap + 1):
            if (p + off) not in page_map:
                continue
            for i in page_map[p]:
                for j in page_map[p + off]:
                    posI, posJ = orig_to_pos[i], orig_to_pos[j]
                    if uf.find(posI) == uf.find(posJ):
                        continue

                    # Same continuity guard as Pass 1: an unextracted table
                    # sitting between i and j is an unknown fragment, and
                    # merging across it risks false positives.
                    lo, hi = (i, j) if i < j else (j, i)
                    if hi - lo > 1:
                        gap_indices = set(range(lo + 1, hi))
                        if not gap_indices.issubset(extracted_indices):
                            log.debug(f"Skipping orphan pair {i}->{j}: "
                                      f"unextracted table(s) "
                                      f"{gap_indices - extracted_indices} between them")
                            continue

                    tA, tB = meta_by_idx[i], meta_by_idx[j]
                    should, reason = should_force_orphan_merge(tA, tB, cfg)
                    if should:
                        uf.union(posI, posJ)
                        log.debug(f"Orphan merge ({reason}): Table {j} -> Table {i}")

    # --- BUILD RESULTS ---
    groups = defaultdict(list)
    for t in tables_meta:
        groups[uf.find(orig_to_pos[t.idx])].append(t.idx)

    results = []
    for idx, members in enumerate(groups.values()):
        members = sorted(members, key=lambda x: (meta_by_idx[x].start_page or 0, x))

        normal_members = [m for m in members if m not in spillover_targets]
        spillover_members = [m for m in members if m in spillover_targets]

        if not normal_members:
            continue

        # Find the actual header orphan (if any) to use as anchor
        header_orphan_idx = next(
            (m for m in normal_members if meta_by_idx[m].is_header_orphan), None
        )

        if header_orphan_idx is not None:
            df, pgs = _build_orphan_merged_table(header_orphan_idx, normal_members, meta_by_idx)
        else:
            df, pgs = _build_generic_merged_table(normal_members, meta_by_idx, cfg)

        # --- APPLY SPILLOVER CONTENT ---
        for spill_idx in spillover_members:
            spill_meta = meta_by_idx[spill_idx]
            if spill_meta.df.shape[0] > 0 and df.shape[0] > 0:
                spill_content = cfg.stitch_separator.join(
                    str(spill_meta.df.iloc[r, 0])
                    for r in range(spill_meta.df.shape[0])
                    if str(spill_meta.df.iloc[r, 0]).strip()
                )

                if spill_content:
                    last_row = df.shape[0] - 1
                    last_col = df.shape[1] - 1
                    raw_val = df.iloc[last_row, last_col]
                    if pd.isna(raw_val):
                        current_val = ""
                    else:
                        current_val = str(raw_val).strip()
                    if current_val:
                        df.iloc[last_row, last_col] = (
                            current_val + cfg.stitch_separator + spill_content
                        )
                    else:
                        df.iloc[last_row, last_col] = spill_content
                    pgs.update(spill_meta.pages)

        if len(pgs) > 1:
            df = stitch_split_cells(df, cfg.stitch_separator)
        df = clean_all_headers(df)

        results.append(LogicalTable(idx, members, sorted(pgs), df))

    return results
