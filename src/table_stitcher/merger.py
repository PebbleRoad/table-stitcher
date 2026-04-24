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
import unicodedata
import logging
from collections import defaultdict
from typing import Any, List, Set, Tuple, Optional, Dict

import pandas as pd

from .models import MultiPageConfig, TableMeta, LogicalTable, MergeTrace

log = logging.getLogger(__name__)


# -------------------------------------------------------------------
# 1. UTILITY FUNCTIONS
# -------------------------------------------------------------------

def normalize_col_name(col: Any) -> str:
    """Normalize column name for comparison."""
    return str(col).strip().lower()


# Scripts where each character is semantically its own token, because the
# script doesn't use whitespace between words (CJK family, Thai, Lao, Khmer,
# Myanmar, Tibetan). Per-character Jaccard works for similarity comparison:
# identical headers produce identical character sets; unrelated headers
# rarely cross the ~60% overlap required to hit the strict threshold.
#
# This list is bounded — Unicode regularly adds new scripts, but almost all
# new scripts are whitespace-using (and therefore handled as words without
# a code change). Only the separator-less family needs enumeration.
_SEPARATORLESS_SCRIPTS: Set[str] = {
    "Han",       # Chinese / Japanese kanji / Korean hanja
    "Hiragana",
    "Katakana",
    "Hangul",
    "Thai",
    "Lao",
    "Khmer",
    "Myanmar",
    "Tibetan",
}

# Map a substring of the Unicode character name to a script tag. Unicode
# character names are standardized and frozen, so this mapping is stable
# across Python and Unicode releases.
_NAME_TO_SCRIPT: List[Tuple[str, str]] = [
    ("CJK", "Han"),
    ("KANGXI", "Han"),          # e.g. U+2F49 "KANGXI RADICAL MOON"
    ("HIRAGANA", "Hiragana"),
    ("KATAKANA", "Katakana"),
    ("HANGUL", "Hangul"),
    ("THAI", "Thai"),
    ("LAO", "Lao"),
    ("KHMER", "Khmer"),
    ("MYANMAR", "Myanmar"),
    ("TIBETAN", "Tibetan"),
]


def _script_of(ch: str) -> Optional[str]:
    """Return a script tag for `ch`, or None for scripts that use whitespace."""
    if ord(ch) < 128:   # ASCII fast path — by far the common case in Latin text
        return None
    name = unicodedata.name(ch, "")
    if not name:
        return None
    for prefix, script in _NAME_TO_SCRIPT:
        if prefix in name:
            return script
    return None


def tokenize(text: str) -> Set[str]:
    """
    Extract tokens for Jaccard similarity comparison — script-aware.

    Rules, all structural (no language models, no external dependencies):

    - Characters in separator-less scripts (CJK, Thai, Lao, Khmer, Myanmar,
      Tibetan): each character is its own token. Unigram Jaccard — identical
      headers produce identical token sets.
    - Other alphabetic characters (Latin, Cyrillic, Greek, Arabic, Hebrew,
      Devanagari, Tamil, ...): grouped into whitespace-separated words,
      lowercased. These scripts have word boundaries at whitespace, so the
      same rule that works for English works for them.
    - Digits, punctuation, and whitespace: ignored — boundaries only.

    Mixed-script text (e.g., "Sales + non-Latin run") produces the union of
    both token sets.
    """
    tokens: Set[str] = set()
    buf: List[str] = []
    for ch in str(text):
        # Check script BEFORE isalpha: Kangxi radicals (U+2F00–U+2FDF) and
        # some CJK compatibility characters are classed as symbols, not
        # letters, but still belong to Han script for tokenization purposes.
        if _script_of(ch) in _SEPARATORLESS_SCRIPTS:
            if buf:
                tokens.add("".join(buf).lower())
                buf.clear()
            tokens.add(ch)
        elif ch.isalpha():
            buf.append(ch)
        else:
            # Non-letter boundary (digit, punctuation, whitespace) — flush.
            if buf:
                tokens.add("".join(buf).lower())
                buf.clear()
    if buf:
        tokens.add("".join(buf).lower())
    return tokens


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


def _pair_signals(tA: TableMeta, tB: TableMeta, cfg: MultiPageConfig) -> Dict[str, Any]:
    """Collect stable, parser-neutral signals for merge explanations."""
    page_gap = None
    if tA.start_page is not None and tB.start_page is not None:
        page_gap = tB.start_page - tA.start_page

    return {
        "left_page": tA.start_page,
        "right_page": tB.start_page,
        "page_gap": page_gap,
        "left_width": tA.width,
        "right_width": tB.width,
        "width_diff": abs(tA.width - tB.width),
        "left_headerless": tA.is_headerless,
        "right_headerless": tB.is_headerless,
        "left_header_orphan": tA.is_header_orphan,
        "right_header_orphan": tB.is_header_orphan,
        "left_data_orphan": tA.is_data_orphan,
        "right_data_orphan": tB.is_data_orphan,
        "header_similarity": jaccard(tA.header_tokens, tB.header_tokens),
        "row_similarity": jaccard(tA.first_row_tokens, tB.first_row_tokens),
        "layout_continuation": layout_suggests_continuation(tA, tB, cfg),
    }


def _trace_pair(
    tA: TableMeta,
    tB: TableMeta,
    cfg: MultiPageConfig,
    merged: bool,
    reason: str,
    warnings: Optional[List[str]] = None,
) -> MergeTrace:
    """Build a MergeTrace for one adjacent pair."""
    return MergeTrace(
        left_idx=tA.idx,
        right_idx=tB.idx,
        merged=merged,
        reason=reason,
        signals=_pair_signals(tA, tB, cfg),
        warnings=warnings or [],
    )


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
    - On the **immediately** following page (page_gap == 1)

    The immediate-next-page constraint is load-bearing: a 1-col fragment
    several pages later is almost certainly an unrelated small table, not a
    continuation. Independent of `cfg.max_page_gap` — which governs the
    general merge search but shouldn't apply to spillover, since the semantic
    is "cell overflow" and overflow physically lands on the very next page.

    By default, the structural signal is strong enough. Content checking
    (URLs, ticket patterns) is optional via spillover_require_content_check.
    """
    if not (tB.is_headerless and tB.width == 1 and tA.width > 1):
        return False
    if tA.start_page is None or tB.start_page is None:
        return False
    if tB.start_page - tA.start_page != 1:
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

    Uses positional (integer) indexing throughout — pandas' label-based
    indexing breaks when a merged DataFrame has duplicate column names
    (common with rowspan/colspan parsers), because df[col] returns a
    sub-DataFrame rather than a scalar.
    """
    if df.shape[0] <= 1:
        return df

    cols = list(df.columns)
    ncols = len(cols)
    stitched_rows = []
    i = 0
    n = df.shape[0]

    while i < n:
        row = df.iloc[i].tolist()
        j = i + 1

        while j < n:
            next_row_vals = df.iloc[j].tolist()
            nonempty_idxs = [k for k, v in enumerate(next_row_vals)
                             if not is_empty_value(v)]

            if len(nonempty_idxs) != 1:
                break

            cont_idx = nonempty_idxs[0]
            cont_val = str(next_row_vals[cont_idx]).strip()
            target_idx = cont_idx

            is_url = "://" in cont_val or cont_val.lower().startswith("http")
            if is_url:
                candidates = [
                    k for k, c in enumerate(cols)
                    if any(x in str(c).lower()
                           for x in ['content', 'ref', 'desc', 'link', 'url'])
                ]
                if candidates:
                    target_idx = candidates[-1]
                else:
                    target_idx = ncols - 1

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
    """
    Align a DataFrame to a canonical column structure.

    Narrower fragments are right-padded with empty columns.
    Wider fragments follow ``cfg.width_overflow_policy``:

    - ``preserve_extra``: add trailing ``_extra_N`` columns.
    - ``warn_drop``: drop trailing columns and log a warning.
    - ``fail``: raise ``ValueError``.
    - ``merge_tail``: append trailing values into the final canonical cell.
    """
    valid_policies = {"preserve_extra", "warn_drop", "fail", "merge_tail"}
    if cfg.width_overflow_policy not in valid_policies:
        raise ValueError(
            "width_overflow_policy must be one of "
            f"{sorted(valid_policies)}, got {cfg.width_overflow_policy!r}"
        )

    df_copy = df.copy()
    warnings: List[str] = []
    if df.shape[1] < len(canonical_cols):
        for k in range(df.shape[1], len(canonical_cols)):
            df_copy[f"_pad_{k}"] = ""
    elif df.shape[1] > len(canonical_cols):
        dropped = df.shape[1] - len(canonical_cols)
        dropped_cols = [str(c) for c in df.columns[len(canonical_cols):]]
        source_idx = getattr(source_meta, "idx", None)
        source_page = getattr(source_meta, "start_page", None)

        if cfg.width_overflow_policy == "fail":
            raise ValueError(
                "Fragment idx=%s page=%s has %d columns, wider than "
                "canonical width %d; extra columns: %s"
                % (source_idx, source_page, df.shape[1], len(canonical_cols), dropped_cols)
            )

        if cfg.width_overflow_policy == "warn_drop":
            warning = (
                "Dropped %d trailing column(s) from fragment idx=%s page=%s "
                "to fit canonical width %d; dropped columns: %s"
                % (dropped, source_idx, source_page, len(canonical_cols), dropped_cols)
            )
            log.warning("align_dataframe_to_header: %s", warning)
            warnings.append(warning)
            df_copy = df_copy.iloc[:, :len(canonical_cols)]
            df_copy.columns = canonical_cols
            df_copy.attrs["table_stitcher_warnings"] = warnings
            return df_copy

        if cfg.width_overflow_policy == "merge_tail":
            rows = []
            for _, row in df.iterrows():
                vals = list(row.tolist())
                head_vals = vals[:len(canonical_cols)]
                tail_vals = [
                    str(v).strip() for v in vals[len(canonical_cols):]
                    if not is_empty_value(v)
                ]
                while len(head_vals) < len(canonical_cols):
                    head_vals.append("")
                if tail_vals and canonical_cols:
                    last_idx = len(canonical_cols) - 1
                    tail_text = cfg.stitch_separator.join(tail_vals)
                    if is_empty_value(head_vals[last_idx]):
                        head_vals[last_idx] = tail_text
                    else:
                        head_vals[last_idx] = (
                            str(head_vals[last_idx]).rstrip()
                            + cfg.stitch_separator
                            + tail_text
                        )
                rows.append(head_vals)
            df_copy = pd.DataFrame(rows, columns=canonical_cols)
            return df_copy

        # Default and safest behavior: keep every trailing value in explicit
        # overflow columns instead of silently losing data.
        extra_cols = []
        used = {str(c) for c in canonical_cols}
        for offset, col in enumerate(df.columns[len(canonical_cols):]):
            base = f"_extra_{offset}_{str(col).strip() or 'column'}"
            candidate = base
            suffix = 1
            while candidate in used:
                candidate = f"{base}_{suffix}"
                suffix += 1
            used.add(candidate)
            extra_cols.append(candidate)
        df_copy.columns = canonical_cols + extra_cols
        return df_copy

    df_copy.columns = canonical_cols
    df_copy.attrs["table_stitcher_warnings"] = warnings
    return df_copy


def _build_orphan_merged_table(
    header_idx: int,
    all_members: List[int],
    meta_by_idx: Dict[int, TableMeta]
) -> Tuple[pd.DataFrame, Set[int], List[str]]:
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

    return (
        pd.DataFrame(rows, columns=canonical_cols),
        set().union(*(meta_by_idx[i].pages for i in all_members)),
        [],
    )


def _build_generic_merged_table(
    members: List[int],
    meta_by_idx: Dict[int, TableMeta],
    cfg: MultiPageConfig
) -> Tuple[pd.DataFrame, Set[int], List[str]]:
    """Build merged table for the general case."""
    base = meta_by_idx[members[0]]
    merged_df = base.df.copy()
    canonical_cols = [str(c) for c in base.df.columns]
    merged_pages = set(base.pages)
    warnings: List[str] = []
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
        warnings.extend(aligned.attrs.get("table_stitcher_warnings", []))
        merged_df = pd.concat([merged_df, aligned], ignore_index=True).fillna("")
        canonical_cols = [str(c) for c in merged_df.columns]
        merged_pages.update(m.pages)
        prev = m

    return merged_df, merged_pages, warnings


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
    decision_traces: List[MergeTrace] = []

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
            decision_traces.append(_trace_pair(tA, tB, cfg, False, "missing_page"))
            continue

        page_gap = tB.start_page - tA.start_page
        if page_gap < 1 or page_gap > cfg.max_page_gap:
            decision_traces.append(_trace_pair(tA, tB, cfg, False, "page_gap_out_of_range"))
            continue

        # Guard: skip if any table index between tA and tB was not extracted.
        # A skipped table means an unknown fragment sits between them in
        # document order — merging across it risks false positives.
        if tB.idx - tA.idx > 1:
            gap_indices = set(range(tA.idx + 1, tB.idx))
            if not gap_indices.issubset(extracted_indices):
                missing = sorted(gap_indices - extracted_indices)
                log.debug(f"Skipping pair {tA.idx}->{tB.idx}: "
                          f"unextracted table(s) {set(missing)} between them")
                decision_traces.append(_trace_pair(
                    tA, tB, cfg, False, "unextracted_table_between",
                    [f"unextracted table indices between pair: {missing}"],
                ))
                continue

        posA, posB = orig_to_pos[tA.idx], orig_to_pos[tB.idx]

        # --- SPILLOVER: 1-column fragment = cell overflow ---
        if is_spillover_fragment(tA, tB, cfg):
            spillover_targets[tB.idx] = tA.idx
            uf.union(posA, posB)
            decision_traces.append(_trace_pair(tA, tB, cfg, True, "spillover"))
            log.debug(f"Spillover: Table {tB.idx} -> Table {tA.idx}")
            continue

        # --- ORPHAN HEADER starts a new table: don't merge into tA ---
        # A header-orphan fragment is structurally a lone header row for the
        # NEXT table, not a continuation of the previous one. Skip the merge
        # attempt; later passes pair it with its own data fragment.
        if tB.is_header_orphan:
            decision_traces.append(_trace_pair(tA, tB, cfg, False, "right_header_orphan_starts_next_table"))
            continue

        # --- WIDTH CHECK ---
        width_diff = abs(tA.width - tB.width)
        if cfg.require_same_width and width_diff > 0:
            decision_traces.append(_trace_pair(tA, tB, cfg, False, "require_same_width"))
            continue
        if width_diff > cfg.max_width_difference:
            decision_traces.append(_trace_pair(tA, tB, cfg, False, "width_difference_too_large"))
            continue

        # --- HEADER ORPHAN → HEADERLESS DATA ---
        # Header orphans often have truncated width (empty cells dropped by
        # the parser); trust the data fragment's width when the two are
        # consecutive and within the general width-diff tolerance.
        if tA.is_header_orphan and tB.is_headerless:
            uf.union(posA, posB)
            decision_traces.append(_trace_pair(tA, tB, cfg, True, "header_orphan_to_headerless"))
            log.debug(f"Header orphan → headerless: Table {tB.idx} -> Table {tA.idx}")
            continue

        # --- HEADERLESS CONTINUATION ---
        if tB.is_headerless:
            if tA.width == tB.width:
                uf.union(posA, posB)
                decision_traces.append(_trace_pair(tA, tB, cfg, True, "headerless_width_match"))
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
                decision_traces.append(_trace_pair(tA, tB, cfg, True, "headerless_width_drift_layout"))
                log.debug(f"Width-drift headerless "
                          f"(±{cfg.headerless_width_tolerance} + layout): "
                          f"Table {tB.idx} -> Table {tA.idx}")
                continue

            row_sim = jaccard(tA.first_row_tokens, tB.first_row_tokens)
            if row_sim >= cfg.row_sim_threshold:
                uf.union(posA, posB)
                decision_traces.append(_trace_pair(tA, tB, cfg, True, "row_similarity"))
                log.debug(f"Row similarity: Table {tB.idx} -> Table {tA.idx}")
                continue

            decision_traces.append(_trace_pair(tA, tB, cfg, False, "headerless_no_signal"))

        # --- REPEATED HEADER ---
        else:
            header_sim = jaccard(tA.header_tokens, tB.header_tokens)
            if header_sim >= cfg.header_sim_strict:
                uf.union(posA, posB)
                decision_traces.append(_trace_pair(tA, tB, cfg, True, "header_similarity_strict"))
                log.debug(f"Header match: Table {tB.idx} -> Table {tA.idx}")
                continue

            # Fallback: accept looser similarity when layout confirms continuation
            if header_sim >= cfg.header_sim_loose and layout_suggests_continuation(tA, tB, cfg):
                uf.union(posA, posB)
                decision_traces.append(_trace_pair(tA, tB, cfg, True, "header_similarity_loose_layout"))
                log.debug(f"Header match (loose+layout): Table {tB.idx} -> Table {tA.idx}")
                continue

            decision_traces.append(_trace_pair(tA, tB, cfg, False, "header_similarity_too_low"))

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
                            missing = sorted(gap_indices - extracted_indices)
                            log.debug(f"Skipping orphan pair {i}->{j}: "
                                      f"unextracted table(s) "
                                      f"{set(missing)} between them")
                            continue

                    tA, tB = meta_by_idx[i], meta_by_idx[j]
                    should, reason = should_force_orphan_merge(tA, tB, cfg)
                    if should:
                        uf.union(posI, posJ)
                        decision_traces.append(_trace_pair(tA, tB, cfg, True, reason or "orphans"))
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
            df, pgs, build_warnings = _build_orphan_merged_table(header_orphan_idx, normal_members, meta_by_idx)
        else:
            df, pgs, build_warnings = _build_generic_merged_table(normal_members, meta_by_idx, cfg)

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

        member_set = set(members)
        group_traces = [
            tr for tr in decision_traces
            if tr.left_idx in member_set and tr.right_idx in member_set
        ]
        merge_reasons = [tr.reason for tr in group_traces if tr.merged]
        group_warnings = list(build_warnings)
        for tr in group_traces:
            group_warnings.extend(tr.warnings)

        results.append(LogicalTable(
            idx,
            members,
            sorted(pgs),
            df,
            merge_reason="+".join(merge_reasons),
            merge_traces=group_traces,
            warnings=group_warnings,
        ))

    return results
