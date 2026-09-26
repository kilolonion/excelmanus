"""The workbook domain entry point used by model tools and HTTP clients."""

from __future__ import annotations

from excelmanus.workbook.snapshot import open_snapshot


def _fold_query_aliases(arguments: dict, alias_map: dict[str, str]) -> dict:
    """Fold a query's declared compatibility aliases to canonical keys.

    Schema validation runs before this helper, so aliases are explicit on the
    wire contract rather than silently accepted by an implementation detail.
    If both spellings are present they must carry the same value; otherwise a
    caller gets an actionable INVALID_ARGS error instead of whichever value
    happened to win dictionary order.
    """
    folded = dict(arguments)
    for alias, canonical in alias_map.items():
        if alias not in folded:
            continue
        alias_value = folded.get(alias)
        canonical_value = folded.get(canonical)
        if canonical in folded and canonical_value not in (None, "") and alias_value not in (None, "") and canonical_value != alias_value:
            raise ValueError(f"别名冲突：{alias} 与 {canonical} 值不同")
        if canonical not in folded or folded.get(canonical) in (None, ""):
            folded[canonical] = alias_value
        folded.pop(alias, None)
    return folded


class WorkbookService:
    def query(self, name: str, arguments: dict):
        import jsonschema
        from excelmanus.tools.workbook_query_schemas import QUERY_SCHEMAS
        from excelmanus.workbook import domain

        if name not in QUERY_SCHEMAS:
            raise ValueError("Unknown workbook query")
        if name == "analyze_spreadsheet" and arguments.get("mode") in {
            "overview",
            "range",
            "search",
            "objects",
            "dependencies",
        }:
            raise ValueError(
                "Use observe_spreadsheet for overview/range/search/objects/dependencies"
            )
        jsonschema.Draft202012Validator(QUERY_SCHEMAS[name]).validate(arguments)
        alias_maps = {
            "analyze_spreadsheet": {
                "path": "file_path", "sheet_name": "sheet", "content_version": "expected_version",
                "groupBy": "group_by", "aggs": "aggregations", "maxRows": "max_rows",
                "sampleRows": "sample_rows", "sortBy": "sort_by", "headerRow": "header_row",
                "filePaths": "file_paths", "dupOnly": "dup_only", "lookup": "join",
            },
            "compare_spreadsheets": {
                "path": "file_a", "other_path": "file_b", "sheet": "sheet_a", "other_sheet": "sheet_b",
                "keyColumns": "key_columns", "maxDifferences": "max_diffs",
            },
            "manage_spreadsheet_versions": {"path": "file_path", "content_version": "expected_version"},
            "split_spreadsheet": {
                "path": "file_path", "column": "by_column", "sheet_name": "sheet", "content_version": "expected_version",
                "maxFiles": "max_files", "outputDir": "output_dir", "filenameTemplate": "filename_template",
                "headerRow": "header_row",
            },
            "trace_spreadsheet_formulas": {"path": "file_path", "range": "target"},
        }
        domain_arguments = _fold_query_aliases(arguments, alias_maps.get(name, {}))
        if name == "analyze_spreadsheet" and isinstance(domain_arguments.get("join"), dict):
            domain_arguments["join"] = _fold_query_aliases(
                domain_arguments["join"],
                {"path": "file_path", "sheet_name": "sheet", "leftOn": "left_on", "rightOn": "right_on"},
            )
        if (
            name in {"analyze_spreadsheet", "split_spreadsheet"}
            and "sheet" in domain_arguments
        ):
            domain_arguments["sheet_name"] = domain_arguments.pop("sheet")
        result = getattr(domain, name)(**domain_arguments)
        if result.success and isinstance(result.value, dict):
            result.value["schema_version"] = "workbook/2"
            result.value["provenance"] = {
                "query": name,
                "calculation": "saved_values; no implicit recalculation",
                "coverage": result.coverage or result.value.get("coverage"),
            }
            versions = dict(result.value.get("source_versions") or {})
            if result.value.get("content_version") and arguments.get("file_path"):
                versions[arguments["file_path"]] = result.value["content_version"]
            for side in ("a", "b"):
                if result.value.get(f"content_version_{side}"):
                    versions[
                        result.value.get(
                            f"file_{side}", arguments.get(f"file_{side}", "")
                        )
                    ] = result.value[f"content_version_{side}"]
            result.value["source_snapshots"] = [
                {"file_path": p, "content_version": v} for p, v in versions.items()
            ]
        return result

    def observe(
        self, file_path: str, *, expected_version: str | None = None, **request
    ):
        if not str(file_path).strip():
            from excelmanus.workbook_commit import CommitError

            raise CommitError(
                "PATH_REQUIRED",
                "file_path is required; use list_directory to discover files",
            )
        from excelmanus.tools.context import require_guard
        from excelmanus.tools._helpers import check_file_exists, MutationAborted

        guard = require_guard()
        missing = check_file_exists(
            guard.resolve_and_validate(file_path), file_path, guard
        )
        if missing is not None:
            raise MutationAborted(missing)
        snapshot = open_snapshot(file_path, expected_version=expected_version)
        return self.observe_snapshot(snapshot, **request)

    def observe_snapshot(self, snapshot, **request):
        """HTTP and history already own a pinned snapshot; never reopen a path."""
        if request.get("facets") is None:
            request.pop("facets", None)
        from excelmanus.workbook.protocol import ObservationRequest

        canonical = ObservationRequest(**request).canonical()
        from excelmanus.workbook.observation import observe_snapshot

        return observe_snapshot(snapshot, **canonical)

    def apply(self, file_path: str = "", **changes):
        from excelmanus.workbook.mutation import apply_changes

        return apply_changes(file_path, **changes)

    def preview(self, file_path: str, **request):
        from excelmanus.workbook.preview import preview_workbook

        return preview_workbook(file_path, **request)
