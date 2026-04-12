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
    DEFAULT_HEADERISH_TOKENS,
)
from ..merger import (
    normalize_col_name,
    tokenize,
    is_numeric_like_colnames,
    first_row_has_number,
)

log = logging.getLogger(__name__)


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


def _compute_vertical_positions(
    prov_list: List[Any],
    page_height: float = 842.0,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Compute normalized vertical positions (0-1 scale, top=0, bottom=1).
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

    # Determine if first row is header or data
    data_patterns = [
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

    non_empty_count = sum(1 for v in first_row if v and v.strip())
    is_sparse = (non_empty_count < num_cols / 2) and (not first_row[0].strip())

    is_headerless = False

    if has_data_values or has_url or is_sparse or has_repeated_values or has_placeholders:
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

def _dataframe_to_docling_data(
    df: pd.DataFrame,
    original_data: Optional[TableData] = None,
) -> TableData:
    """
    Converts a pandas DataFrame back into Docling's TableData structure.

    Creates simple 1x1 cells (no span detection from content).
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

    num_data_rows = len(df)
    num_cols = len(df.columns)
    num_total_rows = num_data_rows + 1

    has_row_headers = False
    if original_data and original_data.grid:
        for row in original_data.grid[1:]:
            if row and len(row) > 0 and row[0]:
                if getattr(row[0], 'row_header', False):
                    has_row_headers = True
                    break

    grid: List[List[TableCell]] = []
    table_cells: List[TableCell] = []

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

    for i, (_, row) in enumerate(df.iterrows()):
        grid_row: List[TableCell] = []
        table_row_idx = i + 1

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
        headerish_tokens = cfg.headerish_tokens or DEFAULT_HEADERISH_TOKENS
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
                vert_center, vert_top, vert_bottom = _compute_vertical_positions(prov)

            raw_columns = [str(c) for c in df.columns]
            numeric_like_cols = is_numeric_like_colnames(raw_columns)
            headerish_in_first_row = len(first_row_tokens & headerish_tokens)
            headerish_in_headers = len(header_tokens & headerish_tokens)

            is_header_orphan = (
                df.shape[0] <= cfg.max_orphan_rows and
                df.shape[0] >= 0 and
                (
                    (numeric_like_cols and headerish_in_first_row >= cfg.min_headerish_tokens) or
                    (headerish_in_headers >= 2 and df.shape[0] <= 1) or
                    df.shape[0] == 0
                )
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
