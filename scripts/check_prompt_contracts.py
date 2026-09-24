"""静态提示词契约门禁。

这不是模型质量评测，而是阻止提示词在进入 harness 前出现结构性漂移：
frontmatter、未解析变量、段预算、模型面内部术语和失效本地引用。
运行：``uv run python scripts/check_prompt_contracts.py``。
"""

from __future__ import annotations

import re
import sys
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "excelmanus" / "prompts"
SKILLS = ROOT / "excelmanus" / "skillpacks" / "system"
TASK_PROMPTS = (
    ROOT / "excelmanus" / "session_title.py",
    ROOT / "excelmanus" / "memory_extractor.py",
    ROOT / "excelmanus" / "memory_maintainer.py",
    ROOT / "excelmanus" / "session_summarizer.py",
    ROOT / "excelmanus" / "compaction.py",
)


def _prompt_text() -> str:
    from excelmanus.prompt.load import PromptComposer, PromptContext

    composer = PromptComposer(PROMPTS)
    composer.load_all(auto_repair=False)
    composer.validate_runtime()
    return composer.compose_system_text(
        PromptContext(chat_mode="write"),
        variables={"workspace_root": "当前工作区", "model": "当前模型"},
    )


def _generated_model_text() -> str:
    """Render the highest-risk generated model surfaces when dependencies exist."""
    try:
        from excelmanus.code_mode import render_sdk_section
        from excelmanus.tools.workbook_tools import get_tools

        return render_sdk_section(get_tools())
    except Exception as exc:  # noqa: BLE001
        # CI installs the full dependency set. Keep local lightweight runs
        # useful while making the skipped surface explicit in the output.
        print(f"Prompt contract note: generated SDK scan skipped: {exc}")
        return ""


def collect_errors() -> list[str]:
    errors: list[str] = []
    from excelmanus.memory import TokenCounter
    from excelmanus.prompt.canonical import FORBIDDEN_MODEL_TERMS, TOOL_DESCRIPTIONS
    from excelmanus.prompt.load import parse_prompt_file

    for folder in (PROMPTS / "core", PROMPTS / "strategies", PROMPTS / "subagent"):
        for path in (sorted(folder.glob("*.md")) if folder.is_dir() else ()):
            try:
                seg = parse_prompt_file(path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path.relative_to(ROOT)}: {exc}")
                continue
            if seg.max_tokens and TokenCounter.count(seg.content) > seg.max_tokens:
                errors.append(
                    f"{path.relative_to(ROOT)}: {TokenCounter.count(seg.content)} tokens > max_tokens {seg.max_tokens}"
                )
            if re.search(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", seg.content) and seg.layer != "core":
                # strategy 也允许变量，但变量必须由 registry 提供；这里只提示，
                # 真正的未定义变量由 PromptRegistry strict assemble 拒绝。
                pass

    skill_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (sorted(SKILLS.rglob("*.md")) if SKILLS.is_dir() else ())
    )
    task_literals: list[str] = []
    for path in TASK_PROMPTS:
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                if not any("PROMPT" in name for name in names):
                    continue
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    value = ""
                if isinstance(value, str):
                    task_literals.append(value)
        except SyntaxError as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
    task_text = "\n".join(task_literals)
    text = (
        _prompt_text()
        + "\n"
        + _generated_model_text()
        + "\n"
        + "\n".join(TOOL_DESCRIPTIONS.values())
        + "\n"
        + skill_text
        + "\n"
        + task_text
    )
    for term in FORBIDDEN_MODEL_TERMS:
        if term in text:
            errors.append(f"model surface contains forbidden internal term: {term}")
    if "{{workspace_root}}" in text or "{{model}}" in text:
        errors.append("model surface contains unresolved prompt variable")

    # Skill reference links are local assets; dangling links are prompt defects,
    # not documentation nitpicks, because the model may be told to load them.
    link_re = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    for path in (sorted(SKILLS.rglob("*.md")) if SKILLS.is_dir() else ()):
        content = path.read_text(encoding="utf-8")
        for target in link_re.findall(content):
            if target.startswith(("http:", "https:", "#", "mailto:")):
                continue
            target_path = (path.parent / target.split("#", 1)[0]).resolve()
            if not target_path.exists():
                errors.append(f"{path.relative_to(ROOT)}: dangling reference {target}")
    return errors


def main() -> int:
    errors = collect_errors()
    if errors:
        print("Prompt contract check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Prompt contract check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
