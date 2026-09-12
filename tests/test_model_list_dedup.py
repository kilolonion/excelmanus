"""list_models：只列出模型档案，并用 active 标记当前激活项。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_config(model: str = "gpt-4o", base_url: str = "https://api.openai.com/v1", api_key: str = "sk-test"):
    return SimpleNamespace(model=model, base_url=base_url, api_key=api_key)


def _make_config_store(profiles: list[dict]):
    store = MagicMock()
    store.list_profiles.return_value = profiles
    return store


def _list_models(config, config_store, active_name=None):
    db_profiles = config_store.list_profiles()
    models: list[dict] = []
    for p in db_profiles:
        models.append({
            "name": p["name"],
            "model": p["model"],
            "display_name": p.get("name", ""),
            "description": p.get("description", ""),
            "active": p["name"] == active_name,
            "base_url": p.get("base_url", ""),
        })
    if models and not any(m["active"] for m in models):
        models[0]["active"] = True
    return models


class TestModelListActive(unittest.IsolatedAsyncioTestCase):
    async def test_single_profile_is_active(self):
        config = _make_config(model="openai-codex/gpt-5.3-codex")
        profiles = [{
            "name": "openai-codex/gpt-5.3-codex",
            "model": "openai-codex/gpt-5.3-codex",
            "description": "Codex 5.3",
            "base_url": "https://api.openai.com/v1",
        }]
        models = _list_models(
            config, _make_config_store(profiles),
            active_name="openai-codex/gpt-5.3-codex",
        )
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "openai-codex/gpt-5.3-codex")
        self.assertTrue(models[0]["active"])

    async def test_no_active_name_marks_first_profile(self):
        config = _make_config()
        profiles = [{
            "name": "my-gpt4o",
            "model": "gpt-4o",
            "description": "自定义 GPT-4o",
            "base_url": "https://api.openai.com/v1",
        }]
        models = _list_models(config, _make_config_store(profiles), active_name=None)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["name"], "my-gpt4o")
        self.assertTrue(models[0]["active"])

    async def test_empty_profiles(self):
        models = _list_models(_make_config(), _make_config_store([]), active_name=None)
        self.assertEqual(models, [])

    async def test_active_flag_only_one(self):
        config = _make_config()
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
        models = _list_models(config, _make_config_store(profiles), active_name="deepseek")
        active_models = [m for m in models if m["active"]]
        self.assertEqual(len(active_models), 1)
        self.assertEqual(active_models[0]["name"], "deepseek")
        self.assertNotIn("default", [m["name"] for m in models])


if __name__ == "__main__":
    unittest.main()
