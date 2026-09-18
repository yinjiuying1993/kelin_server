# 刻灵服务端部署

把 `4_server` 以 Docker 镜像部署到一台 Linux 服务器：同一镜像跑 **api / scheduler / worker** 三个进程。  
本文按仓库真实文件编写（`Dockerfile`、`compose.prod.yaml`、`app/core/config.py`、Alembic head `20260908_0019`）。  
**不是阶段验收，也不表示已经上线。** 本机联调仍看 [`ios-integration.md`](ios-integration.md)。

| 项 | 当前值 |
| :--- | :--- |
| 镜像 | `kelin-server:runtime`（Python 3.12 + ffmpeg） |
| 监听 | `0.0.0.0:8000` |
| Alembic head | `20260908_0019` |
| 业务前缀 | `/api/v1/...`（需 Bearer JWT） |
| 探活 | `/health/live`、`/health/ready`（无 JWT、无 Envelope） |
| 部署 Compose | 仓库根目录 `compose.prod.yaml`（自建 Postgres + api/scheduler/worker） |
| 本机 Compose | `compose.yaml`（`postgres-test` 仅本机；**不要**原样拿到服务器） |
| 数据面 | 与本机相同：**业务库自建 Postgres，登录走 Supabase Auth** |

---

## 1. 架构

```text
iOS ── 匿名登录 ──► Supabase Auth ──► JWT (RS256)
iOS ── HTTPS ────► 反代 :443 ──► api :8000 ──► 自建 Postgres
                                              ▲
                                    scheduler / worker
```

Supabase **只验登录 token**。不要把 `DATABASE_URL_API` 指到 Supabase 托管库。`APP_ENV=dev` 时，有效 JWT 的第一次写事务会按 `sub` 补本机同款的占位 `auth.users`。

| 进程 | 入口 | 职责 |
| :--- | :--- | :--- |
| `api` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | HTTP |
| `scheduler` | `python -m app.scheduler` | 入队 state / visit / usage_rollup / notification_plan；默认 60s 一拍 |
| `worker` | `python -m app.worker` | 串门、通知规划、鉴定生成、注销；**不发 APNs**；默认 5s 一拍 |

Chat / 记忆 / 投喂 / Pact / 设置只要 api。明信片落地、鉴定离开 `generating`、注销清库需要 **worker 在跑**。只重启 api 不够。

当前 Storage / TTS 对象在 **进程内存**：重启 api 后旧短链 404；多副本 api 不共享对象。V0 先单副本 api。

---

## 2. 服务器准备

- Linux（Ubuntu 22.04/24.04 即可），公网或内网 IP，能出网访问 Supabase JWKS 与百炼。
- Docker Engine 24+ 与 Compose v2。
- 不要把宿主机 5433 的 `postgres-test` 当生产库；测试库会被 pytest `DROP SCHEMA`。
- 代码放到例如 `/opt/kelin/4_server`（git clone 本仓库，或 rsync 不含 `.env` / `.venv` 的目录）。

```bash
sudo mkdir -p /opt/kelin
sudo chown "$USER:$USER" /opt/kelin
cd /opt/kelin
git clone <kelin_server 仓库 URL> 4_server
cd 4_server
```

安装 Docker（若尚未安装，按官方文档安装 Engine）。当前用户加入 `docker` 组后重新登录。

防火墙只放行反代端口。不要把 Postgres 端口暴露到公网。

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

若暂时不用 TLS、直接打 `8000`（仅内网或临时联调）：

```bash
sudo ufw allow 8000/tcp
```

---

## 3. 数据库（与本机同一拆分）

| 职责 | 本机 | 服务器 |
| :--- | :--- | :--- |
| 业务表 / RLS / outbox | Compose `postgres-test`（`kelin_test`，宿主机 5433） | Compose `postgres`（库名 `kelin`，数据卷 `kelin-pgdata`，**不映射公网**） |
| 登录发 JWT | 同一套 Supabase Auth | **同一套** issuer / audience / JWKS |
| `APP_ENV` | `dev` | `dev`（才会自动补占位 `auth.users`） |

Alembic 会在自建库建 `auth` schema（占位 `auth.users`）、业务表、RLS 与角色 `kelin_api` / `kelin_worker` / `kelin_scheduler`。运行时连接后事务内 `SET LOCAL ROLE`。

不要：

- 把服务器 `DATABASE_URL_API` 指到 Supabase 项目库
- 把本机 `postgres-test` / `kelin_test` 当服务器库（pytest 会 `DROP SCHEMA`）
- 把服务器 `APP_ENV` 设成 `prod`（`prod` **不会**按 JWT 补 `auth.users`，自建库会对不上匿名登录）

`.env` 里库相关项：

```text
APP_ENV=dev
POSTGRES_USER=kelin
POSTGRES_PASSWORD=<强密码>
POSTGRES_DB=kelin
DATABASE_URL_API=postgresql+asyncpg://kelin:<强密码>@postgres:5432/kelin
```

先起库：

```bash
docker compose -f compose.prod.yaml up -d postgres
docker compose -f compose.prod.yaml ps
```

---

## 4. 环境变量

在仓库根复制模板，**只放服务器**，chmod 600，永不提交、永不打进镜像层（`.dockerignore` 已排除 `.env`）。

```bash
cp .env.example .env
chmod 600 .env
```

### 4.1 与本机对齐（`APP_ENV=dev`）

| 变量 | 说明 |
| :--- | :--- |
| `APP_ENV` | 必须 `dev`，与本机一致 |
| `POSTGRES_PASSWORD` | 自建库口令；同时写进 `DATABASE_URL_API` |
| `DATABASE_URL_API` | 主机名必须是 `postgres`（Compose 服务名），不要用 `127.0.0.1` / Supabase URI |
| `SUPABASE_JWT_ISSUER` / `AUDIENCE` / `JWKS_URL` | 与本机 `.env`、iOS 工程 **同一 Supabase 项目** |
| `CURSOR_HMAC_SECRET` | 建议在服务器单独生成；不设则用代码里的 dev 默认值（公网不安全） |
| `DEVICE_TOKEN_KEY` | 建议在服务器单独生成 Fernet 密钥；不设则用 dev 默认值 |

生成密钥（在已构建的镜像里，或本机 Python 3.12）：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

第一条给 `CURSOR_HMAC_SECRET`，第二条给 `DEVICE_TOKEN_KEY`。

### 4.2 百炼（可选）

全部留空则 Chat 走 stub。要真模型时至少配置 `BAILIAN_API_KEY` + `BAILIAN_CHAT_MODEL` + `BAILIAN_EXTRACT_MODEL`。其余 ASR/TTS/Vision/Safety/Search 按需。不要把真实 key 或厂商模型 ID 写进 git。

### 4.3 示例（占位，不要照抄到生产）

```text
APP_ENV=dev
LOG_LEVEL=INFO
POSTGRES_USER=kelin
POSTGRES_PASSWORD=<强密码>
POSTGRES_DB=kelin
DATABASE_URL_API=postgresql+asyncpg://kelin:<强密码>@postgres:5432/kelin
SUPABASE_JWT_ISSUER=https://<project>.supabase.co/auth/v1
SUPABASE_JWT_AUDIENCE=authenticated
SUPABASE_JWKS_URL=https://<project>.supabase.co/auth/v1/.well-known/jwks.json
CURSOR_HMAC_SECRET=<cursor-secret>
DEVICE_TOKEN_KEY=<fernet-key>
```

JWT 三项从本机 `.env` 原样拷贝（只拷贝这三项和百炼配置，不要拷 `DATABASE_URL_API`）。  
`SUPABASE_ANON_KEY` 只给 iOS，不要配进服务端。

`APP_ENV=dev` 会注册 `/docs` 和 `/api/v1/debug/*`。公网不要裸奔 8000：用反代，或把 Compose ports 改成 `127.0.0.1:8000:8000`。

---

## 5. 构建、迁移、启动

全部在仓库根目录、已写好 `.env` 后执行。

```bash
docker compose -f compose.prod.yaml build
docker compose -f compose.prod.yaml up -d postgres
```

先升级 schema（一次性；**禁止** `downgrade` / `DROP` / `TRUNCATE`）。Postgres healthy 之后：

```bash
docker compose -f compose.prod.yaml run --rm --no-deps api \
  uv run --frozen --no-dev alembic current
docker compose -f compose.prod.yaml run --rm --no-deps api \
  uv run --frozen --no-dev alembic upgrade head
docker compose -f compose.prod.yaml run --rm --no-deps api \
  uv run --frozen --no-dev alembic current
```

第二次 `current` 应为 `20260908_0019 (head)`。漂移或失败时 **停止部署**，不要手改生产 schema。

启动三个进程（会一并拉起自建 Postgres）：

```bash
docker compose -f compose.prod.yaml up -d
docker compose -f compose.prod.yaml ps
docker compose -f compose.prod.yaml logs -f --tail=100
```

启动日志里的 `app_boot` 只打印配置 **是否 set/unset**，不含 Secret 值。若看到 JWT 或数据库口令，视为事故。

---

## 6. 验收命令

在服务器或能访问该 IP 的机器上：

```bash
curl -sS http://127.0.0.1:8000/health/live
# {"status":"live"}

curl -sS -o /tmp/ready.json -w "%{http_code}\n" http://127.0.0.1:8000/health/ready
# 200 且 JSON status=ready、checks.database=ok
# 503 表示配了 URL 但库连不上

curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/v1/bootstrap
# 401

curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/docs
# APP_ENV=dev → 200
```

`/health/ready` 在 `DATABASE_URL_API` 未设或库连不上时是 503。

iOS 只改 `API_BASE_URL` 为 `https://<域名>` 或 `http://<服务器IP>:8000`（真机 ATS 例外）。`SUPABASE_URL` / anon key **不要改**，继续本机那套匿名登录。

---

## 7. TLS 反代（建议）

容器继续听 8000，宿主机用 Caddy 或 Nginx 终止 TLS。api 端口改为只绑回环更安全，把 `compose.prod.yaml` 里的 ports 改成 `"127.0.0.1:8000:8000"`。

Caddy 示例：

```text
api.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Nginx 关键段：

```nginx
server {
    listen 443 ssl http2;
    server_name api.example.com;
    client_max_body_size 6m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Request-ID $http_x_request_id;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

语音 Chat 客户端超时约 48s，反代 `proxy_read_timeout` 不要低于 60s。

---

## 8. 更新与回滚

```bash
cd /opt/kelin/4_server
git fetch
git checkout <已评审的提交>
docker compose -f compose.prod.yaml build
docker compose -f compose.prod.yaml run --rm --no-deps api \
  uv run --frozen --no-dev alembic upgrade head
docker compose -f compose.prod.yaml up -d
```

- 回滚 **优先换回旧镜像/旧提交** 再 `up -d`。
- 破坏性 DDL 不得与首次切流同一批删旧列。
- 不要对生产执行 `alembic downgrade`。
- 改 `.env` 后必须重建/重启 **api + scheduler + worker**。

查看当前 revision：

```bash
docker compose -f compose.prod.yaml run --rm --no-deps api \
  uv run --frozen --no-dev alembic current
```

---

## 9. 常见失败

| 现象 | 处理 |
| :--- | :--- |
| `/health/ready` 503 `database: unavailable` | `DATABASE_URL_API` 主机名不是 `postgres`、密码与 `POSTGRES_PASSWORD` 不一致、或 Postgres 未 healthy |
| 业务 401，health 正常 | JWKS/issuer/audience 与 iOS 不是同一 Supabase 项目；token 不是 RS256 |
| 写接口缺用户行 | 确认 `APP_ENV=dev`；`prod` 不会自动插 `auth.users` |
| Chat 一直 stub | 未配 `BAILIAN_API_KEY` 与 chat/extract 模型 |
| 鉴定一直 `generating` / 注销不落地 | worker 没起来 |
| TTS 播放 404 | api 重启过，内存对象没了 |
| 误用 `compose.yaml` 的 `postgres-test` | 立刻停掉；那是可丢弃测试库 |

---

## 10. 不要做

- 不把 `.env`、dump、真实 JWT、百炼 key 提交 git 或贴进聊天。
- 不在服务器跑 `pytest`，不把 `DATABASE_URL_API` 指到本机 `kelin_test` 或 Supabase 托管库。
- 不手改 schema 掩盖 Alembic 漂移。
- 不把 Debug / `/docs` 裸奔到公网（`APP_ENV=dev` 会注册它们）。
- 不假设 APNs 已投递、Storage 已上对象存储；当前 worker 不发推送，见闻/TTS 仍是进程内对象。
- 不一次拉起多副本 api（内存 Storage 会裂脑）。
