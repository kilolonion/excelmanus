"""账号合并功能测试 — user_oauth_links 表、合并流程、OAuth links 管理。"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from excelmanus.auth.models import UserRecord, UserRole, MergeRequiredResponse
from excelmanus.auth.security import create_merge_token, decode_merge_token
from excelmanus.auth.store import UserStore


# ── Fixtures ──────────────────────────────────────────────


class _FakeDB:
    """Minimal Database-like object for UserStore."""

    def __init__(self, tmp_path):
        db_path = str(tmp_path / "test_auth.db")
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.is_pg = False


@pytest.fixture
def store(tmp_path):
    db = _FakeDB(tmp_path)
    return UserStore(db)


@pytest.fixture
def user_with_password(store: UserStore):
    """创建一个有密码的邮箱注册用户。"""
    user = UserRecord(
        email="alice@example.com",
        display_name="Alice",
        password_hash="$2b$12$fakehash",
        role=UserRole.USER,
    )
    store.create_user(user)
    return user


@pytest.fixture
def user_with_github(store: UserStore):
    """创建一个通过 GitHub 注册的用户。"""
    user = UserRecord(
        email="bob@example.com",
        display_name="Bob",
        role=UserRole.USER,
        oauth_provider="github",
        oauth_id="gh_12345",
    )
    store.create_user(user)
    # 同时创建 oauth link
    store.create_oauth_link(
        user_id=user.id,
        provider="github",
        oauth_id="gh_12345",
        display_name="Bob",
    )
    return user


# ── user_oauth_links 表 DDL + 迁移 ────────────────────────


class TestOAuthLinksTable:
    def test_table_created(self, store: UserStore):
        """user_oauth_links 表应在初始化时自动创建。"""
        row = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_oauth_links'"
        ).fetchone()
        assert row is not None

    def test_migration_from_users_table(self, store: UserStore):
        """已有 users.oauth_provider 数据应自动迁移到 user_oauth_links。"""
        # 直接在 users 表中插入带 OAuth 字段的用户
        user = UserRecord(
            email="legacy@example.com",
            display_name="Legacy",
            role=UserRole.USER,
            oauth_provider="google",
            oauth_id="g_99999",
        )
        store.create_user(user)

        # 强制重新运行迁移
        store._migrate_oauth_links()

        links = store.get_oauth_links(user.id)
        assert len(links) == 1
        assert links[0]["provider"] == "google"
        assert links[0]["oauth_id"] == "g_99999"

    def test_migration_idempotent(self, store: UserStore):
        """多次运行迁移不应产生重复记录。"""
        user = UserRecord(
            email="idem@example.com",
            oauth_provider="github",
            oauth_id="gh_idem",
        )
        store.create_user(user)
        store._migrate_oauth_links()
        store._migrate_oauth_links()

        links = store.get_oauth_links(user.id)
        assert len(links) == 1


# ── OAuth Links CRUD ─────────────────────────────────────


class TestOAuthLinksCRUD:
    def test_create_and_get(self, store: UserStore, user_with_password):
        link_id = store.create_oauth_link(
            user_id=user_with_password.id,
            provider="google",
            oauth_id="g_123",
            display_name="Alice Google",
        )
        assert link_id

        links = store.get_oauth_links(user_with_password.id)
        assert len(links) == 1
        assert links[0]["provider"] == "google"
        assert links[0]["oauth_id"] == "g_123"

    def test_get_by_oauth_via_links_table(self, store: UserStore, user_with_github):
        """get_by_oauth 应优先查 user_oauth_links 表。"""
        found = store.get_by_oauth("github", "gh_12345")
        assert found is not None
        assert found.id == user_with_github.id

    def test_delete_link(self, store: UserStore, user_with_github):
        deleted = store.delete_oauth_link(user_with_github.id, "github")
        assert deleted is True
        assert store.count_oauth_links(user_with_github.id) == 0

    def test_delete_nonexistent(self, store: UserStore, user_with_password):
        deleted = store.delete_oauth_link(user_with_password.id, "github")
        assert deleted is False

    def test_count_links(self, store: UserStore, user_with_password):
        assert store.count_oauth_links(user_with_password.id) == 0
        store.create_oauth_link(user_with_password.id, "github", "gh_1")
        assert store.count_oauth_links(user_with_password.id) == 1
        store.create_oauth_link(user_with_password.id, "google", "g_1")
        assert store.count_oauth_links(user_with_password.id) == 2

    def test_unique_constraint(self, store: UserStore, user_with_password):
        """同一 provider+oauth_id 不能绑定两次。"""
        store.create_oauth_link(user_with_password.id, "github", "gh_dup")
        with pytest.raises(Exception):
            store.create_oauth_link(user_with_password.id, "github", "gh_dup")


# ── Merge Token ──────────────────────────────────────────


class TestMergeToken:
    def test_create_and_decode(self):
        token = create_merge_token(
            existing_user_id="user-123",
            provider="google",
            oauth_id="g_456",
            email="test@example.com",
            display_name="Test",
        )
        payload = decode_merge_token(token)
        assert payload is not None
        assert payload["sub"] == "user-123"
        assert payload["provider"] == "google"
        assert payload["oauth_id"] == "g_456"
        assert payload["type"] == "merge"

    def test_invalid_token(self):
        assert decode_merge_token("invalid.token.here") is None

    def test_wrong_type_rejected(self):
        """非 merge 类型的 token 应被拒绝。"""
        from excelmanus.auth.security import create_access_token
        access = create_access_token({"sub": "user-1", "role": "user"})
        assert decode_merge_token(access) is None


# ── _handle_oauth_user 合并流程 ────────────────────────────


class TestHandleOAuthUser:
    """测试 _handle_oauth_user 的三种路径。"""

    def test_existing_oauth_binding_login(self, store: UserStore, user_with_github):
        """已绑定的 OAuth 用户直接登录。"""
        from excelmanus.auth.router import _handle_oauth_user
        from excelmanus.auth.oauth import OAuthUserInfo

        info = OAuthUserInfo(
            provider="github",
            oauth_id="gh_12345",
            email="bob@example.com",
            display_name="Bob",
            avatar_url=None,
        )
        result = _handle_oauth_user(store, info)
        from excelmanus.auth.models import TokenResponse
        assert isinstance(result, TokenResponse)
        assert result.user.email == "bob@example.com"

    def test_email_match_triggers_merge(self, store: UserStore, user_with_password):
        """同邮箱但不同 provider 应触发合并确认。"""
        from excelmanus.auth.router import _handle_oauth_user
        from excelmanus.auth.oauth import OAuthUserInfo

        info = OAuthUserInfo(
            provider="google",
            oauth_id="g_new_123",
            email="alice@example.com",  # 与 user_with_password 同邮箱
            display_name="Alice Google",
            avatar_url=None,
        )
        result = _handle_oauth_user(store, info)
        assert isinstance(result, MergeRequiredResponse)
        assert result.existing_email == "alice@example.com"
        assert result.new_provider == "google"
        assert result.merge_token  # 非空

    def test_new_user_creates_account_and_link(self, store: UserStore):
        """全新邮箱应创建用户和 OAuth 绑定。"""
        from excelmanus.auth.router import _handle_oauth_user
        from excelmanus.auth.oauth import OAuthUserInfo

        info = OAuthUserInfo(
            provider="google",
            oauth_id="g_brand_new",
            email="newuser@example.com",
            display_name="New User",
            avatar_url=None,
        )
        result = _handle_oauth_user(store, info)
        from excelmanus.auth.models import TokenResponse
        assert isinstance(result, TokenResponse)
        assert result.user.email == "newuser@example.com"

        # 验证 OAuth link 也被创建
        user = store.get_by_email("newuser@example.com")
        assert user is not None
        links = store.get_oauth_links(user.id)
        assert len(links) == 1
        assert links[0]["provider"] == "google"


# ── UserPublic oauth_providers ────────────────────────────


class TestUserPublicOAuthProviders:
    def test_from_record_with_providers(self):
        from excelmanus.auth.models import UserPublic
        user = UserRecord(email="x@x.com")
        pub = UserPublic.from_record(user, oauth_providers=["github", "google"])
        assert pub.oauth_providers == ["github", "google"]

    def test_from_record_without_providers(self):
        from excelmanus.auth.models import UserPublic
        user = UserRecord(email="x@x.com")
        pub = UserPublic.from_record(user)
        assert pub.oauth_providers == []
