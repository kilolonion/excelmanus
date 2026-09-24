"""The workbook domain entry point used by model tools and HTTP clients."""

from __future__ import annotations

from excelmanus.workbook.snapshot import open_snapshot


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
        domain_arguments = dict(arguments)
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
