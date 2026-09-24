"""Fixtures that create documents through the production V2 transaction path."""
from pathlib import Path
from tempfile import TemporaryDirectory

from excelmanus.tools.context import use_workspace
from excelmanus.tools.workbook_tools import apply_spreadsheet_changes


def create_document_bytes(document):
    document = document.model_dump(exclude_none=True) if hasattr(document, "model_dump") else document
    with TemporaryDirectory() as directory, use_workspace(directory):
        result = apply_spreadsheet_changes(file_path="book.xlsx", workbook_spec=document)
        if not result.success:
            raise ValueError(result.model_text)
        return (Path(directory) / "book.xlsx").read_bytes(), result.value


def apply_operations_in_memory(wb, operations):
    from excelmanus.workbook.mutation import execute_operation
    from excelmanus.workbook.contracts import validate_operations
    for operation in validate_operations(operations):
        execute_operation(wb, operation)


def region_matrix(region):
    """Materialize a bounded sparse observation without dropping trailing blanks."""
    box = region["rect"]
    return [[region["cells"].get(f"{r},{c}", {}).get("v") for c in range(box["c0"], box["c1"]+1)]
            for r in range(box["r0"], box["r1"]+1)]


def model_projection(result, root):
    from excelmanus.engine_core.spill import expose_spreadsheet_value, SpillStore
    return expose_spreadsheet_value(result, store=SpillStore(root)).model_text


def projected_payload(result, root):
    import json
    from excelmanus.engine_core.spill import retrieve_spill_result
    payload = json.loads(model_projection(result, root))
    if "result_spill" in payload:
        assert payload["schema_version"] == result.value["schema_version"]
        assert payload["coverage"] == result.value["coverage"]
        retrieved = retrieve_spill_result(payload["result_spill"], workspace_root=root)
        assert retrieved.success
        return retrieved.value
    return payload
