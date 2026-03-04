# Skill 用户隔离方案设计

## 1. 现状分析

### 1.1 当前 Skill 生命周期（全局共享）

```
api.py lifespan（进程启动时，一次性初始化）
│
├─ _skillpack_loader = SkillpackLoader(_config)     ← 进程级单例
│   └─ load_all() 扫描 3 个目录：
│       ├─ system:  excelmanus/skillpacks/system     ← 内置技能
│       ├─ user:    ~/.excelmanus/skillpacks          ← OS 用户级（非应用用户级）
│       └─ project: .excelmanus/skillpacks            ← 项目级（所有用户共享）
│
├─ _skill_router = SkillRouter(_config, _skillpack_loader)  ← 进程级单例
│
└─ _skillpack_manager = SkillpackManager(_config, _skillpack_loader) ← 进程级单例
```

**关键问题：所有组件都是进程级单例，所有用户看到完全相同的技能集合。**

| 组件 | 实例化 | 用户感知 |
|------|--------|----------|
| `SkillpackLoader` | 进程级单例（`api.py` lifespan） | ❌ 无 user_id 参数 |
| `SkillRouter` | 进程级单例 | ❌ `route()` 无 user_id |
| `SkillpackManager` | 进程级 + per-session 各一个 | ❌ CRUD 写入共享目录 |
| Skill API 端点 | 使用全局 `_skillpack_manager` | ❌ 不提取 user_id |
| `AgentEngine` | per-session，但引用共享 router | ❌ 所有用户共享同一 router |

### 1.2 数据流追踪

```
用户请求 → api.py chat_stream
  → _get_isolation_user_id(request) → user_id ✅ 已有
  → SessionManager.acquire_for_chat(session_id, user_id=...)
    → _create_engine_with_history(...)
      → IsolatedWorkspace.resolve(user_id=...) → per-user workspace ✅ 已有
      → engine_config = replace(config, workspace_root=user_ws_root)
      → AgentEngine(
          config=engine_config,        ← ✅ per-user config
          skill_router=self._skill_router,  ← ❌ 共享全局 router!
        )
        → self._skillpack_manager = SkillpackManager(config, skill_router._loader)
                                                            ↑ 共享 loader!
```

### 1.3 已有隔离模式（可复用）

| 子系统 | 隔离机制 | 代码位置 |
|--------|----------|----------|
| 工作区 | `UserContext.create()` → `{root}/users/{user_id}/` | `user_context.py:46-56` |
| DB Store | `UserScope` + `ScopedDatabase` + per-store `user_id` 过滤 | `user_scope.py` |
| 会话 | `SessionManager.acquire_for_chat` 校验 session 归属 | `session.py:577-581` |
| 记忆 | `MemoryStore(conn, user_id=user_id)` | `session.py:388-392` |
| 配置 | `UserConfigStore(conn, user_id=user_id)` | `session.py:289-297` |

---

## 2. 隔离切入点

需改动的 6 个层次：

```
┌──────────────────────────────────────────────────────────┐
│ Layer 1: 存储路径 — 增加 per-user skill 目录             │
│ Layer 2: 加载器   — SkillpackLoader 支持 per-user 实例   │
│ Layer 3: 路由器   — SkillRouter 使用 per-user loader     │
│ Layer 4: 管理器   — SkillpackManager 写入 per-user 目录  │
│ Layer 5: API 端点 — 提取 user_id，使用 per-user 管理器   │
│ Layer 6: 引擎     — 接收 per-user router                 │
└──────────────────────────────────────────────────────────┘
```

---

## 3. 方案设计：分层 Loader + Per-User Overlay

### 3.1 设计原则

- **三层可见性**：system（全局只读）→ user（私有读写）→ project（项目共享读写）
- **复用已有隔离模式**：用户技能目录跟随 `UserContext.workspace_root`
- **最小改动**：不改变 Skillpack/SKILL.md 格式，不引入新 DB 表
- **向后兼容**：单用户/CLI 模式行为不变（user_id=None → 匿名模式）

### 3.2 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                      API Layer                           │
│  /api/v1/skills/* → extract user_id → UserSkillService  │
│  /api/v1/chat/*   → extract user_id → SessionManager    │
└────────────┬────────────────────────────┬───────────────┘
             │                            │
             ▼                            ▼
┌────────────────────────┐  ┌──────────────────────────────┐
│  UserSkillService      │  │  SessionManager              │
│  (进程级单例，缓存)     │  │  (进程级单例)                 │
│                        │  │                              │
│  get_loader(user_id)   │◄─┤  _create_engine_with_history │
│  get_router(user_id)   │  │  → 使用 user loader/router   │
│  get_manager(user_id)  │  │                              │
└────────┬───────────────┘  └──────────────────────────────┘
         │
         │  Per-user 缓存: {user_id → (loader, router, manager)}
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  SkillpackLoader (per-user instance)                     │
│                                                          │
│  _iter_discovery_roots() 扫描顺序：                       │
│  ① system:  excelmanus/skillpacks/system   (共享, 只读)  │
│  ② user:    {user_workspace}/skillpacks/   (私有, 读写)  │
│  ③ project: .excelmanus/skillpacks         (共享, 读写)  │
│                                                          │
│  user_id=None 时: ② 回退到 ~/.excelmanus/skillpacks     │
└─────────────────────────────────────────────────────────┘
```

### 3.3 技能可见性矩阵

| 操作 | system 技能 | user 技能 | project 技能 |
|------|-------------|-----------|--------------|
| 用户 A 查看 | ✅ | ✅ 仅自己的 | ✅ |
| 用户 A 创建 | ❌ | ✅ 写入自己目录 | ✅（可选，需权限） |
| 用户 A 删除 | ❌ | ✅ 仅自己的 | ✅（可选，需权限） |
| 用户 B 查看 A 的 user 技能 | ❌ | ❌ | ❌ |
| 路由命中 | 全体 | 仅 owner | 全体 |

### 3.4 存储路径映射

```
# 认证开启（多用户模式）
User A:
  workspace:  {root}/users/alice/
  user skills: {root}/users/alice/skillpacks/
  
User B:
  workspace:  {root}/users/bob/
  user skills: {root}/users/bob/skillpacks/

# 认证关闭（单用户模式，向后兼容）
  workspace:  {root}/
  user skills: ~/.excelmanus/skillpacks/
```

---

## 4. 逐文件改动设计

### 4.1 新增: `excelmanus/skillpacks/user_skill_service.py`

**核心协调层**，为 API 端点和 SessionManager 提供 per-user 的 Loader/Router/Manager。

```python
class UserSkillService:
    """Per-user 技能服务：缓存 Loader/Router/Manager 实例。"""

    def __init__(
        self,
        base_config: ExcelManusConfig,
        registry: ToolRegistry,
    ) -> None:
        self._base_config = base_config
        self._registry = registry
        self._lock = threading.Lock()
        # 共享 system loader（只加载 system 技能，所有用户共享）
        self._system_loader: SkillpackLoader | None = None
        # per-user 缓存
        self._user_cache: dict[str | None, _UserSkillBundle] = {}

    def get_loader(self, user_id: str | None) -> SkillpackLoader:
        """获取 per-user loader（含缓存）。"""
        ...

    def get_router(self, user_id: str | None) -> SkillRouter:
        """获取 per-user router。"""
        ...

    def get_manager(self, user_id: str | None) -> SkillpackManager:
        """获取 per-user manager（写入 user 目录）。"""
        ...

    def invalidate(self, user_id: str | None = None) -> None:
        """使缓存失效（技能变更后调用）。"""
        ...

    def _resolve_user_skill_dir(self, user_id: str | None) -> Path:
        """计算用户私有技能目录路径。"""
        if user_id is None:
            return Path(self._base_config.skills_user_dir).expanduser()
        # 复用 UserContext 的路径规则
        data_root = self._base_config.data_root
        if data_root:
            return Path(data_root) / "users" / user_id / "skillpacks"
        return Path(self._base_config.workspace_root) / "users" / user_id / "skillpacks"
```

**缓存策略**：
- 按 `user_id` 键缓存 `(loader, router, manager)` 三元组
- 技能 CRUD 操作后调用 `invalidate(user_id)` 清除该用户缓存
- 可选 LRU 淘汰（上限如 128 用户），避免内存膨胀
- system 技能仅加载一次，所有用户共享引用

### 4.2 修改: `excelmanus/skillpacks/loader.py`

**改动量：小** — `_iter_discovery_roots` 中 user 目录改为使用 config 中的值（已经是这样），无需结构性变更。

关键：确保每个 per-user loader 实例的 `config.skills_user_dir` 指向用户私有目录即可。

```python
# 无需修改 _iter_discovery_roots 逻辑
# 只需在创建 loader 时传入正确的 config：
#   config.skills_user_dir = "{user_workspace}/skillpacks/"
# 现有代码已经会读取 config.skills_user_dir 并扫描
```

### 4.3 修改: `excelmanus/skillpacks/manager.py`

**改动量：小** — 增加 `user_dir` 写入目标。

当前 `SkillpackManager` 的 CRUD 全部写入 `_project_dir`。需要区分：
- 用户通过 API 创建的技能 → 默认写入 **user dir**（私有）
- 用户显式共享到项目 → 写入 **project dir**（需要额外参数）

```python
class SkillpackManager:
    def __init__(self, config, loader, *, user_skill_dir: Path | None = None):
        ...
        self._project_dir = self._resolve_path(config.skills_project_dir)
        # 新增：用户私有技能目录（为 None 时回退到 project_dir）
        self._user_dir = user_skill_dir or self._project_dir

    def create_skillpack(self, *, name, payload, actor, scope="user"):
        """scope="user" 写入用户目录，scope="project" 写入项目目录。"""
        target_dir = self._user_dir if scope == "user" else self._project_dir
        ...
        skill_dir = target_dir / normalized_name
        ...

    def delete_skillpack(self, *, name, actor, reason=""):
        """只允许删除 source="user" 的技能（自己创建的）。"""
        ...
        if skill.source == "system":
            raise SkillpackInputError("不能删除系统技能")
        # source="user" → 删除 user dir 中的文件
        # source="project" → 需要项目权限检查
        ...
```

### 4.4 修改: `excelmanus/api.py`

**改动量：中** — Skill 端点提取 user_id，使用 per-user manager。

```python
# 模块级
_user_skill_service: UserSkillService | None = None

# lifespan 中初始化
_user_skill_service = UserSkillService(
    base_config=_config,
    registry=_tool_registry,
)

# 端点改造
@_router.get("/api/v1/skills")
async def list_skills(raw_request: Request):
    user_id = _get_isolation_user_id(raw_request)
    manager = _user_skill_service.get_manager(user_id)
    details = manager.list_skillpacks()
    return [_to_skill_summary(detail) for detail in details]

@_router.post("/api/v1/skills")
async def create_skill(request: SkillpackCreateRequest, raw_request: Request):
    user_id = _get_isolation_user_id(raw_request)
    manager = _user_skill_service.get_manager(user_id)
    detail = manager.create_skillpack(
        name=request.name, payload=request.payload, actor=user_id or "api",
    )
    _user_skill_service.invalidate(user_id)  # 清缓存
    return SkillpackMutationResponse(...)

# 其他 CRUD 端点类似...
```

### 4.5 修改: `excelmanus/session.py`

**改动量：中** — `_create_engine_with_history` 使用 per-user router。

```python
class SessionManager:
    def __init__(self, ..., user_skill_service: UserSkillService | None = None):
        ...
        self._user_skill_service = user_skill_service

    def _create_engine_with_history(self, ..., user_id=None, ...):
        ...
        # 获取 per-user skill router
        if self._user_skill_service is not None:
            user_router = self._user_skill_service.get_router(user_id)
        else:
            user_router = self._skill_router  # 兼容回退

        engine = AgentEngine(
            config=engine_config,
            registry=self._registry,
            skill_router=user_router,   # ← per-user router
            ...
        )
```

### 4.6 修改: `excelmanus/engine.py`

**改动量：极小** — 无结构性改变，已通过参数注入获得 per-user router。

当前代码：
```python
self._skill_router = skill_router
self._skillpack_manager = (
    SkillpackManager(config, skill_router._loader)
    if skill_router is not None
    else None
)
```

改为让 `UserSkillService` 统一管理 manager 的创建。或者保持现有模式——
engine 的 `_skillpack_manager` 已经是 per-session 创建的，只要 loader 是 per-user 的，
manager 自然就是 per-user 的。但需要传入 `user_skill_dir`：

```python
self._skillpack_manager = (
    SkillpackManager(
        config, skill_router._loader,
        user_skill_dir=Path(config.skills_user_dir),  # per-user
    )
    if skill_router is not None
    else None
)
```

---

## 5. 向后兼容

| 场景 | 行为 |
|------|------|
| 认证关闭（单用户） | `user_id=None` → 匿名模式，`skills_user_dir` 保持 `~/.excelmanus/skillpacks`，行为完全不变 |
| CLI 模式 | 不经过 API，直接使用全局 loader，行为不变 |
| 已有 project 技能 | project 目录不变，所有用户可见 |
| 已有 user 目录技能 | 认证开启后，旧 `~/.excelmanus/skillpacks` 中的技能不会自动迁移；需手动复制到用户目录或保留为 system 级 |

---

## 6. 实施阶段

### Phase 1: 基础隔离（最小可用）
1. 新建 `UserSkillService`
2. 修改 `SkillpackManager` 支持 `user_skill_dir`
3. 修改 `session.py` 使用 per-user router
4. 修改 skill API 端点提取 user_id
5. 测试：多用户场景下技能互不可见

### Phase 2: 技能共享与协作
1. 添加 "发布到项目" 功能（user skill → project skill）
2. 技能所有权标记（metadata 中增加 `owner_user_id`）
3. 项目级技能的权限控制（创建/删除需项目权限）

### Phase 3: 技能市场（可选）
1. ClawHub 集成 per-user（当前已有全局 ClawHub）
2. 技能订阅/安装到个人目录
3. 技能版本管理

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Per-user loader 内存膨胀 | 100 用户 × 每 loader ~1MB = ~100MB | LRU 缓存 + 共享 system 技能引用 |
| 技能变更通知 | 用户 A 创建技能后 loader 缓存需失效 | CRUD 后调用 `invalidate(user_id)` |
| 旧技能迁移 | 升级后旧 `~/.excelmanus/skillpacks` 技能归属不明 | 提供迁移脚本；认证关闭时保持旧行为 |
| Engine 生命周期 | Engine 持有 router 引用，缓存失效后旧 engine 仍用旧 router | 可接受：同一会话内技能列表一致即可，新会话获取最新 |
| 并发安全 | 多请求同时创建同一用户的 loader | `UserSkillService` 内部加锁 |

---

## 8. 测试策略

### 单元测试
- `test_user_skill_service.py`:
  - 不同 user_id 获取不同 loader/router/manager
  - user_id=None 回退到全局行为
  - 缓存命中 & invalidate
  - LRU 淘汰

### 集成测试
- `test_skill_isolation.py`:
  - 用户 A 创建技能 → 用户 B 不可见
  - 用户 A 删除技能 → 不影响用户 B
  - system 技能对所有用户可见
  - project 技能对所有用户可见
  - 用户 A 的技能在路由时只匹配给用户 A
  - Skill API 端点返回正确的 per-user 列表

### 回归测试
- 所有现有 skill 相关测试在 `user_id=None` 模式下行为不变
- CLI 模式不受影响
