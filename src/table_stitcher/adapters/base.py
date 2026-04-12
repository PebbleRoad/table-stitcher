"""
Adapter protocol for table-stitcher.

Any PDF parser can integrate with table-stitcher by implementing these
two methods. The merge engine only ever sees TableMeta objects — it never
touches parser-native document structures.
"""

from typing import Any, List, runtime_checkable, Protocol

from ..models import MultiPageConfig, TableMeta, LogicalTable


@runtime_checkable
class TableStitcherAdapter(Protocol):
    """
    Minimal interface a parser must implement to plug into table-stitcher.

    Implement ``extract`` to read table fragments from your document format,
    and ``inject`` to write merged results back.
    """

    def extract(self, doc: Any, cfg: MultiPageConfig) -> List[TableMeta]:
        """
        Read all table fragments from the parser-native document object.

        Returns a list of TableMeta, one per table fragment found in the doc.
        The merger engine only ever sees these — it never touches ``doc``.
        """
        ...

    def inject(self, doc: Any, logical_tables: List[LogicalTable]) -> Any:
        """
        Write merged results back into the parser-native document object.

        Receives the original doc and the full list of LogicalTable objects
        (including single-fragment tables that were not merged — the adapter
        decides whether to skip or handle them).

        Returns the (potentially modified) doc.
        """
        ...
