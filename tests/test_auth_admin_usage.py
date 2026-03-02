from __future__ import annotations

from datetime import datetime, timezone

from excelmanus.auth.models import UserRecord
from excelmanus.auth.router import _build_admin_llm_usage_index, _infer_provider_from_model
from excelmanus.auth.store import UserStore
from excelmanus.database import Database


def _insert_llm_call(
    db: Database,
    *,
    user_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    created_at: str,
) -> None:
    db.conn.execute(
        "INSERT INTO llm_call_log "
        "(session_id, turn, iteration, model, prompt_tokens, completion_tokens, "
        "cached_tokens, total_tokens, has_tool_calls, thinking_chars, stream, latency_ms, "
        "error, created_at, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "s-1",
            1,
            1,
            model,
            prompt_tokens,
            completion_tokens,
            0,
            total_tokens,
            0,
            0,
            0,
            10.0,
            None,
            created_at,
            user_id,
        ),
    )
    db.conn.commit()


def test_infer_provider_from_model() -> None:
    assert _infer_provider_from_model("gpt-4o") == "openai"
    assert _infer_provider_from_model("claude-3.5-sonnet") == "anthropic"
    assert _infer_provider_from_model("gemini-2.5-pro") == "gemini"
    assert _infer_provider_from_model("qwen-plus") == "qwen"
    assert _infer_provider_from_model("deepseek-v3") == "deepseek"
    assert _infer_provider_from_model("openai-codex/gpt-5.3-codex") == "openai-codex"


def test_build_admin_llm_usage_index_groups_by_provider_and_model(tmp_path) -> None:
    db = Database(str(tmp_path / "admin-usage.db"))
    store = UserStore(db)

    user_a = store.create_user(UserRecord(email="a@example.com", password_hash="x"))
    user_b = store.create_user(UserRecord(email="b@example.com", password_hash="x"))

    now = datetime.now(tz=timezone.utc).isoformat()

    _insert_llm_call(
        db,
        user_id=user_a.id,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=30,
        total_tokens=130,
        created_at=now,
    )
    _insert_llm_call(
        db,
        user_id=user_a.id,
        model="gpt-4o",
        prompt_tokens=50,
        completion_tokens=20,
        total_tokens=70,
        created_at=now,
    )
    _insert_llm_call(
        db,
        user_id=user_a.id,
        model="claude-3.5-sonnet",
        prompt_tokens=80,
        completion_tokens=20,
        total_tokens=100,
        created_at=now,
    )
    _insert_llm_call(
        db,
        user_id=user_a.id,
        model="openai-codex/gpt-5.3-codex",
        prompt_tokens=200,
        completion_tokens=40,
        total_tokens=240,
        created_at=now,
    )

    _insert_llm_call(
        db,
        user_id=user_b.id,
        model="qwen-plus",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        created_at=now,
    )

    usage_index = _build_admin_llm_usage_index(store, [user_a.id, user_b.id])

    usage_a = usage_index[user_a.id]
    assert usage_a["total_calls"] == 4
    assert usage_a["total_prompt_tokens"] == 430
    assert usage_a["total_completion_tokens"] == 110
    assert usage_a["total_tokens"] == 540

    providers_a = {p["provider"]: p for p in usage_a["providers"]}
    assert providers_a["openai-codex"]["total_tokens"] == 240
    assert providers_a["openai"]["total_tokens"] == 200
    assert providers_a["anthropic"]["total_tokens"] == 100

    openai_models = providers_a["openai"]["models"]
    assert len(openai_models) == 1
    assert openai_models[0]["model"] == "gpt-4o"
    assert openai_models[0]["calls"] == 2
    assert openai_models[0]["total_tokens"] == 200

    usage_b = usage_index[user_b.id]
    assert usage_b["total_calls"] == 1
    assert usage_b["total_tokens"] == 15
    assert usage_b["providers"][0]["provider"] == "qwen"

    db.close()
