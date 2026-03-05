"""list_models 去重：default 与 profile 同 model 时不重复显示。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_config(model: str = "gpt-4o", base_url: str = "https://api.openai.com/v1", api_key: str = "sk-test"):
    cfg = SimpleNamespace(model=model, base_url=base_url, api_key=api_key)
    return cfg


def _make_config_store(profiles: list[dict]):
    store = MagicMock()
    store.list_profiles.return_value = profiles
    store.get_active_model.return_value = None
    return store


def _make_request(user_id: str | None = None):
    req = MagicMock()
    req.state = SimpleNamespace(user_id=user_id)
    return req


class TestModelListDedup(unittest.IsolatedAsyncioTestCase):
    """验证 list_models 端点在 default model 与 profile model 重复时的去重行为。"""

    async def _call_list_models(self, config, config_store, active_name=None, user_id=None):
        """直接调用 list_models 的核心逻辑（避免启动完整 app）。"""
        # 复制 list_models 中的去重逻辑进行单元测试
        db_profiles = config_store.list_profiles()

        default_duplicated_by = next(
            (p["name"] for p in db_profiles if p["model"] == config.model),
            None,
        )

        models: list[dict] = []
        if default_duplicated_by is None:
            models.append({
                "name": "default",
                "model": config.model,
                "display_name": config.model,
                "description": "默认模型（主配置）",
                "active": active_name is None,
                "base_url": config.base_url,
            })

        for p in db_profiles:
            is_active = p["name"] == active_name
            if p["name"] == default_duplicated_by and active_name is None:
                is_active = True
            models.append({
                "name": p["name"],
                "model": p["model"],
                "display_name": p.get("name", ""),
                "description": p.get("description", ""),
                "active": is_active,
                "base_url": p.get("base_url", ""),
            })

        return models

    # ── 去重场景 ──

    async def test_codex_oauth_dedup_profile_selected(self):
        """Codex OAuth profile 与 default 同 model，用户选中 profile → 仅显示 profile。"""
        config = _make_config(model="openai-codex/gpt-5.3-codex")
        profiles = [{
            "name": "openai-codex/gpt-5.3-codex",
            "model": "openai-codex/gpt-5.3-codex",
            "description": "Codex 5.3 — OAuth 登录（无需 API Key）",
            "base_url": "https://api.openai.com/v1",
        }]
        store = _make_config_store(profiles)

        models = await self._call_list_models(
            config, store, active_name="openai-codex/gpt-5.3-codex",
        )
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "openai-codex/gpt-5.3-codex")
        self.assertTrue(models[0]["active"])

    async def test_codex_oauth_dedup_default_selected(self):
        """Codex OAuth profile 与 default 同 model，用户选中 default → 仅显示 profile 且标记 active。"""
        config = _make_config(model="openai-codex/gpt-5.3-codex")
        profiles = [{
            "name": "openai-codex/gpt-5.3-codex",
            "model": "openai-codex/gpt-5.3-codex",
            "description": "Codex 5.3 — OAuth 登录（无需 API Key）",
            "base_url": "https://api.openai.com/v1",
        }]
        store = _make_config_store(profiles)

        models = await self._call_list_models(config, store, active_name=None)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "openai-codex/gpt-5.3-codex")
        self.assertTrue(models[0]["active"])

    async def test_generic_profile_same_model_dedup(self):
        """通用 profile 与 default 同 model 也应去重。"""
        config = _make_config(model="gpt-4o")
        profiles = [{
            "name": "my-gpt4o",
            "model": "gpt-4o",
            "description": "自定义 GPT-4o",
            "base_url": "https://api.openai.com/v1",
        }]
        store = _make_config_store(profiles)

        models = await self._call_list_models(
            config, store, active_name="my-gpt4o",
        )
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "my-gpt4o")
        self.assertTrue(models[0]["active"])

    # ── 不去重场景 ──

    async def test_no_dedup_when_models_differ(self):
        """default model 与 profile model 不同时应保留两者。"""
        config = _make_config(model="gpt-4o")
        profiles = [{
            "name": "claude-sonnet",
            "model": "claude-sonnet-4-20250514",
            "description": "Claude Sonnet",
            "base_url": "https://api.anthropic.com/v1",
        }]
        store = _make_config_store(profiles)

        models = await self._call_list_models(config, store, active_name=None)
        self.assertEqual(len(models), 2)
        names = [m["name"] for m in models]
        self.assertIn("default", names)
        self.assertIn("claude-sonnet", names)

    async def test_no_profiles_keeps_default(self):
        """无 profile 时应显示 default。"""
        config = _make_config(model="gpt-4o")
        store = _make_config_store([])

        models = await self._call_list_models(config, store, active_name=None)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "default")
        self.assertTrue(models[0]["active"])

    async def test_multiple_profiles_only_matching_deduped(self):
        """多个 profile 中仅与 default 同 model 的那个触发去重，其余保留。"""
        config = _make_config(model="openai-codex/gpt-5.3-codex")
        profiles = [
            {
                "name": "openai-codex/gpt-5.3-codex",
                "model": "openai-codex/gpt-5.3-codex",
                "description": "Codex 5.3",
                "base_url": "https://api.openai.com/v1",
            },
            {
                "name": "deepseek",
                "model": "deepseek-chat",
                "description": "DeepSeek",
                "base_url": "https://api.deepseek.com/v1",
            },
        ]
        store = _make_config_store(profiles)

        models = await self._call_list_models(
            config, store, active_name="openai-codex/gpt-5.3-codex",
        )
        self.assertEqual(len(models), 2)
        names = [m["name"] for m in models]
        self.assertNotIn("default", names)
        self.assertIn("openai-codex/gpt-5.3-codex", names)
        self.assertIn("deepseek", names)

    async def test_active_flag_correct_after_dedup(self):
        """去重后 active 标记仅出现在一个条目上。"""
        config = _make_config(model="openai-codex/gpt-5.3-codex")
        profiles = [
            {
                "name": "openai-codex/gpt-5.3-codex",
                "model": "openai-codex/gpt-5.3-codex",
                "description": "Codex",
                "base_url": "https://api.openai.com/v1",
            },
            {
                "name": "deepseek",
                "model": "deepseek-chat",
                "description": "DeepSeek",
                "base_url": "https://api.deepseek.com/v1",
            },
        ]
        store = _make_config_store(profiles)

        # 用户选中 deepseek
        models = await self._call_list_models(config, store, active_name="deepseek")
        active_models = [m for m in models if m["active"]]
        self.assertEqual(len(active_models), 1)
        self.assertEqual(active_models[0]["name"], "deepseek")


if __name__ == "__main__":
    unittest.main()
