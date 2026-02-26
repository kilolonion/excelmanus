# ExcelManus 运维手册

## 1. 架构总览

```
                           ┌─────────────────────────────────────┐
                           │         用户浏览器                    │
                           └──────────────┬──────────────────────┘
                                          │ https://<YOUR_DOMAIN>
                                          ▼
                    ┌─────────────────────────────────────────────┐
                    │    前端服务器 <FRONTEND_IP>（国内 · 阿里云）     │
                    │                                             │
                    │  Nginx (SSL 终止)                            │
                    │    ├─ /api/*  ──▶  <BACKEND_IP>:8000      │
                    │    └─ /*      ──▶  127.0.0.1:3000           │
                    │                                             │
                    │  PM2 进程                                    │
                    │    └─ excelmanus-web  (Next.js, port 3000)  │
                    └──────────────┬──────────────────────────────┘
                                   │ proxy /api/*
                                   ▼
                    ┌─────────────────────────────────────────────┐
                    │    后端服务器 <BACKEND_IP>（海外 · 阿里云）   │
                    │                                             │
                    │  Nginx (备用 SSL, Let's Encrypt)             │
                    │    ├─ /api/*  ──▶  127.0.0.1:8000           │
                    │    └─ /*      ──▶  <FRONTEND_IP>:3000        │
                    │                                             │
                    │  PM2 进程                                    │
                    │    └─ excelmanus-api (Python/uvicorn, 8000) │
                    └─────────────────────────────────────────────┘
```

**为什么这样拆分？**

- 前端服务器在国内，用户访问速度快，承担 DNS 入口 + SSL 终止 + 静态资源
- 后端服务器在海外，可直接调用 OpenAI/Claude 等 API，无需代理
- 后端服务器也配了 Nginx + SSL（Let's Encrypt），如果 DNS 切过去可独立运行

---

## 2. 服务器清单

| 角色 | IP | 系统 | 关键路径 |
|------|----|------|----------|
| 前端 | `<FRONTEND_IP>` | Alibaba Cloud Linux | `/www/wwwroot/excelmanus/web` |
| 后端 | `<BACKEND_IP>` | Alibaba Cloud Linux | `/www/wwwroot/excelmanus` |

**SSH 登录**（两台共用同一密钥）：

```bash
ssh -i <SSH_KEY_FILE> root@<FRONTEND_IP>   # 前端
ssh -i <SSH_KEY_FILE> root@<BACKEND_IP>  # 后端
```

---

## 3. 运行环境

### 3.1 前端服务器 (<FRONTEND_IP>)

| 组件 | 版本 | 路径 |
|------|------|------|
| Node.js | v22.22.0 | `/www/server/nodejs/v22.22.0/bin` |
| PM2 | 6.x | 同上 |
| Nginx | 系统自带 | 配置: `/etc/nginx/conf.d/excelmanus.conf` |
| SSL 证书 | 宝塔面板管理 | `/www/server/panel/vhost/cert/<YOUR_DOMAIN>/` |

**注意**: 该服务器的 PM2 需要手动加 PATH：

```bash
export PATH=/www/server/nodejs/v22.22.0/bin:$PATH
```

### 3.2 后端服务器 (<BACKEND_IP>)

| 组件 | 版本 | 路径 |
|------|------|------|
| Python | 3.11.9 | `/usr/local/bin/python3.11`（从源码编译） |
| Node.js | v22.22.0 | `/usr/bin/node`（nodesource RPM） |
| PM2 | 6.0.14 | `/usr/bin/pm2` |
| Nginx | 1.20.1 | 配置: `/etc/nginx/conf.d/excelmanus.conf` |
| SSL 证书 | Let's Encrypt (certbot) | `/etc/letsencrypt/live/<YOUR_DOMAIN>/` |
| venv | Python 3.11 | `/www/wwwroot/excelmanus/venv` |

---

## 4. 防火墙端口

### 前端服务器

```
20/tcp 21/tcp 22/tcp 80/tcp 443/tcp 3000/tcp 8888/tcp 39000-40000/tcp
```

- `3000/tcp` 必须开放，供后端服务器的 Nginx 回源前端

### 后端服务器

```
20/tcp 21/tcp 22/tcp 80/tcp 443/tcp 8000/tcp 8888/tcp 15996/tcp 39000-40000/tcp
```

- `8000/tcp` 必须开放，供前端服务器的 Nginx 转发 API 请求

**管理命令**：

```bash
firewall-cmd --list-ports                         # 查看
firewall-cmd --permanent --add-port=PORT/tcp      # 添加
firewall-cmd --permanent --remove-port=PORT/tcp   # 删除
firewall-cmd --reload                             # 生效
```

---

## 5. 日常运维

### 5.1 一键部署

项目根目录的 `deploy.sh` 支持前后端分离部署：

```bash
# 完整部署（后端 + 前端）
./deploy.sh

# 只更新后端
./deploy.sh --backend-only

# 只更新前端
./deploy.sh --frontend-only

# 本地构建并打包前端制品（推荐）
cd /path/to/excelmanus/web
npm run build
mkdir -p ../web-dist
tar -czf ../web-dist/frontend-standalone.tar.gz .next/standalone .next/static public

# 跳过前端构建，只重启
./deploy.sh --frontend-only --skip-build

# 使用本地/CI 构建好的前端制品（推荐低内存服务器）
./deploy.sh --frontend-only --frontend-artifact ./web-dist/frontend-standalone.tar.gz

# 远端冷构建（仅排障，风险高）
./deploy.sh --frontend-only --cold-build

# 从本地 rsync 同步（不走 GitHub）
./deploy.sh --from-local
```

### 5.2 手动操作

**后端 (<BACKEND_IP>)**：

```bash
# 查看状态
pm2 list

# 重启后端
pm2 restart excelmanus-api

# 查看日志
pm2 logs excelmanus-api --lines 50 --nostream

# 实时日志
pm2 logs excelmanus-api

# 手动更新代码
cd /www/wwwroot/excelmanus
git fetch https://github.com/kilolonion/excelmanus main
git reset --hard FETCH_HEAD
source venv/bin/activate
pip install -e . -q
pm2 restart excelmanus-api
```

**前端 (<FRONTEND_IP>)**：

```bash
export PATH=/www/server/nodejs/v22.22.0/bin:$PATH

# 查看状态
pm2 list

# 重启前端（不重新构建）
pm2 restart excelmanus-web

# 重新构建并重启
cd /www/wwwroot/excelmanus/web
npm install --production=false
NEXT_PUBLIC_BACKEND_ORIGIN= BACKEND_INTERNAL_URL=http://<BACKEND_IP>:8000 npm run build
pm2 restart excelmanus-web

# 若默认构建失败，可尝试 webpack 兜底
npm run build:webpack
pm2 restart excelmanus-web

# 查看日志
pm2 logs excelmanus-web --lines 50 --nostream
```

> 低内存机器（1~2G）应优先使用 `--frontend-artifact` 制品化发布，避免现场冷编译触发 OOM。
> 跨区传输推荐使用支持断点续传的 rsync（脚本已内置 `--partial --append-verify`）。

### 5.3 健康检查

```bash
# 通过域名（走完整链路）
curl https://<YOUR_DOMAIN>/api/v1/health

# 直连后端
curl http://<BACKEND_IP>:8000/api/v1/health

# 检查前端可达性
curl -o /dev/null -w "%{http_code}" https://<YOUR_DOMAIN>/login
```

---

## 6. Nginx 配置

### 6.1 前端服务器 `/etc/nginx/conf.d/excelmanus.conf`

```nginx
# HTTP -> HTTPS redirect
server {
    listen 80;
    server_name <YOUR_DOMAIN> www.<YOUR_DOMAIN>;
    return 301 https://$host$request_uri;
}

# HTTPS
server {
    listen 443 ssl http2;
    server_name <YOUR_DOMAIN> www.<YOUR_DOMAIN>;

    ssl_certificate     /www/server/panel/vhost/cert/<YOUR_DOMAIN>/fullchain.pem;
    ssl_certificate_key /www/server/panel/vhost/cert/<YOUR_DOMAIN>/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    client_max_body_size 100m;

    # SSE 流式接口（不使用 Connection: upgrade，否则 SSE 会失败）
    location /api/v1/chat/stream {
        proxy_pass http://<BACKEND_IP>:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        chunked_transfer_encoding on;
    }

    # 其他 API 请求转发到后端服务器
    location /api/ {
        proxy_pass http://<BACKEND_IP>:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }

    # 前端本地 Next.js
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

### 6.2 后端服务器 `/etc/nginx/conf.d/excelmanus.conf`

此配置由 certbot 自动管理。当 DNS 指向此服务器时，可独立处理所有流量：

- `/api/*` → 本地后端 (`127.0.0.1:8000`)
- `/*` → 回源到前端服务器 (`<FRONTEND_IP>:3000`)

**Nginx 管理命令**：

```bash
nginx -t             # 检查配置语法
nginx -s reload      # 平滑重载
systemctl restart nginx  # 完全重启
```

---

## 7. 环境变量 (.env)

后端的 `.env` 位于 `/www/wwwroot/excelmanus/.env`，关键配置：

| 变量 | 用途 | 备注 |
|------|------|------|
| `EXCELMANUS_API_KEY` | 主模型 API Key | |
| `EXCELMANUS_BASE_URL` | 主模型端点 | |
| `EXCELMANUS_MODEL` | 主模型名称 | |
| `EXCELMANUS_AUX_*` | 辅助小模型（路由/子代理） | |
| `EXCELMANUS_VLM_*` | 视觉模型（图片提取） | |
| `EXCELMANUS_EMBEDDING_*` | Embedding 模型 | |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | CORS 白名单 | 必须包含前端域名 |
| `EXCELMANUS_AUTH_ENABLED` | 是否启用认证 | `true` |
| `EXCELMANUS_JWT_SECRET` | JWT 签名密钥 | 生产环境必须固定 |
| `EXCELMANUS_GITHUB_*` | GitHub OAuth | |
| `EXCELMANUS_GOOGLE_*` | Google OAuth | |
| `EXCELMANUS_OAUTH_PROXY` | OAuth 代理 | 国内服务器访问 Google 需要 |

---

## 8. SSL 证书续期

### 前端服务器

SSL 证书由宝塔面板管理，通过面板界面续期。

### 后端服务器

SSL 证书由 Let's Encrypt (certbot) 管理，已配置自动续期：

```bash
# 查看证书信息
certbot certificates

# 手动续期测试
certbot renew --dry-run

# 强制续期
certbot renew
```

证书到期日: **2026-05-25**

---

## 9. DNS 切换指南

当前 DNS 指向前端服务器 (`<FRONTEND_IP>`)。如需切换到后端服务器独立运行：

1. 将 `<YOUR_DOMAIN>` 和 `www.<YOUR_DOMAIN>` 的 A 记录改为 `<BACKEND_IP>`
2. 后端服务器的 Nginx 已配好 SSL + 双向代理，无需额外操作
3. 切换后验证：`curl https://<YOUR_DOMAIN>/api/v1/health`

切回时将 A 记录改回 `<FRONTEND_IP>` 即可。

---

## 10. 故障排查

### 前端 502

```bash
# 1. 检查前端进程
ssh -i <SSH_KEY_FILE> root@<FRONTEND_IP>
export PATH=/www/server/nodejs/v22.22.0/bin:$PATH
pm2 list    # excelmanus-web 应为 online

# 2. 检查后端可达性（从前端服务器）
curl http://<BACKEND_IP>:8000/api/v1/health

# 3. 如果后端不可达，检查后端
ssh -i <SSH_KEY_FILE> root@<BACKEND_IP>
pm2 list    # excelmanus-api 应为 online
pm2 logs excelmanus-api --lines 30 --nostream
```

### 后端 500

```bash
ssh -i <SSH_KEY_FILE> root@<BACKEND_IP>
pm2 logs excelmanus-api --lines 50 --nostream
# 检查 .env 配置
cat /www/wwwroot/excelmanus/.env
```

### Nginx 配置错误

```bash
nginx -t    # 语法检查
# 查看错误日志
tail -50 /var/log/nginx/error.log
```

### 内存不足

```bash
free -h
pm2 list    # 检查进程内存
# 后端 API 通常占用 ~200MB
```

---

## 11. 从零搭建后端服务器

如果需要在新服务器上重建后端环境，按以下步骤操作。

> **注意**: 下方以 Python 3.11 为例，实际可使用任何 `>=3.10` 的版本（Docker 部署默认使用 3.12）。

```bash
# 1. 安装编译依赖
yum groupinstall -y "Development Tools"
yum install -y openssl-devel bzip2-devel libffi-devel zlib-devel readline-devel sqlite-devel

# 2. 编译安装 Python 3.11
cd /tmp
curl -O https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz
tar xzf Python-3.11.9.tgz && cd Python-3.11.9
./configure --enable-optimizations
make -j$(nproc)       # 大约 10-20 分钟
make altinstall
ln -sf /usr/local/bin/python3.11 /usr/local/bin/python3
ln -sf /usr/local/bin/pip3.11 /usr/local/bin/pip3

# 3. 安装 Node.js 22
curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
yum install -y nodejs
npm install -g pm2

# 4. 克隆代码
mkdir -p /www/wwwroot
git clone https://github.com/kilolonion/excelmanus.git /www/wwwroot/excelmanus
cd /www/wwwroot/excelmanus

# 5. 创建 venv 并安装依赖
python3.11 -m venv venv
source venv/bin/activate
pip install -e .
pip install httpx[socks] psycopg2-binary

# 6. 配置 .env（从旧服务器复制并修改）
# 7. 配置 mcp.json

# 8. 启动后端
pm2 start "venv/bin/python -c \"import uvicorn; uvicorn.run('excelmanus.api:app', host='0.0.0.0', port=8000, log_level='info')\"" --name excelmanus-api --cwd /www/wwwroot/excelmanus
pm2 save
pm2 startup

# 9. 安装 Nginx + SSL
yum install -y nginx certbot python3-certbot-nginx
# 写入 Nginx 配置（见第 6 节）
systemctl start nginx && systemctl enable nginx
certbot --nginx -d <YOUR_DOMAIN> -d www.<YOUR_DOMAIN> --non-interactive --agree-tos --email YOUR_EMAIL

# 10. 开放防火墙
firewall-cmd --permanent --add-port=8000/tcp
firewall-cmd --permanent --add-port=80/tcp
firewall-cmd --permanent --add-port=443/tcp
firewall-cmd --reload
```

---

## 12. 文件清单

```
项目根目录/
├── deploy.sh              # 一键部署脚本（前后端分离）
├── <SSH_KEY_FILE>      # SSH 私钥（两台服务器共用）
├── .env                   # 本地开发环境变量
├── mcp.json               # MCP 服务器配置
├── start.sh               # 本地开发启动脚本
└── docs/
    └── ops-manual.md      # 本手册
```
