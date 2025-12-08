import logging
from typing import List, Set, Any, Optional, Dict, Tuple
import pandas as pd
from docling_core.types.doc import (
    DoclingDocument,
    TableData,
    TableCell,
)

from .models import LogicalTable

log = logging.getLogger(__name__)


def extract_original_cell_metadata(table_data: Optional[TableData]) -> Tuple[
    Dict[Tuple[int, int], Tuple[bool, bool]],  # (row, col) -> (col_header, row_header)
    Dict[Tuple[int, int], Tuple[int, int]],    # (row, col) -> (row_span, col_span)
]:
    """
    Extract header flags and span info from original TableData.
    """
    headers: Dict[Tuple[int, int], Tuple[bool, bool]] = {}
    spans: Dict[Tuple[int, int], Tuple[int, int]] = {}
    
    if not table_data or not table_data.grid:
        return headers, spans
    
    for i, row in enumerate(table_data.grid):
        for j, cell in enumerate(row):
            if cell:
                headers[(i, j)] = (
                    getattr(cell, 'column_header', False),
                    getattr(cell, 'row_header', False)
                )
                spans[(i, j)] = (
                    getattr(cell, 'row_span', 1) or 1,
                    getattr(cell, 'col_span', 1) or 1
                )
    
    return headers, spans


def build_grid_from_dataframe(df: pd.DataFrame) -> List[List[str]]:
    """
    Build a unified grid (header + data) from DataFrame.
    """
    grid: List[List[str]] = []
    
    # Row 0: headers
    header_row = [str(c) if c is not None else "" for c in df.columns]
    grid.append(header_row)
    
    # Rows 1+: data
    for _, row in df.iterrows():
        data_row = []
        for val in row:
            if pd.isna(val) or val is None:
                data_row.append("")
            else:
                data_row.append(str(val))
        grid.append(data_row)
    
    return grid


def detect_col_spans(grid: List[List[str]]) -> Tuple[
    Dict[Tuple[int, int], int],  # (row, col) -> col_span
    Set[Tuple[int, int]],        # covered cells
]:
    """
    Detect column spans by finding consecutive identical non-empty values in a row.
    
    Only detects spans where cells have EXACTLY identical content (strong signal).
    """
    col_spans: Dict[Tuple[int, int], int] = {}
    covered: Set[Tuple[int, int]] = set()
    
    for i, row in enumerate(grid):
        j = 0
        while j < len(row):
            if (i, j) in covered:
                j += 1
                continue
            
            val = row[j]
            span = 1
            
            # Only consider non-empty values for span detection
            if val and val.strip():
                while j + span < len(row):
                    next_val = row[j + span]
                    # Must be EXACTLY identical (not just similar)
                    if next_val == val:
                        covered.add((i, j + span))
                        span += 1
                    else:
                        break
            
            col_spans[(i, j)] = span
            j += span
    
    return col_spans, covered


def detect_row_spans(
    grid: List[List[str]], 
    covered: Set[Tuple[int, int]]
) -> Tuple[
    Dict[Tuple[int, int], int],  # (row, col) -> row_span
    Set[Tuple[int, int]],        # updated covered cells
]:
    """
    Detect row spans by finding consecutive identical non-empty values in a column.
    """
    row_spans: Dict[Tuple[int, int], int] = {}
    
    if not grid:
        return row_spans, covered
    
    num_rows = len(grid)
    num_cols = len(grid[0]) if grid else 0
    
    for j in range(num_cols):
        i = 0
        while i < num_rows:
            if (i, j) in covered:
                i += 1
                continue
            
            val = grid[i][j] if j < len(grid[i]) else ""
            span = 1
            
            # Only consider non-empty values
            if val and val.strip():
                while i + span < num_rows:
                    if j < len(grid[i + span]):
                        next_val = grid[i + span][j]
                        if next_val == val and (i + span, j) not in covered:
                            covered.add((i + span, j))
                            span += 1
                        else:
                            break
                    else:
                        break
            
            row_spans[(i, j)] = span
            i += span
    
    return row_spans, covered


def dataframe_to_docling_data(
    df: pd.DataFrame,
    original_anchor_data: Optional[TableData] = None,
    preserve_spans: bool = True,
) -> TableData:
    """
    Converts a Pandas DataFrame back into the Docling TableData structure.
    
    Properly handles:
    - Column headers (from original or row 0)
    - Row headers (from original or column 0 if flagged)
    - Row spans and column spans (detected from content patterns)
    - Covered cells (cells "under" a span are not duplicated)
    
    Args:
        df: The DataFrame to convert
        original_anchor_data: Original TableData to preserve metadata from
        preserve_spans: If True, detect and preserve merged cells
    
    Returns:
        TableData with proper structure
    """
    if df.empty:
        return TableData(
            num_rows=1,
            num_cols=len(df.columns) or 1,
            table_cells=[],
            grid=[[]]
        )
    
    # Extract original metadata
    orig_headers, orig_spans = extract_original_cell_metadata(original_anchor_data)
    
    # Build grid from DataFrame
    grid_values = build_grid_from_dataframe(df)
    num_rows = len(grid_values)
    num_cols = len(df.columns)
    
    # Detect spans from content patterns
    col_spans: Dict[Tuple[int, int], int] = {}
    row_spans: Dict[Tuple[int, int], int] = {}
    covered: Set[Tuple[int, int]] = set()
    
    if preserve_spans:
        col_spans, covered = detect_col_spans(grid_values)
        row_spans, covered = detect_row_spans(grid_values, covered)
    
    # Build the TableData grid
    grid: List[List[TableCell]] = []
    table_cells: List[TableCell] = []
    
    for i in range(num_rows):
        grid_row: List[TableCell] = []
        
        for j in range(num_cols):
            if (i, j) in covered:
                # This cell is covered by a span from another cell - skip
                continue
            
            # Get text value
            text_val = grid_values[i][j] if j < len(grid_values[i]) else ""
            
            # Determine spans
            r_span = row_spans.get((i, j), 1)
            c_span = col_spans.get((i, j), 1)
            
            # If original had different spans and content matches, prefer original
            if (i, j) in orig_spans:
                orig_r, orig_c = orig_spans[(i, j)]
                # Only use original spans if they're larger (more specific)
                if orig_r > r_span:
                    r_span = orig_r
                if orig_c > c_span:
                    c_span = orig_c
            
            # Determine header flags
            col_header = False
            row_header = False
            
            if (i, j) in orig_headers:
                # Use original flags
                col_header, row_header = orig_headers[(i, j)]
            else:
                # Default logic: row 0 = column headers
                if i == 0:
                    col_header = True
                # Check if column 0 should be row headers (from original pattern)
                if j == 0 and i > 0:
                    # Check if original had row headers
                    for (oi, oj), (och, orh) in orig_headers.items():
                        if oj == 0 and oi > 0 and orh:
                            row_header = True
                            break
            
            cell = TableCell(
                text=text_val,
                row_span=r_span,
                col_span=c_span,
                column_header=col_header,
                row_header=row_header,
                start_row_offset_idx=i,
                end_row_offset_idx=i + r_span,
                start_col_offset_idx=j,
                end_col_offset_idx=j + c_span,
            )
            grid_row.append(cell)
            table_cells.append(cell)
        
        grid.append(grid_row)
    
    return TableData(
        num_rows=num_rows,
        num_cols=num_cols,
        table_cells=table_cells,
        grid=grid
    )


def get_ref_pointer(ref_obj: Any) -> str:
    """
    Safely extracts the string pointer (e.g., '#/tables/1') from a Ref object.
    """
    if hasattr(ref_obj, "ref"):
        return ref_obj.ref
    
    if hasattr(ref_obj, "model_dump"):
        data = ref_obj.model_dump(by_alias=True)
        return data.get("$ref", "")
    
    if isinstance(ref_obj, dict):
        return ref_obj.get("$ref", "")
        
    return ""


def inject_merged_tables(
    doc: DoclingDocument, 
    logical_tables: List[LogicalTable]
) -> DoclingDocument:
    """
    Modifies the DoclingDocument IN-PLACE with merged table data.
    
    Preserves original cell structure (spans, headers) where possible.
    """
    log.info("Starting DoclingDocument injection...")
    
    refs_to_remove: Set[str] = set()
    
    for lt in logical_tables:
        if not lt.members:
            continue
            
        anchor_idx = lt.members[0]
        anchor_table = doc.tables[anchor_idx]
        
        log.info(f"Injecting Logical Table {lt.logical_index} into Anchor Table {anchor_idx}")
        
        # 1. UPDATE CONTENT with span/header preservation
        original_data = getattr(anchor_table, 'data', None)
        anchor_table.data = dataframe_to_docling_data(
            lt.df,
            original_anchor_data=original_data,
            preserve_spans=True
        )
        
        # 2. MERGE PROVENANCE from satellites
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

    # 3. PRUNE satellite references from body hierarchy
    removed_count = 0

    def traverse_and_prune(group_node: Any):
        nonlocal removed_count
        if not hasattr(group_node, 'children'):
            return

        new_children = []
        for child_ref in group_node.children:
            ptr = get_ref_pointer(child_ref)
            
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