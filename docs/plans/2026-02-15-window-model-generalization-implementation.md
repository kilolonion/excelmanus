# Window Model Generalization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the monolithic `WindowState` model with `BaseWindow + ExplorerWindow/SheetWindow + typed containers`, and enforce identity-driven updates plus projection-only rendering.

**Architecture:** Build a discriminated-union window domain model with explicit `Identity`, `Delta`, and `Projection` layers. Route all state mutation through a single `apply_delta` entrypoint, and keep notice/tool-payload/confirmation as read-only projections. Remove generic metadata escape hatches from core state.

**Tech Stack:** Python 3.12, dataclasses, typing/Protocol, pytest, existing `excelmanus.window_perception` package.

---

## Completion Status (2026-02-15)

- `WindowState` class has been removed from `excelmanus/window_perception/models.py`; core model is now `Window = ExplorerWindow | SheetWindow`.
- `domain.py` now owns `BaseWindow`, `ExplorerWindow`, `SheetWindow`, typed containers, and cross-cutting states (`LifecycleState`, `IntentState`, `AuditState`, `FocusState`).
- `WindowPerceptionManager` now mutates real `Window` objects through `apply_delta(window, delta)` without temporary `_to_domain_window` conversion.
- `projection_service.py`, `renderer.py`, and `confirmation.py` now consume `Window`/projection DTOs rather than `WindowState`.
- Core metadata escape-hatch keys were migrated to typed fields (`entries`, `scroll_position`, `status_bar`, `column_widths`, `row_heights`, `merged_ranges`, `conditional_effects`).
- `engine.py` and related tests were migrated to the new `Window` model.

## Final Validation Commands

1. `uv run --extra dev pytest tests/test_window_*.py -v`
2. `uv run --extra dev pytest tests/test_engine.py -k "window_perception" -v`
3. `uv run --extra dev pytest tests/test_window_*.py tests/test_engine.py -k "window_perception" -v`
4. `rg "WindowState" excelmanus tests`
5. `rg "metadata\\[\"(entries|scroll_position|status_bar|column_widths|row_heights|merged_ranges|conditional_effects)\"\\]" excelmanus/window_perception`

---

### Task 1: Introduce Window Domain Types (Base + Subclasses + Typed Data)

**Files:**
- Create: `excelmanus/window_perception/domain.py`
- Modify: `excelmanus/window_perception/__init__.py`
- Test: `tests/test_window_domain.py`

**Step 1: Write the failing test**

```python
from excelmanus.window_perception.domain import BaseWindow, ExplorerWindow, SheetWindow

def test_explorer_and_sheet_have_separate_typed_data() -> None:
    explorer = ExplorerWindow.new(id="explorer_1", title="资源管理器", directory=".")
    sheet = SheetWindow.new(id="sheet_1", title="t", file_path="a.xlsx", sheet_name="Sheet1")
    assert explorer.kind == "explorer"
    assert sheet.kind == "sheet"
    assert hasattr(explorer.data, "directory")
    assert hasattr(sheet.data, "file_path")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_window_domain.py::test_explorer_and_sheet_have_separate_typed_data -v`  
Expected: FAIL with import/attribute errors.

**Step 3: Write minimal implementation**

```python
# domain.py
@dataclass
class BaseWindow: ...

@dataclass
class ExplorerData: ...

@dataclass
class SheetData: ...

@dataclass
class ExplorerWindow(BaseWindow): ...

@dataclass
class SheetWindow(BaseWindow): ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_window_domain.py::test_explorer_and_sheet_have_separate_typed_data -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/window_perception/domain.py excelmanus/window_perception/__init__.py tests/test_window_domain.py
git commit -m "feat(window): add base and typed window domain models"
```

### Task 2: Add Identity Model and Locate Semantics

**Files:**
- Create: `excelmanus/window_perception/identity.py`
- Create: `excelmanus/window_perception/locator.py`
- Test: `tests/test_window_identity.py`

**Step 1: Write the failing test**

```python
from excelmanus.window_perception.identity import ExplorerIdentity, SheetIdentity
from excelmanus.window_perception.locator import WindowLocator

def test_locator_matches_by_sheet_identity_only() -> None:
    locator = WindowLocator()
    sid = SheetIdentity(file_path_norm="/tmp/a.xlsx", sheet_name_norm="sheet1")
    locator.register("sheet_1", sid)
    assert locator.find(sid) == "sheet_1"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_window_identity.py::test_locator_matches_by_sheet_identity_only -v`  
Expected: FAIL with missing classes.

**Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class ExplorerIdentity: ...

@dataclass(frozen=True)
class SheetIdentity: ...

class WindowLocator:
    def register(...): ...
    def find(...): ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_window_identity.py::test_locator_matches_by_sheet_identity_only -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/window_perception/identity.py excelmanus/window_perception/locator.py tests/test_window_identity.py
git commit -m "feat(window): add identity and locator semantics"
```

### Task 3: Add Delta Contract and Single Mutation Entry (`apply_delta`)

**Files:**
- Create: `excelmanus/window_perception/delta.py`
- Create: `excelmanus/window_perception/apply.py`
- Test: `tests/test_window_apply_delta.py`

**Step 1: Write the failing test**

```python
from excelmanus.window_perception.apply import apply_delta
from excelmanus.window_perception.delta import ExplorerDelta, SheetReadDelta


def test_apply_delta_rejects_kind_mismatch() -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_window_apply_delta.py::test_apply_delta_rejects_kind_mismatch -v`  
Expected: FAIL with missing contract/behavior.

**Step 3: Write minimal implementation**

```python
class DeltaReject(Exception): ...

def apply_delta(window, delta):
    # kind guard + mutation + audit append
    ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_window_apply_delta.py::test_apply_delta_rejects_kind_mismatch -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/window_perception/delta.py excelmanus/window_perception/apply.py tests/test_window_apply_delta.py
git commit -m "feat(window): enforce delta contract with single mutation entry"
```

### Task 4: Add Projection DTO Layer (Notice / Tool Payload / Confirmation)

**Files:**
- Create: `excelmanus/window_perception/projection_models.py`
- Create: `excelmanus/window_perception/projection_service.py`
- Test: `tests/test_window_projection_models.py`

**Step 1: Write the failing test**

```python
from excelmanus.window_perception.projection_service import project_notice

def test_notice_projection_is_read_only_and_contains_identity() -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_window_projection_models.py::test_notice_projection_is_read_only_and_contains_identity -v`  
Expected: FAIL

**Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class NoticeProjection: ...

def project_notice(window, ctx) -> NoticeProjection:
    ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_window_projection_models.py::test_notice_projection_is_read_only_and_contains_identity -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/window_perception/projection_models.py excelmanus/window_perception/projection_service.py tests/test_window_projection_models.py
git commit -m "feat(window): add projection DTO layer"
```

### Task 5: Refactor Renderer and Confirmation to Consume Projection DTOs

**Files:**
- Modify: `excelmanus/window_perception/renderer.py`
- Modify: `excelmanus/window_perception/confirmation.py`
- Test: `tests/test_window_perception_renderer.py`
- Test: `tests/test_window_confirmation.py`

**Step 1: Write the failing tests**

```python
def test_renderer_uses_notice_projection_not_window_fields_directly() -> None:
    ...

def test_confirmation_uses_confirmation_projection_shape_priority() -> None:
    ...
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_window_perception_renderer.py tests/test_window_confirmation.py -v`  
Expected: FAIL on new assertions.

**Step 3: Write minimal implementation**

```python
# renderer.py
# accept NoticeProjection/ToolPayloadProjection and render text only

# confirmation.py
# accept ConfirmationProjection and serialize only
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_window_perception_renderer.py tests/test_window_confirmation.py -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/window_perception/renderer.py excelmanus/window_perception/confirmation.py tests/test_window_perception_renderer.py tests/test_window_confirmation.py
git commit -m "refactor(window): render and confirm from projection DTOs"
```

### Task 6: Refactor Manager to Identity+Delta Pipeline

**Files:**
- Modify: `excelmanus/window_perception/manager.py`
- Modify: `excelmanus/window_perception/ingest.py`
- Test: `tests/test_window_perception_focus.py`
- Test: `tests/test_window_focus_semantics.py`
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

```python
def test_manager_routes_tool_result_via_classify_locate_apply() -> None:
    ...

def test_focus_hit_promotes_window_to_active() -> None:
    ...
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_window_perception_focus.py tests/test_window_focus_semantics.py -v`  
Expected: FAIL (old field mutation path).

**Step 3: Write minimal implementation**

```python
# manager pipeline
# classify -> locate(identity) -> apply_delta -> project -> render
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_window_perception_focus.py tests/test_window_focus_semantics.py tests/test_engine.py -k "window_perception" -v`  
Expected: PASS on updated cases.

**Step 5: Commit**

```bash
git add excelmanus/window_perception/manager.py excelmanus/window_perception/ingest.py tests/test_window_perception_focus.py tests/test_window_focus_semantics.py tests/test_engine.py
git commit -m "refactor(window): switch manager to identity-delta pipeline"
```

### Task 7: Remove Legacy `WindowState` Surface and Metadata Escape Hatch

**Files:**
- Modify: `excelmanus/window_perception/models.py`
- Modify: `excelmanus/window_perception/__init__.py`
- Modify: all imports in `excelmanus/window_perception/*.py`
- Test: `tests/test_window_perception_models.py`

**Step 1: Write the failing test**

```python
def test_legacy_windowstate_is_not_exported() -> None:
    import excelmanus.window_perception as wp
    assert not hasattr(wp, "WindowState")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_window_perception_models.py::test_legacy_windowstate_is_not_exported -v`  
Expected: FAIL (legacy symbol still present).

**Step 3: Write minimal implementation**

```python
# remove WindowState export, replace usages with Window union
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_window_perception_models.py -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/window_perception/models.py excelmanus/window_perception/__init__.py tests/test_window_perception_models.py
git commit -m "refactor(window): remove legacy WindowState surface"
```

### Task 8: Full Regression, Docs Sync, and Final Validation

**Files:**
- Modify: `docs/plans/2026-02-15-window-strategy-design.md`
- Modify: `docs/plans/2026-02-15-window-model-generalization-implementation.md`
- Test: `tests/test_window_*.py`
- Test: `tests/test_engine.py`

**Step 1: Add/adjust failing regression tests first**

```python
def test_projection_identity_intent_consistency_across_outputs() -> None:
    ...
```

**Step 2: Run targeted suite**

Run: `pytest tests/test_window_*.py -v`  
Expected: FAIL first, then PASS after fixes.

**Step 3: Run engine integration subset**

Run: `pytest tests/test_engine.py -k "window_perception" -v`  
Expected: PASS

**Step 4: Run final verification commands**

Run: `pytest tests/test_window_*.py tests/test_engine.py -k "window_perception" -v`  
Expected: PASS

**Step 5: Commit**

```bash
git add docs/plans/2026-02-15-window-strategy-design.md docs/plans/2026-02-15-window-model-generalization-implementation.md tests
git commit -m "test/docs(window): finalize generalized window model rollout"
```

---

## Execution Notes

- Keep each task isolated and small. Do not batch multiple architecture moves in one commit.
- Maintain strict TDD order per step: fail -> implement -> pass -> commit.
- If a task uncovers hidden coupling, create a micro-task before continuing; do not bypass failing tests.
- Preserve invariant checks in tests (kind mismatch, identity drift, projection read-only behavior).
- `tests/test_engine.py::TestEngineWindowPerception` selector was stale in this repository; use `-k "window_perception"` equivalent scope.
