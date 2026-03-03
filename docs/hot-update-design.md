# ExcelManus 前后端分离热更新方案（本地 + 服务器）

## 1. 现状（基于当前仓库）

当前项目已经具备热更新的基础能力：

- 远程部署脚本支持分离部署、健康检查、回滚、部署锁  
  `deploy/deploy.sh`
- 前端制品化发布与原子切换能力（`--frontend-artifact`）  
  `deploy/deploy.sh`
- 后端在线升级能力（版本检查、备份、更新）  
  `excelmanus/updater.py` + `excelmanus/api_routes_version.py`
- 前端具备“服务重启等待+自动探活刷新”组件  
  `web/src/hooks/use-server-restart.ts`

主要缺口：

- 后端重启仍偏“单实例重启”，会有短暂抖动
- 前端更新后缺少统一“新版本就绪”通知机制
- 前后端版本兼容关系没有显式清单（Manifest）
- 本地与服务器更新流程还没有统一成一套“热更新协议”

---

## 2. 设计目标

1. 支持前后端分离部署（两台服务器或单机分进程）
2. 支持本地开发机和生产服务器一致的更新策略
3. 更新过程可观测、可回滚、可重试
4. 对 SSE 长连接影响最小（尽量无感）

---

## 3. 总体方案（双轨 + 双阶段）

采用两条轨道：

- **轨道 A：部署热更新（Infra）**  
  通过 `deploy/deploy.sh` 做“候选版本启动 -> 健康检查 -> 流量切换 -> 旧版本下线”。
- **轨道 B：应用热更新（App）**  
  通过 `/api/v1/version/*` 与前端重启探活组件做“应用内升级提示与无感刷新”。

并按两阶段落地：

- **阶段 1（快速见效）**：在现有脚本上补齐“版本兼容清单 + 自动重启协同 + 客户端刷新提示”
- **阶段 2（高可用）**：引入前后端双实例蓝绿切换（或滚动）实现近零中断

---

## 4. 服务器分离场景（推荐流程）

### 4.1 后端热更新（Blue/Green）

1. 在新目录准备候选版本（代码 + 依赖）
2. 候选实例启动在影子端口（如 `8001`）
3. 通过 `/api/v1/health` + 冒烟接口校验
4. Nginx upstream 从 `8000` 原子切到 `8001`（`nginx -s reload`）
5. 等待旧连接排空（建议 30~60 秒，重点照顾 SSE）
6. 下线旧实例；保留回滚窗口

失败时：立即切回旧 upstream + 保留候选日志。

### 4.2 前端热更新（Artifact + Atomic Switch）

1. CI 构建 `Next standalone` 制品（含 `.next/standalone` / `.next/static` / `public`）
2. 上传制品到服务器，解压到 `stage` 目录
3. 校验构建完整性（`BUILD_ID`、`routes-manifest.json`、`server.js`）
4. 通过软链或目录原子替换切换 `current`
5. 前端进程 `reload`（优先）或快速重启

失败时：回滚到 `last_backup_path`，恢复旧静态资源目录。

---

## 5. 本地场景（开发机 + 本地生产模拟）

### 5.1 开发模式

- 前端：`next dev`（天然 HMR）
- 后端：建议 `uvicorn --reload`（仅本地）

### 5.2 本地生产模拟（与服务器一致）

建议引入本地反向代理（Nginx/Caddy）并采用双端口切换：

- 后端：`8000(active)` / `8001(candidate)`
- 前端：`3000(active)` / `3001(candidate)`

更新时与服务器同流程：候选启动 -> 健康检查 -> 代理切流 -> 旧实例下线。  
这样本地验证结果与线上一致，能提前发现切流类问题。

---

## 6. 版本兼容与热刷新机制

新增统一发布清单（Manifest），建议字段：

- `release_id`
- `backend_version`
- `frontend_build_id`
- `api_schema_version`
- `min_frontend_build_id`
- `min_backend_version`

前端启动后定时比对：

- 若 `frontend_build_id` 落后：弹出“新版本可用，点击刷新”
- 若 `api_schema_version` 不兼容：强制刷新并提示升级中

---

## 7. 失败处理与回滚策略

- 部署全局锁（已有，保留）
- 分阶段断点日志（prepare/start/verify/switch/drain/finalize）
- 每次切流都必须有“反向切回”动作
- 前端与后端独立回滚，避免双端同时回滚扩大故障面
- 数据变更必须前置备份（当前 `updater.py` 机制可复用）

---

## 8. 按当前仓库的实施清单

### P0（建议先做，1~2 天）

1. 增加发布 Manifest 与兼容校验接口  
   `excelmanus/api_routes_version.py` / `excelmanus/api.py`
2. 前端版本页升级后自动触发重启探活  
   `web/src/components/settings/VersionTab.tsx`
3. 部署后把 `release_id + build_id + commit` 落盘并暴露给健康接口  
   `deploy/deploy.sh` + `excelmanus/api.py`

### P1（高可用，3~5 天）

1. 后端双端口候选启动 + Nginx upstream 切流  
   `deploy/deploy.sh` + `deploy/nginx.conf`
2. 前端 reload 优先策略（pm2 reload / systemd 零停机参数）  
   `deploy/deploy.sh`
3. 引入连接排空窗口（SSE 友好）

### P2（增强，后续）

1. 更新状态 SSE（前端可见更新进度）
2. 自动灰度（按实例/按比例）
3. 一键回滚控制面板

---

## 9. 推荐操作基线

- 服务器分离：默认使用 `--frontend-artifact`，避免远端低内存构建
- 所有生产更新先走“候选启动 + 健康检查”，禁止直接覆盖重启
- 更新窗口内观察指标：`5xx`、SSE 断连率、启动时长、回滚次数

这套方案可以在你现有实现上平滑演进，不需要推翻现有脚本体系。
