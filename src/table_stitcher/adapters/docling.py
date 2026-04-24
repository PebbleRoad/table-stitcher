"""
Docling adapter for table-stitcher.

Reads tables from a DoclingDocument and writes merged results back.
"""

import re
import logging
from typing import Any, List, Set, Optional, Tuple

import pandas as pd
from docling_core.types.doc import (
    DoclingDocument,
    TableData,
    TableCell,
)

from ..models import (
    MultiPageConfig,
    TableMeta,
    LogicalTable,
)
from ..merger import (
    normalize_col_name,
    tokenize,
    is_numeric_like_colnames,
    first_row_has_number,
)

log = logging.getLogger(__name__)


# -------------------------------------------------------------------
# Cell-shape heuristics (used for both headerless detection and
# structural header-orphan detection — shared so the two checks stay
# consistent).
# -------------------------------------------------------------------

# Patterns a cell matches when it looks like data rather than a header.
_DATA_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'^\d+$',
        r'^\d+\.\d+$',
        r'^\d{1,2}/\d{1,2}',
        r'^\d{1,2}-\d{1,2}',
        r'^https?://',
        r'^[A-Z]+-\d+$',
        r'^\$[\d,]+',
        r'^[\d,]+\s*%$',
        r'^Row\s*\d+',
        r'^\d+\.\d+\.\d+',
        r'^[\d,]+\.\d+$',               # financial: "13,085.03"
        r'^[\d,]+$',                    # grouped integer: "1,234,567"
        r'^\d+\.?\d*\s*\([\d,\s.]+\)',  # stat with range: "280 (176, 404)"
        r'^\d+\.?\d*\s*[xX×]\s*10',     # scientific: "7.0 x 10-7"
    ]
]

_AUTO_COLNAME_RE = re.compile(r"^(column|unnamed)[_:]?\s*\d+$", re.IGNORECASE)

# A cell is "header-shaped" when it's short, alphabetic-ish, and contains
# no data patterns. Used as the structural signal for orphan detection —
# no domain vocabulary involved.
_MAX_HEADER_CELL_LEN = 30


def _looks_like_data(cell: str) -> bool:
    s = str(cell).strip()
    if not s:
        return False
    return any(p.search(s) for p in _DATA_PATTERNS)


def _is_header_shaped_cell(cell: str) -> bool:
    """True if cell is plausibly a header cell — short, not data, not auto-label."""
    s = str(cell).strip()
    if not s:
        return True  # empty cells coexist with header cells
    if len(s) > _MAX_HEADER_CELL_LEN:
        return False
    if _AUTO_COLNAME_RE.match(s):
        return False
    if _looks_like_data(s):
        return False
    return True


def _detect_header_orphan(df: pd.DataFrame, is_headerless: bool, max_orphan_rows: int) -> bool:
    """
    Structural rule: a fragment is a header orphan when it's small,
    its first row was treated as a header (not promoted from data), and
    any data rows present look header-shaped too (no data patterns, short).

    Column names themselves are only screened for data patterns and
    auto-label form — NOT for length, because legitimate headers can be
    phrase-long (e.g. "Average annual revenue per customer"). Data rows,
    however, must be short AND non-data to qualify as header-shaped.

    No vocabulary is consulted — universal across domains and languages.
    """
    if is_headerless:
        return False
    if df.shape[0] > max_orphan_rows:
        return False

    cols = [str(c) for c in df.columns]
    # At least one meaningful column — not all empty / all auto-labels.
    meaningful = [c for c in cols if c.strip() and not _AUTO_COLNAME_RE.match(c)]
    if not meaningful:
        return False

    # Columns must not contain data patterns (numbers, currency, ranges).
    # Length is NOT checked here — real headers can be long phrases.
    if any(_looks_like_data(c) for c in cols):
        return False

    # Data rows (if any) must be header-shaped: short, non-data, non-auto.
    # A long or data-shaped value in a data row means this fragment carries
    # real data, not just orphaned header content.
    for _, row in df.iterrows():
        if not all(_is_header_shaped_cell(v) for v in row.tolist()):
            return False

    return True


# -------------------------------------------------------------------
# Docling-specific helpers (bbox / provenance)
# -------------------------------------------------------------------

def _extract_y_bounds_from_prov(prov_list: List[Any]) -> Optional[Tuple[float, float, str]]:
    """
    Extract vertical bounds from Docling provenance data.

    Returns: (y_min, y_max, coord_origin) or None if not available.
    """
    for p in prov_list:
        bbox = getattr(p, "bbox", None)
        if bbox is None:
            continue

        t = getattr(bbox, "t", None)
        b = getattr(bbox, "b", None)

        if t is not None and b is not None:
            coord_origin = getattr(bbox, "coord_origin", None)
            origin_str = str(coord_origin) if coord_origin else "BOTTOMLEFT"
            return (float(b), float(t), origin_str)

    return None


def _resolve_page_height(prov_list: List[Any], doc: Any, fallback: float = 842.0) -> float:
    """
    Look up the actual page height for the first prov entry from the document.
    Falls back to A4 (842pt) only when the document does not expose a size.
    """
    pages = getattr(doc, "pages", None)
    if not pages:
        return fallback
    for p in prov_list:
        page_no = getattr(p, "page_no", None)
        if page_no is None:
            continue
        page_item = pages.get(page_no) if hasattr(pages, "get") else None
        size = getattr(page_item, "size", None) if page_item else None
        height = getattr(size, "height", None) if size else None
        if height:
            return float(height)
    return fallback


def _compute_vertical_positions(
    prov_list: List[Any],
    page_height: float = 842.0,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Compute normalized vertical positions (0-1 scale, top=0, bottom=1).

    Caller should pass the actual page height for the page in question;
    the default of 842.0 (A4) is only a safety net for missing metadata.
    """
    bounds = _extract_y_bounds_from_prov(prov_list)
    if bounds is None:
        return None, None, None

    y_bottom, y_top, origin_str = bounds

    if "BOTTOMLEFT" in origin_str.upper():
        if y_top > page_height:
            page_height = max(y_top * 1.1, page_height)
        vert_top = 1.0 - (y_top / page_height)
        vert_bottom = 1.0 - (y_bottom / page_height)
    else:
        vert_top = y_top / page_height
        vert_bottom = y_bottom / page_height

    vert_top = max(0.0, min(1.0, vert_top))
    vert_bottom = max(0.0, min(1.0, vert_bottom))
    vert_center = (vert_top + vert_bottom) / 2.0

    return vert_center, vert_top, vert_bottom


# -------------------------------------------------------------------
# Grid-to-DataFrame conversion (Docling-specific)
# -------------------------------------------------------------------

def _grid_to_dataframe(table: Any, doc: Any) -> pd.DataFrame:
    """
    Convert Docling table grid to DataFrame with intelligent header detection.
    """
    if not hasattr(table, 'data') or not table.data or not hasattr(table.data, 'grid'):
        return table.export_to_dataframe(doc=doc)

    grid = table.data.grid
    if not grid:
        return pd.DataFrame()

    all_rows = []
    for row in grid:
        row_data = [getattr(cell, 'text', str(cell)) if cell else '' for cell in row]
        all_rows.append(row_data)

    if not all_rows:
        return pd.DataFrame()

    real_content_rows = [r for r in all_rows if any(c.strip() for c in r)]

    if not real_content_rows:
        return pd.DataFrame(columns=[f"Column_{i}" for i in range(len(all_rows[0]))])

    first_row = real_content_rows[0]
    num_cols = len(first_row)

    # Determine if first row is header or data — uses module-level
    # _looks_like_data and _is_header_shaped_cell to stay consistent with
    # structural orphan detection below.
    has_data_values = any(_looks_like_data(c) for c in first_row)
    has_url = any("http" in str(c).lower() for c in first_row)

    non_empty_vals = [str(c).strip().upper() for c in first_row if str(c).strip()]
    if len(non_empty_vals) >= 3:
        unique_vals = set(non_empty_vals)
        repetition_ratio = len(unique_vals) / len(non_empty_vals)
        has_repeated_values = repetition_ratio < 0.5
        placeholder_vals = {'DATA', 'N/A', 'NA', 'NULL', '-', '0', 'TBD', 'NONE', 'YES', 'NO'}
        has_placeholders = len(unique_vals & placeholder_vals) > 0
    else:
        has_repeated_values = False
        has_placeholders = False

    # Real headers are typically short (≤30 chars); a majority of long cells
    # in the "header" row usually means we're looking at a data row whose
    # true header was eaten by the parser on this page.
    non_empty_cells = [str(c).strip() for c in first_row if str(c).strip()]
    long_cells = sum(1 for c in non_empty_cells if len(c) > 30)
    has_long_cells = bool(non_empty_cells) and long_cells / len(non_empty_cells) >= 0.5

    non_empty_count = sum(1 for v in first_row if v and v.strip())
    is_sparse = (non_empty_count < num_cols / 2) and (not first_row[0].strip())

    is_headerless = False

    if (has_data_values or has_url or is_sparse or has_repeated_values
            or has_placeholders or has_long_cells):
        is_headerless = True
        header = [f"Column_{i}" for i in range(num_cols)]

        if is_sparse and len(real_content_rows) > 1:
            pre_header_rows = [first_row]
            data_rows = real_content_rows[1:]
        else:
            pre_header_rows = []
            data_rows = real_content_rows
    else:
        is_headerless = False
        pre_header_rows = []
        header = first_row
        data_rows = real_content_rows[1:]

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

    df.attrs['pre_header_rows'] = pre_header_rows
    df.attrs['is_headerless'] = is_headerless
    return df


# -------------------------------------------------------------------
# DataFrame → Docling TableData conversion
# -------------------------------------------------------------------

def _extract_original_header_rows(
    original_data: Optional[TableData],
) -> Tuple[List[List[TableCell]], List[TableCell]]:
    """
    Extract header rows from the anchor table's original grid.

    Returns (header_grid_rows, flat_header_cells).
    If the original data has multi-row headers with rowspan/colspan,
    they are preserved exactly as-is.
    """
    if not original_data or not original_data.grid:
        return [], []

    header_rows: List[List[TableCell]] = []
    header_cells: List[TableCell] = []

    for row in original_data.grid:
        if row and any(getattr(c, 'column_header', False) for c in row if c):
            header_rows.append(row)
            header_cells.extend(c for c in row if c)
        else:
            break  # first non-header row = end of header

    return header_rows, header_cells


def _dataframe_to_docling_data(
    df: pd.DataFrame,
    original_data: Optional[TableData] = None,
) -> TableData:
    """
    Converts a pandas DataFrame back into Docling's TableData structure.

    When ``original_data`` is provided and contains multi-row header rows
    (cells with ``column_header=True``, rowspan, colspan), those header rows
    are preserved exactly.  Only the data rows are rebuilt from the DataFrame.
    This prevents the lossy roundtrip that would flatten complex headers into
    simple 1x1 cells.
    """
    if df.empty:
        cols = list(df.columns) if len(df.columns) > 0 else ["Column_0"]
        header_cells = []
        for j, col_name in enumerate(cols):
            cell = TableCell(
                text=str(col_name) if col_name is not None else "",
                row_span=1,
                col_span=1,
                column_header=True,
                row_header=False,
                start_row_offset_idx=0,
                end_row_offset_idx=1,
                start_col_offset_idx=j,
                end_col_offset_idx=j + 1,
            )
            header_cells.append(cell)
        return TableData(
            num_rows=1,
            num_cols=len(cols),
            table_cells=header_cells,
            grid=[header_cells],
        )

    # --- Try to reuse original header rows (preserves rowspan/colspan) ---
    orig_header_rows, orig_header_cells = _extract_original_header_rows(original_data)

    num_cols = len(df.columns)

    if orig_header_rows:
        # Use original header rows as-is
        num_header_rows = len(orig_header_rows)
        grid: List[List[TableCell]] = list(orig_header_rows)
        table_cells: List[TableCell] = list(orig_header_cells)
    else:
        # Fall back to building flat 1x1 header from DataFrame columns
        num_header_rows = 1
        grid = []
        table_cells = []

        header_row_cells = []
        for j, col_name in enumerate(df.columns):
            cell = TableCell(
                text=str(col_name) if col_name is not None else "",
                row_span=1,
                col_span=1,
                column_header=True,
                row_header=False,
                start_row_offset_idx=0,
                end_row_offset_idx=1,
                start_col_offset_idx=j,
                end_col_offset_idx=j + 1,
            )
            header_row_cells.append(cell)
            table_cells.append(cell)

        grid.append(header_row_cells)

    # --- Detect row_header styling from original data ---
    has_row_headers = False
    if original_data and original_data.grid:
        for row in original_data.grid[num_header_rows:]:
            if row and len(row) > 0 and row[0]:
                if getattr(row[0], 'row_header', False):
                    has_row_headers = True
                    break

    # --- Build data rows from merged DataFrame ---
    for i, (_, row) in enumerate(df.iterrows()):
        grid_row: List[TableCell] = []
        table_row_idx = num_header_rows + i

        for j, val in enumerate(row):
            if pd.isna(val) or val is None:
                text_val = ""
            else:
                text_val = str(val)

            row_header = (j == 0 and has_row_headers)

            cell = TableCell(
                text=text_val,
                row_span=1,
                col_span=1,
                column_header=False,
                row_header=row_header,
                start_row_offset_idx=table_row_idx,
                end_row_offset_idx=table_row_idx + 1,
                start_col_offset_idx=j,
                end_col_offset_idx=j + 1,
            )
            grid_row.append(cell)
            table_cells.append(cell)

        grid.append(grid_row)

    num_total_rows = num_header_rows + len(df)

    return TableData(
        num_rows=num_total_rows,
        num_cols=num_cols,
        table_cells=table_cells,
        grid=grid
    )


# -------------------------------------------------------------------
# Reference pointer helper
# -------------------------------------------------------------------

def _get_ref_pointer(ref_obj: Any) -> str:
    """Safely extract the string pointer (e.g., '#/tables/1') from a Ref object."""
    if hasattr(ref_obj, "ref"):
        return ref_obj.ref

    if hasattr(ref_obj, "model_dump"):
        data = ref_obj.model_dump(by_alias=True)
        return data.get("$ref", "")

    if isinstance(ref_obj, dict):
        return ref_obj.get("$ref", "")

    return ""


# -------------------------------------------------------------------
# DoclingAdapter
# -------------------------------------------------------------------

class DoclingAdapter:
    """
    Table-stitcher adapter for Docling (docling-core).

    Reads tables from a ``DoclingDocument`` and writes merged results back.
    """

    def extract(self, doc: DoclingDocument, cfg: MultiPageConfig) -> List[TableMeta]:
        """Extract metadata from all tables in a DoclingDocument."""
        tables_meta: List[TableMeta] = []
        total = len(doc.tables)
        skipped = 0

        for idx, table in enumerate(doc.tables):
            try:
                df = _grid_to_dataframe(table, doc)
            except Exception as e:
                log.warning(f"Skipping table {idx}/{total}: extraction failed ({e}). "
                            "Original table will be preserved unchanged.")
                skipped += 1
                continue

            continuation_content = []
            pre_header_rows = df.attrs.get('pre_header_rows', [])
            is_headerless = df.attrs.get('is_headerless', False)

            if pre_header_rows:
                for row in pre_header_rows:
                    non_empty = [(i, v) for i, v in enumerate(row) if v and v.strip()]
                    for col_idx, val in non_empty:
                        continuation_content.append({'col_idx': col_idx, 'value': val})

            prov = getattr(table, "prov", None) or []
            pages = sorted({p.page_no for p in prov}) if prov else []
            start_page = pages[0] if pages else None

            header_tokens: Set[str] = set()
            for col in df.columns:
                header_tokens |= tokenize(normalize_col_name(col))

            first_row_tokens: Set[str] = set()
            if df.shape[0] > 0:
                row_text = " ".join(str(x) for x in df.iloc[0].tolist())
                first_row_tokens = tokenize(row_text)

            vert_center, vert_top, vert_bottom = None, None, None
            if cfg.use_layout_hint and prov:
                page_height = _resolve_page_height(prov, doc)
                vert_center, vert_top, vert_bottom = _compute_vertical_positions(
                    prov, page_height=page_height
                )

            raw_columns = [str(c) for c in df.columns]
            numeric_like_cols = is_numeric_like_colnames(raw_columns)

            is_header_orphan = _detect_header_orphan(
                df, is_headerless, cfg.max_orphan_rows
            )

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

        if skipped:
            log.warning(f"Extracted {len(tables_meta)}/{total} tables "
                        f"({skipped} skipped — originals preserved)")

        return tables_meta

    def inject(self, doc: DoclingDocument, logical_tables: List[LogicalTable]) -> DoclingDocument:
        """
        Modify the DoclingDocument in-place with merged table data.

        Only modifies tables that were actually merged (multiple fragments).
        Single-page tables retain their original Docling structure.
        """
        log.info("Starting DoclingDocument injection...")

        refs_to_remove: Set[str] = set()

        for lt in logical_tables:
            if not lt.members:
                continue

            if len(lt.members) == 1:
                log.debug(f"Skipping single-table {lt.members[0]} - preserving original structure")
                continue

            anchor_idx = lt.members[0]
            anchor_table = doc.tables[anchor_idx]

            log.info(f"Injecting Logical Table {lt.logical_index} into Anchor Table {anchor_idx} "
                     f"(merged from {len(lt.members)} fragments)")

            original_data = getattr(anchor_table, 'data', None)

            anchor_table.data = _dataframe_to_docling_data(
                lt.df,
                original_data=original_data,
            )

            for satellite_idx in lt.members[1:]:
                satellite_table = doc.tables[satellite_idx]

                if satellite_table.prov:
                    if anchor_table.prov is None:
                        anchor_table.prov = []

                    if isinstance(satellite_table.prov, list):
                        if isinstance(anchor_table.prov, list):
                            anchor_table.prov.extend(satellite_table.prov)
                        else:
                            anchor_table.prov = [anchor_table.prov] + satellite_table.prov
                    else:
                        if isinstance(anchor_table.prov, list):
                            anchor_table.prov.append(satellite_table.prov)
                        else:
                            anchor_table.prov = [anchor_table.prov, satellite_table.prov]

                refs_to_remove.add(satellite_table.self_ref)

                # Clear the satellite in place so downstream code iterating
                # doc.tables directly doesn't see stale fragment content.
                # We don't pop the entry because self_refs are position-based
                # (`#/tables/N` = list index N) — removing an element would
                # shift every subsequent self_ref and body reference. The
                # satellite becomes an empty shell, still present but
                # without data or prov.
                satellite_table.data = TableData(
                    num_rows=0, num_cols=0, table_cells=[], grid=[]
                )
                satellite_table.prov = []

        # Prune satellite references from body hierarchy
        removed_count = 0

        def traverse_and_prune(group_node: Any):
            nonlocal removed_count
            if not hasattr(group_node, 'children'):
                return

            new_children = []
            for child_ref in group_node.children:
                ptr = _get_ref_pointer(child_ref)

                if not ptr:
                    new_children.append(child_ref)
                    continue

                if ptr in refs_to_remove:
                    removed_count += 1
                    continue

                new_children.append(child_ref)

                if ptr.startswith("#/groups/"):
                    try:
                        group_idx = int(ptr.split("/")[-1])
                        if group_idx < len(doc.groups):
                            traverse_and_prune(doc.groups[group_idx])
                    except (ValueError, IndexError):
                        pass

            group_node.children = new_children

        if doc.body:
            traverse_and_prune(doc.body)

        log.info(f"Injection complete. Pruned {removed_count} satellite table references.")
        return doc
