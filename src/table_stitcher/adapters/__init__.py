from .base import TableStitcherAdapter

try:
    from .docling import DoclingAdapter
except ModuleNotFoundError as e:
    if "docling_core" in str(e):
        DoclingAdapter = None  # docling-core not installed; use core-only mode
    else:
        raise  # genuine missing dependency inside docling.py — don't swallow

__all__ = ["TableStitcherAdapter", "DoclingAdapter"]
