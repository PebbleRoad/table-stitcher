import logging
from typing import List, Set, Any
import pandas as pd
from docling_core.types.doc import (
    DoclingDocument,
    TableData,
    TableCell,
)

# Only import the models, NEVER import .injector here
from .models import LogicalTable

log = logging.getLogger(__name__)

def dataframe_to_docling_data(df: pd.DataFrame) -> TableData:
    """
    Converts a Pandas DataFrame back into the Docling TableData structure.
    Reconstructs the grid and cell objects strictly adhering to the schema.
    """
    grid: List[List[TableCell]] = []
    
    # 1. Handle Header Row (Index 0)
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
    grid.append(header_row_cells)

    # 2. Handle Data Rows (Indices 1 to N)
    for i, (_, row) in enumerate(df.iterrows()):
        grid_row = []
        table_row_idx = i + 1
        
        for j, val in enumerate(row):
            if pd.isna(val) or val is None:
                text_val = ""
            else:
                text_val = str(val)

            cell = TableCell(
                text=text_val,
                row_span=1,
                col_span=1,
                column_header=False,
                row_header=False,
                start_row_offset_idx=table_row_idx,
                end_row_offset_idx=table_row_idx + 1,
                start_col_offset_idx=j,
                end_col_offset_idx=j + 1,
            )
            grid_row.append(cell)
        grid.append(grid_row)

    # 3. Create Flattened Cell List
    table_cells = [cell for row in grid for cell in row]

    return TableData(
        num_rows=len(grid),
        num_cols=len(df.columns),
        table_cells=table_cells,
        grid=grid
    )

def get_ref_pointer(ref_obj: Any) -> str:
    """
    Safely extracts the string pointer (e.g., '#/tables/1') from a Ref object.
    Handles differences in Docling versions (Ref vs RefItem, .ref vs $ref).
    """
    # 1. Try standard attribute access
    if hasattr(ref_obj, "ref"):
        return ref_obj.ref
    
    # 2. Try Pydantic model dump (most robust)
    if hasattr(ref_obj, "model_dump"):
        data = ref_obj.model_dump(by_alias=True)
        return data.get("$ref", "")
    
    # 3. Try dict access if it's already a dict
    if isinstance(ref_obj, dict):
        return ref_obj.get("$ref", "")
        
    return ""

def inject_merged_tables(doc: DoclingDocument, logical_tables: List[LogicalTable]) -> DoclingDocument:
    """
    Modifies the DoclingDocument IN-PLACE.
    """
    log.info("Starting DoclingDocument injection...")
    
    refs_to_remove: Set[str] = set()
    
    # Iterate through our merged logical tables
    for lt in logical_tables:
        if not lt.members:
            continue
            
        anchor_idx = lt.members[0]
        anchor_table = doc.tables[anchor_idx]
        
        log.info(f"Injecting Logical Table {lt.logical_index} into Anchor Table {anchor_idx}")
        
        # 1. UPDATE CONTENT
        anchor_table.data = dataframe_to_docling_data(lt.df)
        
        # 2. UPDATE PROVENANCE & COLLECT SATELLITES
        for satellite_idx in lt.members[1:]:
            satellite_table = doc.tables[satellite_idx]
            
            # Merge provenance
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

            # Mark for removal
            refs_to_remove.add(satellite_table.self_ref)

    # 3. CLEAN UP BODY HIERARCHY
    removed_count = 0

    def traverse_and_prune(group_node: Any):
        nonlocal removed_count
        if not hasattr(group_node, 'children'):
            return

        new_children = []
        for child_ref in group_node.children:
            # ROBUST POINTER EXTRACTION HERE 
            ptr = get_ref_pointer(child_ref)
            
            if not ptr: 
                # Keep it if we can't read it, to be safe
                new_children.append(child_ref)
                continue

            # If this is a satellite table, skip it (prune it)
            if ptr in refs_to_remove:
                removed_count += 1
                continue
                
            new_children.append(child_ref)
            
            # If this child is a Group, recurse
            if ptr.startswith("#/groups/"):
                try:
                    group_idx = int(ptr.split("/")[-1])
                    if group_idx < len(doc.groups):
                        traverse_and_prune(doc.groups[group_idx])
                except (ValueError, IndexError):
                    pass
                    
        group_node.children = new_children

    # Start traversal
    if doc.body:
        traverse_and_prune(doc.body)
    
    log.info(f"Injection complete. Pruned {removed_count} satellite table references.")
    return doc