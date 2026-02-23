"""用户自定义规则管理器：支持全局规则（YAML 文件）和会话级规则（数据库）。

全局规则注入到每个会话的 system prompt 中，会话级规则仅对当前会话生效。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from excelmanus.stores.rules_store import RulesStore

logger = logging.getLogger(__name__)

_DEFAULT_RULES_DIR = "~/.excelmanus"
_RULES_FILENAME = "rules.yaml"


@dataclass
class Rule:
    """单条规则。"""

    id: str
    content: str
    enabled: bool = True
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"r_{int(time.time() * 1000)}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")


class RulesManager:
    """管理全局规则（YAML 文件持久化）和会话级规则（数据库持久化）。"""

    def __init__(
        self,
        rules_dir: str | None = None,
        *,
        db_store: "RulesStore | None" = None,
    ) -> None:
        self._rules_dir = Path(rules_dir or _DEFAULT_RULES_DIR).expanduser()
        self._rules_dir.mkdir(parents=True, exist_ok=True)
        self._rules_file = self._rules_dir / _RULES_FILENAME
        self._db_store = db_store
        self._global_rules: list[Rule] = []
        self._load_global_rules()

    # ── 全局规则 CRUD ──────────────────────────────────────

    def _load_global_rules(self) -> None:
        if not self._rules_file.exists():
            self._global_rules = []
            return
        try:
            raw = self._rules_file.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
            items = data.get("rules", [])
            self._global_rules = [
                Rule(
                    id=item.get("id", ""),
                    content=item.get("content", ""),
                    enabled=item.get("enabled", True),
                    created_at=item.get("created_at", ""),
                )
                for item in items
                if item.get("content", "").strip()
            ]
        except Exception:
            logger.warning("加载全局规则失败: %s", self._rules_file, exc_info=True)
            self._global_rules = []

    def _save_global_rules(self) -> None:
        data = {"rules": [asdict(r) for r in self._global_rules]}
        try:
            self._rules_file.write_text(
                yaml.dump(data, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("保存全局规则失败: %s", self._rules_file, exc_info=True)

    def list_global_rules(self) -> list[Rule]:
        return list(self._global_rules)

    def get_enabled_global_rules(self) -> list[Rule]:
        return [r for r in self._global_rules if r.enabled]

    def add_global_rule(self, content: str) -> Rule:
        rule = Rule(id="", content=content.strip())
        self._global_rules.append(rule)
        self._save_global_rules()
        return rule

    def update_global_rule(
        self,
        rule_id: str,
        *,
        content: str | None = None,
        enabled: bool | None = None,
    ) -> Rule | None:
        for rule in self._global_rules:
            if rule.id == rule_id:
                if content is not None:
                    rule.content = content.strip()
                if enabled is not None:
                    rule.enabled = enabled
                self._save_global_rules()
                return rule
        return None

    def delete_global_rule(self, rule_id: str) -> bool:
        before = len(self._global_rules)
        self._global_rules = [r for r in self._global_rules if r.id != rule_id]
        if len(self._global_rules) < before:
            self._save_global_rules()
            return True
        return False

    # ── 会话级规则 CRUD（委托 RulesStore）─────────────────

    def list_session_rules(self, session_id: str) -> list[Rule]:
        if self._db_store is None:
            return []
        return self._db_store.list_rules(session_id)

    def get_enabled_session_rules(self, session_id: str) -> list[Rule]:
        return [r for r in self.list_session_rules(session_id) if r.enabled]

    def add_session_rule(self, session_id: str, content: str) -> Rule | None:
        if self._db_store is None:
            return None
        rule = Rule(id="", content=content.strip())
        self._db_store.save_rule(session_id, rule)
        return rule

    def update_session_rule(
        self,
        session_id: str,
        rule_id: str,
        *,
        content: str | None = None,
        enabled: bool | None = None,
    ) -> Rule | None:
        if self._db_store is None:
            return None
        return self._db_store.update_rule(session_id, rule_id, content=content, enabled=enabled)

    def delete_session_rule(self, session_id: str, rule_id: str) -> bool:
        if self._db_store is None:
            return False
        return self._db_store.delete_rule(session_id, rule_id)

    # ── Prompt 组装 ─────────────────────────────────────

    def compose_rules_prompt(self, session_id: str | None = None) -> str:
        """将全局规则和会话规则组装为可注入 system prompt 的文本段。"""
        parts: list[str] = []

        global_rules = self.get_enabled_global_rules()
        if global_rules:
            lines = ["<user_rules>", "以下是用户配置的自定义规则，请严格遵守："]
            for r in global_rules:
                lines.append(f"- {r.content}")
            lines.append("</user_rules>")
            parts.append("\n".join(lines))

        if session_id:
            session_rules = self.get_enabled_session_rules(session_id)
            if session_rules:
                lines = ["<session_rules>", "以下是当前会话的特定规则："]
                for r in session_rules:
                    lines.append(f"- {r.content}")
                lines.append("</session_rules>")
                parts.append("\n".join(lines))

        return "\n\n".join(parts)
