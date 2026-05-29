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

import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from .models import LogicalTable, MergeTrace, MultiPageConfig, TableMeta

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
_SEPARATORLESS_SCRIPTS: set[str] = {
    "Han",  # Chinese / Japanese kanji / Korean hanja
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
_NAME_TO_SCRIPT: list[tuple[str, str]] = [
    ("CJK", "Han"),
    ("KANGXI", "Han"),  # e.g. U+2F49 "KANGXI RADICAL MOON"
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
    if ord(ch) < 128:  # ASCII fast path — by far the common case in Latin text
        return None
    name = unicodedata.name(ch, "")
    if not name:
        return None
    for prefix, script in _NAME_TO_SCRIPT:
        if prefix in name:
            return script
    return None


def tokenize(text: str) -> set[str]:
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
    tokens: set[str] = set()
    buf: list[str] = []
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


def jaccard(a: set[str], b: set[str]) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def is_numeric_like_colnames(cols: list[Any]) -> bool:
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


def _pair_signals(tA: TableMeta, tB: TableMeta, cfg: MultiPageConfig) -> dict[str, Any]:
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
    warnings: Optional[list[str]] = None,
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


def _both_have_unique_header_tokens(tA: TableMeta, tB: TableMeta) -> bool:
    """
    True when each side's header set has at least one token the other lacks.

    This is the structural signature of *parallel* tables sharing domain
    vocabulary (e.g. clinical studies that share patient/age/sex but differ
    on outcome column), not of a single table split across pages. A real
    continuation has either identical headers or tB ⊆ tA — parsers may
    drop columns on page 2 but cannot invent header tokens that weren't on
    page 1. So when both sides bring their own tokens, header similarity
    alone is unsafe; we require layout corroboration before merging.
    """
    a, b = tA.header_tokens, tB.header_tokens
    if not a or not b:
        return False
    return bool(a - b) and bool(b - a)


def should_force_orphan_merge(h: TableMeta, d: TableMeta, cfg: MultiPageConfig) -> tuple[bool, str]:
    """Check if header orphan + data orphan should merge."""
    if h.start_page is None or d.start_page is None:
        return False, ""
    if (d.start_page - h.start_page) > cfg.max_page_gap:
        return False, ""
    if abs(h.width - d.width) > cfg.max_width_difference:
        return False, ""
    # Intervening-content guard — sibling of the one in _classify_sequential_pair.
    # Pass 2 reaches this without going through that function, so the guard must
    # be repeated here: a heading before the data fragment means a new table.
    if cfg.block_on_intervening_content and d.content_before:
        return False, "content_between_tables"

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
        "http" in first_cell
        or "://" in first_cell
        or bool(re.search(r"[A-Z]+-\d+", str(tB.df.iloc[0, 0])))
        or tB.row_count <= 2
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
            nonempty_idxs = [k for k, v in enumerate(next_row_vals) if not is_empty_value(v)]

            if len(nonempty_idxs) != 1:
                break

            # A genuine continuation always has col 0 empty — that column
            # is the record identifier (participant ID, row label, etc.).
            # A non-empty col 0 means a new record or a category/section
            # row, not an overflow of the previous cell.
            if not is_empty_value(next_row_vals[0]):
                break

            cont_idx = nonempty_idxs[0]
            cont_val = str(next_row_vals[cont_idx]).strip()
            target_idx = cont_idx

            is_url = "://" in cont_val or cont_val.lower().startswith("http")
            if is_url:
                candidates = [
                    k
                    for k, c in enumerate(cols)
                    if any(x in str(c).lower() for x in ["content", "ref", "desc", "link", "url"])
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


_VALID_WIDTH_OVERFLOW_POLICIES = {"preserve_extra", "warn_drop", "fail", "merge_tail"}


def _pad_narrow(df: pd.DataFrame, canonical_cols: list[str]) -> pd.DataFrame:
    """Right-pad a narrower fragment with empty ``_pad_N`` columns."""
    df_copy = df.copy()
    for k in range(df.shape[1], len(canonical_cols)):
        df_copy[f"_pad_{k}"] = ""
    df_copy.columns = canonical_cols
    df_copy.attrs["table_stitcher_warnings"] = []
    return df_copy


def _overflow_fail(
    df: pd.DataFrame, canonical_cols: list[str], source_meta: TableMeta, cfg: MultiPageConfig
) -> pd.DataFrame:
    dropped_cols = [str(c) for c in df.columns[len(canonical_cols) :]]
    raise ValueError(
        f"Fragment idx={getattr(source_meta, 'idx', None)} "
        f"page={getattr(source_meta, 'start_page', None)} has {df.shape[1]} columns, "
        f"wider than canonical width {len(canonical_cols)}; extra columns: {dropped_cols}"
    )


def _overflow_warn_drop(
    df: pd.DataFrame, canonical_cols: list[str], source_meta: TableMeta, cfg: MultiPageConfig
) -> pd.DataFrame:
    dropped = df.shape[1] - len(canonical_cols)
    dropped_cols = [str(c) for c in df.columns[len(canonical_cols) :]]
    warning = (
        f"Dropped {dropped} trailing column(s) from fragment "
        f"idx={getattr(source_meta, 'idx', None)} "
        f"page={getattr(source_meta, 'start_page', None)} "
        f"to fit canonical width {len(canonical_cols)}; dropped columns: {dropped_cols}"
    )
    log.warning("align_dataframe_to_header: %s", warning)

    df_copy = df.iloc[:, : len(canonical_cols)].copy()
    df_copy.columns = canonical_cols
    df_copy.attrs["table_stitcher_warnings"] = [warning]
    return df_copy


def _overflow_merge_tail(
    df: pd.DataFrame, canonical_cols: list[str], source_meta: TableMeta, cfg: MultiPageConfig
) -> pd.DataFrame:
    """Fold trailing overflow cells into the last canonical column."""
    rows = []
    for _, row in df.iterrows():
        vals = list(row.tolist())
        head_vals = vals[: len(canonical_cols)]
        tail_vals = [str(v).strip() for v in vals[len(canonical_cols) :] if not is_empty_value(v)]
        while len(head_vals) < len(canonical_cols):
            head_vals.append("")
        if tail_vals and canonical_cols:
            last_idx = len(canonical_cols) - 1
            tail_text = cfg.stitch_separator.join(tail_vals)
            if is_empty_value(head_vals[last_idx]):
                head_vals[last_idx] = tail_text
            else:
                head_vals[last_idx] = (
                    str(head_vals[last_idx]).rstrip() + cfg.stitch_separator + tail_text
                )
        rows.append(head_vals)
    df_copy = pd.DataFrame(rows, columns=canonical_cols)
    df_copy.attrs["table_stitcher_warnings"] = []
    return df_copy


def _overflow_preserve_extra(
    df: pd.DataFrame, canonical_cols: list[str], source_meta: TableMeta, cfg: MultiPageConfig
) -> pd.DataFrame:
    """Keep overflow cells in explicit ``_extra_N_<origname>`` columns (default, lossless)."""
    df_copy = df.copy()
    extra_cols: list[str] = []
    used = {str(c) for c in canonical_cols}
    for offset, col in enumerate(df.columns[len(canonical_cols) :]):
        base = f"_extra_{offset}_{str(col).strip() or 'column'}"
        candidate = base
        suffix = 1
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        extra_cols.append(candidate)
    df_copy.columns = canonical_cols + extra_cols
    df_copy.attrs["table_stitcher_warnings"] = []
    return df_copy


_WIDTH_OVERFLOW_HANDLERS = {
    "preserve_extra": _overflow_preserve_extra,
    "warn_drop": _overflow_warn_drop,
    "fail": _overflow_fail,
    "merge_tail": _overflow_merge_tail,
}


def align_dataframe_to_header(
    df: pd.DataFrame,
    canonical_cols: list[str],
    source_meta: TableMeta,
    cfg: MultiPageConfig,
) -> pd.DataFrame:
    """
    Align a DataFrame to a canonical column structure.

    Narrower fragments are right-padded with empty columns.
    Wider fragments dispatch to a handler keyed by ``cfg.width_overflow_policy``:

    - ``preserve_extra`` (default): add trailing ``_extra_N_<origname>`` columns.
    - ``warn_drop``: drop trailing columns and log a warning.
    - ``fail``: raise ``ValueError``.
    - ``merge_tail``: append trailing values into the final canonical cell.
    """
    if cfg.width_overflow_policy not in _VALID_WIDTH_OVERFLOW_POLICIES:
        raise ValueError(
            "width_overflow_policy must be one of "
            f"{sorted(_VALID_WIDTH_OVERFLOW_POLICIES)}, got {cfg.width_overflow_policy!r}"
        )

    if df.shape[1] < len(canonical_cols):
        return _pad_narrow(df, canonical_cols)

    if df.shape[1] > len(canonical_cols):
        return _WIDTH_OVERFLOW_HANDLERS[cfg.width_overflow_policy](
            df, canonical_cols, source_meta, cfg
        )

    # Exact width match — just relabel and carry an empty warnings list.
    df_copy = df.copy()
    df_copy.columns = canonical_cols
    df_copy.attrs["table_stitcher_warnings"] = []
    return df_copy


def _build_orphan_merged_table(
    header_idx: int, all_members: list[int], meta_by_idx: dict[int, TableMeta]
) -> tuple[pd.DataFrame, set[int], list[str]]:
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
                if cc["col_idx"] < len(canonical_cols):
                    canonical_cols[cc["col_idx"]] += " " + cc["value"]
        elif m.continuation_content and rows:
            for cc in m.continuation_content:
                if cc["col_idx"] < max_w:
                    rows[-1][cc["col_idx"]] += "\n" + cc["value"]

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
    members: list[int], meta_by_idx: dict[int, TableMeta], cfg: MultiPageConfig
) -> tuple[pd.DataFrame, set[int], list[str]]:
    """Build merged table for the general case."""
    base = meta_by_idx[members[0]]
    merged_df = base.df.copy()
    canonical_cols = [str(c) for c in base.df.columns]
    merged_pages = set(base.pages)
    warnings: list[str] = []
    prev = base

    for idx in members[1:]:
        m = meta_by_idx[idx]

        if m.continuation_content and merged_df.shape[0] > 0:
            if (min(m.pages or [0]) - max(prev.pages or [0])) <= cfg.max_page_gap:
                for cc in m.continuation_content:
                    if cc["col_idx"] < merged_df.shape[1]:
                        curr = str(merged_df.iloc[-1, cc["col_idx"]])
                        if curr and not is_empty_value(curr):
                            merged_df.iloc[-1, cc["col_idx"]] += cfg.stitch_separator + cc["value"]

        aligned = align_dataframe_to_header(m.df, canonical_cols, m, cfg)
        warnings.extend(aligned.attrs.get("table_stitcher_warnings", []))
        merged_df = pd.concat([merged_df, aligned], ignore_index=True).fillna("")
        canonical_cols = [str(c) for c in merged_df.columns]
        merged_pages.update(m.pages)
        prev = m

    return merged_df, merged_pages, warnings


# -------------------------------------------------------------------
# 5. MAIN MERGE FUNCTION
#
# The main `merge_multipage_tables` function reads as four named phases:
# setup → Pass 1 (sequential) → Pass 2 (orphan repair) → build results.
# Each phase is a helper that takes `_MergeState` plus cfg; state holds
# the cross-phase data (union-find, index maps, traces).
# -------------------------------------------------------------------


@dataclass
class _MergeState:
    """Mutable state passed between the phases of merge_multipage_tables."""

    uf: UnionFind
    tables_meta: list[TableMeta]
    meta_by_idx: dict[int, TableMeta]
    orig_to_pos: dict[int, int]
    sorted_tables: list[TableMeta]
    extracted_indices: set[int]
    spillover_targets: dict[int, int] = field(default_factory=dict)
    decision_traces: list[MergeTrace] = field(default_factory=list)


def _init_merge_state(tables_meta: list[TableMeta]) -> _MergeState:
    """Build the shared state for one merge invocation."""
    # Original t.idx values may be non-contiguous when table extraction
    # fails for some tables. Positional index maps bridge that gap.
    orig_to_pos = {t.idx: pos for pos, t in enumerate(tables_meta)}
    return _MergeState(
        uf=UnionFind(len(tables_meta)),
        tables_meta=tables_meta,
        meta_by_idx={t.idx: t for t in tables_meta},
        orig_to_pos=orig_to_pos,
        sorted_tables=sorted(tables_meta, key=lambda t: (t.start_page or 0, t.idx)),
        extracted_indices={t.idx for t in tables_meta},
    )


def _classify_sequential_pair(
    tA: TableMeta,
    tB: TableMeta,
    cfg: MultiPageConfig,
) -> tuple[bool, str, bool, list[str]]:
    """
    Decide whether two adjacent-in-document-order fragments should merge.

    Returns ``(should_merge, reason, is_spillover, warnings)``. The caller
    handles the actual union and trace bookkeeping; this function is pure
    logic over the pair's signals. Keeping it pure makes every merge
    decision independently reviewable.
    """
    # --- Page-adjacency guard ---
    if tA.start_page is None or tB.start_page is None:
        return False, "missing_page", False, []
    page_gap = tB.start_page - tA.start_page
    if page_gap < 1 or page_gap > cfg.max_page_gap:
        return False, "page_gap_out_of_range", False, []

    # --- Intervening-content guard ---
    # A genuine page-split continuation has nothing but page furniture between
    # its fragments. Substantive body content (a heading, paragraph, list item,
    # or figure) between tA and tB means they are separate tables that merely
    # share a column schema, not one table split across a page break. The
    # adapter computes this in reading order (furniture, captions and footnotes
    # are already filtered out); ``None`` means position unknown, so we defer to
    # the other signals rather than block.
    if cfg.block_on_intervening_content and tB.content_before:
        return False, "content_between_tables", False, []

    # --- Spillover (checked before width guards since spillover can cross
    # width boundaries legitimately: 1-col fragment follows N-col table) ---
    if is_spillover_fragment(tA, tB, cfg):
        return True, "spillover", True, []

    # --- Right-side header orphan starts a new table, not a continuation ---
    if tB.is_header_orphan:
        return False, "right_header_orphan_starts_next_table", False, []

    # --- Width guards ---
    width_diff = abs(tA.width - tB.width)
    if cfg.require_same_width and width_diff > 0:
        return False, "require_same_width", False, []
    if width_diff > cfg.max_width_difference:
        return False, "width_difference_too_large", False, []

    # --- Header orphan on the left + headerless data on the right:
    # trust the data fragment's width (header orphans often have
    # truncated widths from empty cells dropped by the parser). ---
    if tA.is_header_orphan and tB.is_headerless:
        return True, "header_orphan_to_headerless", False, []

    # --- Headerless continuation ---
    if tB.is_headerless:
        if tA.width == tB.width:
            # When tA also has no real header, width alone is not enough —
            # two independent same-width tables would always match. Require
            # layout (tA near page bottom → tB near page top) to confirm the
            # table actually overflowed onto the next page.
            if not tA.is_headerless or layout_suggests_continuation(tA, tB, cfg):
                return True, "headerless_width_match", False, []
        if width_diff <= cfg.headerless_width_tolerance and layout_suggests_continuation(
            tA, tB, cfg
        ):
            return True, "headerless_width_drift_layout", False, []
        if jaccard(tA.first_row_tokens, tB.first_row_tokens) >= cfg.row_sim_threshold:
            return True, "row_similarity", False, []
        return False, "headerless_no_signal", False, []

    # --- Repeated-header continuation ---
    header_sim = jaccard(tA.header_tokens, tB.header_tokens)
    layout = layout_suggests_continuation(tA, tB, cfg)

    if header_sim >= cfg.header_sim_strict:
        # Strict path normally trusts similarity alone. But when both sides
        # carry unique tokens, we're seeing parallel tables sharing domain
        # vocabulary (clinical studies, quarterly reports) — a continuation
        # would have identical headers or tB ⊆ tA. Demand layout in that case.
        if _both_have_unique_header_tokens(tA, tB) and not layout:
            return False, "header_similarity_strict_disjoint_tokens", False, []
        return True, "header_similarity_strict", False, []
    if header_sim >= cfg.header_sim_loose and layout:
        return True, "header_similarity_loose_layout", False, []
    return False, "header_similarity_too_low", False, []


def _pass1_sequential_merge(state: _MergeState, cfg: MultiPageConfig) -> None:
    """
    Walk document-order-adjacent pairs and union them by the rules in
    ``_classify_sequential_pair``. Records a MergeTrace for every pair
    (merged or not) so downstream consumers can audit the decision stream.
    """
    sorted_tables = state.sorted_tables
    for i in range(1, len(sorted_tables)):
        tA, tB = sorted_tables[i - 1], sorted_tables[i]

        # Continuity guard: if any table index between tA and tB failed to
        # extract, an unknown fragment sits between them and merging risks
        # false positives.
        if tB.idx - tA.idx > 1:
            gap_indices = set(range(tA.idx + 1, tB.idx))
            if not gap_indices.issubset(state.extracted_indices):
                missing = sorted(gap_indices - state.extracted_indices)
                log.debug(
                    f"Skipping pair {tA.idx}->{tB.idx}: "
                    f"unextracted table(s) {set(missing)} between them"
                )
                state.decision_traces.append(
                    _trace_pair(
                        tA,
                        tB,
                        cfg,
                        False,
                        "unextracted_table_between",
                        [f"unextracted table indices between pair: {missing}"],
                    )
                )
                continue

        should_merge, reason, is_spillover, warnings = _classify_sequential_pair(tA, tB, cfg)
        state.decision_traces.append(_trace_pair(tA, tB, cfg, should_merge, reason, warnings))

        if not should_merge:
            continue

        if is_spillover:
            state.spillover_targets[tB.idx] = tA.idx

        state.uf.union(state.orig_to_pos[tA.idx], state.orig_to_pos[tB.idx])
        log.debug(f"Merge ({reason}): Table {tB.idx} -> Table {tA.idx}")


def _pass2_orphan_repair(state: _MergeState, cfg: MultiPageConfig) -> None:
    """
    Second pass: pair any not-yet-unioned fragments across pages when
    one is a header orphan and the other is a data orphan. This catches
    cases Pass 1 misses because the two aren't document-order-adjacent.
    """
    page_map: dict[int, list[int]] = defaultdict(list)
    for t in state.tables_meta:
        if t.start_page is not None:
            page_map[t.start_page].append(t.idx)

    for p in page_map:
        for off in range(1, cfg.max_page_gap + 1):
            if (p + off) not in page_map:
                continue
            for i in page_map[p]:
                for j in page_map[p + off]:
                    posI, posJ = state.orig_to_pos[i], state.orig_to_pos[j]
                    if state.uf.find(posI) == state.uf.find(posJ):
                        continue

                    # Same continuity guard as Pass 1.
                    lo, hi = (i, j) if i < j else (j, i)
                    if hi - lo > 1:
                        gap_indices = set(range(lo + 1, hi))
                        if not gap_indices.issubset(state.extracted_indices):
                            missing = sorted(gap_indices - state.extracted_indices)
                            log.debug(
                                f"Skipping orphan pair {i}->{j}: "
                                f"unextracted table(s) {set(missing)} between them"
                            )
                            continue

                    tA, tB = state.meta_by_idx[i], state.meta_by_idx[j]
                    should, reason = should_force_orphan_merge(tA, tB, cfg)
                    if should:
                        state.uf.union(posI, posJ)
                        state.decision_traces.append(
                            _trace_pair(tA, tB, cfg, True, reason or "orphans")
                        )
                        log.debug(f"Orphan merge ({reason}): Table {j} -> Table {i}")


def _apply_spillover(
    df: pd.DataFrame,
    pgs: set[int],
    spillover_members: list[int],
    meta_by_idx: dict[int, TableMeta],
    cfg: MultiPageConfig,
) -> None:
    """
    Stitch each spillover fragment's content into the last cell of df
    (in-place). Extracted for readability — the build phase would
    otherwise nest this loop four levels deep.
    """
    for spill_idx in spillover_members:
        spill_meta = meta_by_idx[spill_idx]
        if spill_meta.df.shape[0] == 0 or df.shape[0] == 0:
            continue
        spill_content = cfg.stitch_separator.join(
            str(spill_meta.df.iloc[r, 0])
            for r in range(spill_meta.df.shape[0])
            if str(spill_meta.df.iloc[r, 0]).strip()
        )
        if not spill_content:
            continue

        last_row_idx = df.shape[0] - 1
        last_col_idx = df.shape[1] - 1
        raw_val = df.iloc[last_row_idx, last_col_idx]
        current_val = "" if pd.isna(raw_val) else str(raw_val).strip()
        if current_val:
            df.iloc[last_row_idx, last_col_idx] = current_val + cfg.stitch_separator + spill_content
        else:
            df.iloc[last_row_idx, last_col_idx] = spill_content
        pgs.update(spill_meta.pages)


def _build_logical_tables(state: _MergeState, cfg: MultiPageConfig) -> list[LogicalTable]:
    """
    Collapse the union-find groups into a list of LogicalTable objects.
    Handles spillover application, orphan-anchor vs generic build paths,
    post-merge cell stitching, and attaches per-group merge traces.
    """
    groups: dict[int, list[int]] = defaultdict(list)
    for t in state.tables_meta:
        groups[state.uf.find(state.orig_to_pos[t.idx])].append(t.idx)

    results: list[LogicalTable] = []
    for idx, members in enumerate(groups.values()):
        members = sorted(members, key=lambda x: (state.meta_by_idx[x].start_page or 0, x))

        normal_members = [m for m in members if m not in state.spillover_targets]
        spillover_members = [m for m in members if m in state.spillover_targets]
        if not normal_members:
            continue

        header_orphan_idx = next(
            (m for m in normal_members if state.meta_by_idx[m].is_header_orphan),
            None,
        )
        if header_orphan_idx is not None:
            df, pgs, build_warnings = _build_orphan_merged_table(
                header_orphan_idx, normal_members, state.meta_by_idx
            )
        else:
            df, pgs, build_warnings = _build_generic_merged_table(
                normal_members, state.meta_by_idx, cfg
            )

        _apply_spillover(df, pgs, spillover_members, state.meta_by_idx, cfg)

        if len(pgs) > 1:
            df = stitch_split_cells(df, cfg.stitch_separator)
        df = clean_all_headers(df)

        member_set = set(members)
        group_traces = [
            tr
            for tr in state.decision_traces
            if tr.left_idx in member_set and tr.right_idx in member_set
        ]
        merge_reasons = [tr.reason for tr in group_traces if tr.merged]
        group_warnings = list(build_warnings)
        for tr in group_traces:
            group_warnings.extend(tr.warnings)

        results.append(
            LogicalTable(
                idx,
                members,
                sorted(pgs),
                df,
                merge_reason="+".join(merge_reasons),
                merge_traces=group_traces,
                warnings=group_warnings,
            )
        )

    return results


def merge_multipage_tables(
    tables_meta: list[TableMeta],
    cfg: MultiPageConfig,
) -> list[LogicalTable]:
    """
    Merge table fragments into logical tables.

    The merge engine runs in four named phases:

    1. **Setup** (``_init_merge_state``) — build index maps, union-find,
       and sort fragments into document order.
    2. **Sequential merge** (``_pass1_sequential_merge``) — walk adjacent
       pairs; union them by structural rules in ``_classify_sequential_pair``.
    3. **Orphan repair** (``_pass2_orphan_repair``) — catch any header/data
       orphan pairs Pass 1 missed.
    4. **Build results** (``_build_logical_tables``) — group by union-find
       root, apply spillover content, stitch split cells, attach traces.

    Returns a list of ``LogicalTable`` objects, each with ``merge_reason``,
    ``merge_traces``, and ``warnings`` populated for downstream auditing.
    """
    if not tables_meta:
        return []

    state = _init_merge_state(tables_meta)
    _pass1_sequential_merge(state, cfg)
    _pass2_orphan_repair(state, cfg)
    return _build_logical_tables(state, cfg)
