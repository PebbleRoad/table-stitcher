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


def dataframe_to_docling_data(
    df: pd.DataFrame,
    original_data: Optional[TableData] = None,
) -> TableData:
    """
    Converts a Pandas DataFrame back into the Docling TableData structure.
    
    IMPORTANT: We do NOT try to detect spans from content.
    Identical values do NOT imply merged cells.
    
    For merged tables, we create simple 1x1 cells (no spans).
    The original spans are lost during DataFrame merge, which is acceptable.
    
    Args:
        df: The DataFrame to convert
        original_data: Original TableData (used for header flag hints only)
    
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
    
    num_data_rows = len(df)
    num_cols = len(df.columns)
    num_total_rows = num_data_rows + 1  # +1 for header row
    
    # Check if original had row headers in first column
    has_row_headers = False
    if original_data and original_data.grid:
        for row in original_data.grid[1:]:  # Skip header row
            if row and len(row) > 0 and row[0]:
                if getattr(row[0], 'row_header', False):
                    has_row_headers = True
                    break
    
    # Build the grid
    grid: List[List[TableCell]] = []
    table_cells: List[TableCell] = []
    
    # Row 0: Header row
    header_row_cells = []
    for j, col_name in enumerate(df.columns):
        cell = TableCell(
            text=str(col_name) if col_name is not None else "",
            row_span=1,
            col_span=1,
            column_header=True,  # Header row cells are column headers
            row_header=False,
            start_row_offset_idx=0,
            end_row_offset_idx=1,
            start_col_offset_idx=j,
            end_col_offset_idx=j + 1,
        )
        header_row_cells.append(cell)
        table_cells.append(cell)
    
    grid.append(header_row_cells)
    
    # Data rows
    for i, (_, row) in enumerate(df.iterrows()):
        grid_row: List[TableCell] = []
        table_row_idx = i + 1  # +1 because row 0 is header
        
        for j, val in enumerate(row):
            # Get text value
            if pd.isna(val) or val is None:
                text_val = ""
            else:
                text_val = str(val)
            
            # First column might be row header (based on original structure)
            row_header = (j == 0 and has_row_headers)
            
            cell = TableCell(
                text=text_val,
                row_span=1,  # Simple 1x1 cells - no span detection!
                col_span=1,
                column_header=False,  # Data cells are not column headers
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
    
    IMPORTANT: Only modifies tables that were actually merged.
    Single-page tables retain their original Docling structure.
    """
    log.info("Starting DoclingDocument injection...")
    
    refs_to_remove: Set[str] = set()
    
    for lt in logical_tables:
        if not lt.members:
            continue
        
        # ONLY modify tables that were actually merged (multiple fragments)
        if len(lt.members) == 1:
            # Single table - don't touch it, preserve original Docling structure
            log.debug(f"Skipping single-table {lt.members[0]} - preserving original structure")
            continue
            
        anchor_idx = lt.members[0]
        anchor_table = doc.tables[anchor_idx]
        
        log.info(f"Injecting Logical Table {lt.logical_index} into Anchor Table {anchor_idx} "
                 f"(merged from {len(lt.members)} fragments)")
        
        # Get original data for header hints
        original_data = getattr(anchor_table, 'data', None)
        
        # Update content - simple cells, no false span detection
        anchor_table.data = dataframe_to_docling_data(
            lt.df,
            original_data=original_data,
        )
        
        # Merge provenance from satellites
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