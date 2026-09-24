"""提示词测试共用：从 md 加载正文，不引用已删除的 canonical 副本。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.prompt.load import PromptComposer, PromptContext, parse_prompt_file
from excelmanus.prompt.registry import interpolate

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "prompt_snapshots"
VARS = {"workspace_root": "/tmp/excelmanus-ws", "model": "test-model"}

WRITE_SECTION_NAMES = (
    "harness:identity",
    "deployment:persona",
    "spreadsheet:invariants",
    "tool:observe",
    "tool:analyze",
    "tool:changes",
    "tool:preview",
    "tool:split",
    "spreadsheet:document",
    "tool:run_code",
)
PLAN_SECTION_NAMES = (
    "harness:identity",
    "deployment:persona",
    "plan:policy",
    "spreadsheet:invariants",
    "tool:observe",
    "tool:analyze",
    "tool:preview",
)
READ_SECTION_NAMES = (
    "harness:identity",
    "deployment:persona",
    "spreadsheet:invariants",
    "tool:observe",
    "tool:analyze",
    "tool:preview",
)


def composer() -> PromptComposer:
    loaded = PromptComposer(PROMPTS_DIR)
    loaded.load_all()
    return loaded


def section_body(relative: str) -> str:
    return parse_prompt_file(PROMPTS_DIR / relative).content


def filled(text: str) -> str:
    return interpolate(text, VARS, strict=True)


def system_text(mode: str = "write") -> str:
    return composer().compose_system_text(
        PromptContext(chat_mode=mode),
        variables=VARS,
    )


def snapshot_path(name: str) -> Path:
    return SNAPSHOT_DIR / name


def read_snapshot(name: str) -> str:
    path = snapshot_path(name)
    assert path.is_file(), f"缺少快照 {path}"
    return path.read_text(encoding="utf-8")


def dump_snapshots() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    mapping = {
        "write.txt": system_text("write"),
        "plan.txt": system_text("plan"),
        "read.txt": system_text("read"),
    }
    for name, text in mapping.items():
        body = text if text.endswith("\n") else text + "\n"
        snapshot_path(name).write_text(body, encoding="utf-8")
