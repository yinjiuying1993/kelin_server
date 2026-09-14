# 刻灵 P01–P07 联调说明

面向 iOS ↔ 本机 FastAPI，覆盖 **P0 能力门禁里本阶段相关行** 以及 **P01→P07**。  
当前 **全量契约锁、进程、SessionStore** 以 [`ios-integration.md`](ios-integration.md) 为准（OpenAPI SHA `fed40c13…`，Alembic `20260908_0019`）。本文 SHA 是加后续路由 **之前** 的快照，只服务孵化这一截。  
Chat 孵化句在本机可能已是百炼（`generation_source=provider`）；fixture 样例仍可能是 `stub`，不得把 stub 显示成百炼。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机 `3_ios` 工程若尚未接线，可先用本文契约 + curl / 抓包客户端。  
P08 起见总说明分册表。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `fc79081f5b0bfb3e15be0994324bdbacdc930dd1f27e7c23487432895b9565b5` |
| Alembic head | `20260908_0007` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/`（与 SHA 同锁） |

S04 细节见第 9–12 节。若只联孵化：第 2 节环境 + 第 8–12 节接口。

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| P01 health | **已完成**。`/health/live` 与 `/health/ready` 均 200 | `app/health.py` |
| P02 Schema | **已完成**。head `20260908_0007` | `app/db/migrations/` |
| P03 JWT/Envelope | **已完成**。RS256 + JWKS；无 token 401 | `app/core/security.py`、`app/core/envelope.py` |
| P04 OpenAPI/fixture | **已完成**。SHA 与 `fixtures/` 锁定 | `app/contracts/` |
| P05 `POST /spirits` | **已完成**。幂等创建、蛋温、邀请码 | `app/api/v1/spirits.py` |
| P06 `GET /bootstrap` | **已完成**。settle + RR 聚合 | `app/api/v1/bootstrap.py` |
| P07 `POST /chat` Stub | **已完成**。成对结算、`generation_source=stub` | `app/api/v1/chat.py` |
| P07 `POST /onboarding/complete` | **已完成**。step=5 且 5 对才孵化 | `app/api/v1/onboarding.py` |
| 本机 `auth.users` | **dev/test 自动补行**：已验证 JWT 第一次写事务时插入 `sub`（不是在 Supabase 再注册） | `app/db/session.py` |

进程：`kelin-postgres-test` + `kelin-server-api`（`0.0.0.0:8000`）。无 worker / 百炼 / APNs，P01–P07 不需要。

### 服务端自检

```text
GET http://192.168.100.212:8000/health/live     → 200 {"status":"live"}
GET http://192.168.100.212:8000/health/ready    → 200 {"status":"ready",...}
GET http://192.168.100.212:8000/api/v1/bootstrap （无 JWT）→ 401 UNAUTHENTICATED
GET http://192.168.100.212:8000/api/v1/bootstrap （带 RS256 JWT）→ 200，无精灵时 spirit/room 为 null
```

iOS 匿名登录后直接打 API 即可。本机库会在第一次 `claimed_transaction`（bootstrap settle / 创建精灵）用 JWT `sub` 补上 `auth.users`，**不必再把 sub 发给服务端手工插入**。`prod` 不走这条（那边应已是 Supabase 的 `auth.users`）。

改 Python 后需 `docker restart kelin-server-api`。Windows 入站规则 `kelin-api-8000` 已添加。

---

## iOS 需要改什么

本机工作区 `3_ios/` **目前没有 App 源码**（仅仓库治理文件）。下列按规格 + 当前已接线服务端整理，给 **192.168.100.205** 上的 iOS 工程改。服务端 **不必**再收 `sub`、不必手工插 `auth.users`。

### 不要做

- 不要把 `user_id` 放进任何业务 body  
- 不要直连 Postgres / 百炼；业务只打 `API_BASE_URL`  
- 不要把 publishable key、JWT 提交 git 或打进日志  
- 不要本地数 5 句就算孵化；不要本地写 `hatched_at`  
- 不要在 S04 申请麦克风；不要把 `generation_source=stub` 显示成百炼  
- 不要打 Pact、社交、通知等 P14+ 接口；P08–P10 见 [`p08-p10-integration.md`](p08-p10-integration.md)；记忆 CRUD 见 [`p11-integration.md`](p11-integration.md)；Feed/见闻见 [`p12-integration.md`](p12-integration.md)；成长/配额见 [`p13-integration.md`](p13-integration.md)  
- 不要在重试时重新 `UUID()`  
- 不要等服务端「开户」接口——没有这种 API

### 必须改：工程与配置（P01 / P03）

| 项 | 改什么 |
| :--- | :--- |
| `API_BASE_URL` | Debug 联调：`http://192.168.100.212:8000`。请求路径为 `{API_BASE_URL}/api/v1/...`，health 为 `{API_BASE_URL}/health/live` |
| `SUPABASE_URL` | `https://rnvfnvowjavtojvzgvtr.supabase.co` |
| `SUPABASE_ANON_KEY` | Dashboard 的 **publishable / anon**，只放本机 `.xcconfig`（gitignore） |
| `APP_ENV` | `dev` |
| ATS | 真机访问 IP 必须允许明文 HTTP。`Info.plist` 为 `192.168.100.212` 加 `NSExceptionDomains`（`NSExceptionAllowsInsecureHTTPLoads=true`），或 Debug 临时 `NSAllowsArbitraryLoads`（不要进 Release） |
| Auth | `signInAnonymously()`，无登录页。恢复 Keychain 会话后再打 bootstrap |
| Token | 每个 `/api/v1` 请求：`Authorization: Bearer <session.accessToken>`。切换 RS256 后 **退出再登录**；`alg` 必须是 `RS256`，否则一直 401 |
| Envelope | 解码 `ok` / `data` / `error.code` / `error.retryable` / `request_id` / `server_time`。用 `code` 映射 UI，不要用英文 `message` |
| Header | 建议 `X-Request-ID` 为 UUID；`Content-Type: application/json` |

先验证：真机浏览器或 App 打 `http://192.168.100.212:8000/health/live` → `{"status":"live"}`。通了再联 Auth。

### 必须改：DTO 对齐 SHA（P04）

锁定 SHA：`fc79081f5b0bfb3e15be0994324bdbacdc930dd1f27e7c23487432895b9565b5`。  
DTO 按 `4_server/fixtures/` 解码，未知字段/缺字段要失败可观测。fixture 里的 version/时间是样例，**live 以响应为准**（第一轮 chat 的 `spirit.version` 是 **2** 不是 1）。

`SessionStore` 用 bootstrap **整包替换**；mutation 只合入服务端 `patch`，客户端不算成长/配额/孵化。

### 必须改：AppPhase（P03 + P07）

```text
launching → restoringSession / signingInAnonymously
  → requestingBootstrap
  → spirit==null        → needsAgreement（S02）
  → step<5 未孵化       → needsEggSelection 或 needsFirstConversation(step)
  → hatched_at != nil   → ready（S01；四态 UI 属 P08）
```

- S02：AI 声明 **不可预勾**；未勾主按钮禁用。协议页不打 API。  
- `needsFirstConversation(step)` 的 step = bootstrap/chat patch 的 `onboarding.step`，不要用本地发送计数。

### 必须改：S03 创建精灵（P05）

`POST /api/v1/spirits`

- 首次进入 S03 生成一次 `client_id`，写入 LocalStore；失败、连点、杀进程 **复用**  
- `egg`：暖 `warm` / 冷 `cold` / 不定 `wild`；不要展示属性条、稀有度  
- `name` 先可用「未名」（1–20）  
- consents：`ai_disclosure.explicitly_accepted=true`（仅勾选后）；`data_notice`/`user_terms` 只要 `displayed=true`  
- 邀请码、closeness 等只展示服务端返回值，不要本地生成  
- `409 CONFLICT`：已有精灵却换了 `client_id` → 拉 bootstrap，不要再建一只

### 必须改：S04 五句 + complete（P07）

`POST /api/v1/chat` 每一句：

- 必带 `"onboarding": true`、`"source": "text"`  
- 每句一个 `client_message_id`；超时/失败 **原样重试**；新句子新 UUID  
- 单请求在途，禁止并行两句  
- `context`：`timezone`、`local_hour`（0–23）、`weather`、`city`（可 null）。`local_hour` 不算配额  
- 进度只在 **200 且收到 spirit_message** 后用 `patch.onboarding.step`  
- `patch.room` **恒为 null**，不要当丢字段；房间用 bootstrap 或 complete 的 patch  
- UI 标明 Stub（`generation_source=stub`）  
- 杀进程：只信 `GET /bootstrap` 的 step；未完成句用原 `client_message_id`

`POST /api/v1/onboarding/complete`：

- 另存一个 **complete 专用** `client_id`（不要复用 spirits 的）  
- `expected_spirit_version` = **当时** `spirit.version`（五轮结束后是 **6**，不是创建时的 1）  
- 动效 0.8–1.2s 不能代替这次 200；`hatched_at` 有值再进 ready  
- 同 ID 重放应得到同一 `hatched_at`

### 错误处理（iOS 映射）

| `error.code` | iOS |
| :--- | :--- |
| `UNAUTHENTICATED` | 重新匿名登录；确认 RS256 |
| `INVALID_INPUT` | 修 body（常见：漏了 `onboarding`） |
| `IDEMPOTENCY_CONFLICT` | 不要改已发出的稳定 ID |
| `IDEMPOTENCY_IN_PROGRESS` | `retryable=true`，同一 ID 稍后重试 |
| `ONBOARDING_INCOMPLETE` | 留在 S04 |
| `ONBOARDING_ALREADY_COMPLETED` | 拉 bootstrap 进房间 |
| `CONFLICT` | 拉 bootstrap，用当前 version / 原 client_id |
| `MODEL_UNAVAILABLE` | 同一 `client_message_id` 重试 |

### 建议改动顺序

1. xcconfig + ATS + health/live  
2. 匿名 Auth + Bearer + bootstrap（无精灵 200）  
3. S02 勾选 → S03 `POST /spirits`  
4. S04 五轮 chat → complete → bootstrap 进房间  
5. 杀进程从 `onboarding.step` 恢复  

---

## 1. P0 能力：现在能联什么

P0 矩阵里，**本机 HTTP 已接线且可供 iOS 真实请求**的只有下面四条（外加 health）：

| P0 能力 | 阶段 | 本机状态 | 联调注意 |
| :--- | :--- | :--- | :--- |
| `GET /health/live`、`/health/ready` | P01 | **可联** | 均 200；无 JWT、无 Envelope |
| Envelope + JWT/JWKS | P03 | **可联** | 只验 RS256；无 JWT → 401 |
| `POST /api/v1/spirits` | P05/P07 | **可联** | 带 JWT 即可，本机自动补 `auth.users` |
| `GET /api/v1/bootstrap` | P06 | **可联** | 含短写 settle + 只读聚合 |
| `POST /api/v1/chat`（onboarding Stub） | P07 | **可联** | S04；不是 P10 真模型 |
| `POST /api/v1/onboarding/complete` | P07 | **可联** | step=5 且 5 完整成对 |

**P01–P07 仍不要打的接口**：`PATCH /spirit`、记忆 CRUD、Feed/Storage/审核、Pact、社交、通知、Report 生成、注销等。  
`GET /messages`、`POST /extract`、孵化后 `onboarding=false` 的 Chat 已接线，见 [`p08-p10-integration.md`](p08-p10-integration.md)。  
P0 写「POST /chat 真实联网需真模型」——P01–P07 的 **Stub 只用于 S04 孵化**。

---

## 2. 当前环境

| 角色 | 地址 |
| :--- | :--- |
| 服务端电脑 | `192.168.100.212` |
| iOS 设备 | `192.168.100.205` |
| **API Base URL** | **`http://192.168.100.212:8000`** |
| 健康检查 | `GET http://192.168.100.212:8000/health/live` |

容器 `kelin-server-api` 监听 `0.0.0.0:8000`。联调库为 compose `postgres-test`（宿主机 `5433`）。**不要**在联调期间对该库跑会 `DROP SCHEMA` 的全量 pytest。

### iOS 配置

| Key | 值 |
| :--- | :--- |
| `API_BASE_URL` | `http://192.168.100.212:8000` |
| `SUPABASE_URL` | `https://rnvfnvowjavtojvzgvtr.supabase.co` |
| `SUPABASE_ANON_KEY` | Dashboard 的 publishable/anon（只放 iOS，勿提交 git，勿给服务端） |
| `APP_ENV` | `dev` |

ATS 须允许该 IP **明文 HTTP**。服务端入站 **TCP 8000** 规则名 `kelin-api-8000`（已加；若重装系统再执行）：

```powershell
netsh advfirewall firewall add rule name="kelin-api-8000" dir=in action=allow protocol=TCP localport=8000 profile=any
```

### 服务端 JWT（已配，无需 iOS 再抄）

```text
SUPABASE_JWT_ISSUER=https://rnvfnvowjavtojvzgvtr.supabase.co/auth/v1
SUPABASE_JWT_AUDIENCE=authenticated
SUPABASE_JWKS_URL=https://rnvfnvowjavtojvzgvtr.supabase.co/auth/v1/.well-known/jwks.json
```

Access token：**`alg=RS256`**，`kid=fdf70206-21fb-4d68-bfd7-5295525b1586`。  
JWKS 同时列出 ES256 公钥是正常的；ES256 token 会 401。切换密钥后请重新登录。  
不要把完整 JWT 贴聊天。

匿名登录发生在 **Supabase Auth**（自动开户）。本机 Postgres 的 `auth.users` 只是 FK 占位：`APP_ENV=dev|test` 时，带有效 JWT 的第一次写事务会自动 `INSERT ... ON CONFLICT DO NOTHING`，无需再发 `sub`。

---

## 3. 分阶段：服务端事实 ↔ iOS 联调动作

### P01 工程基座

| 服务端已有 | iOS 要对齐 |
| :--- | :--- |
| FastAPI + `/health/live`、`/health/ready`；dev 可开 `/docs`、`/openapi.json` | `API_BASE_URL`、ATS、无业务页也可先打 live |

`GET /health/live` → `{"status":"live"}`。  
`GET /health/ready` → `{"status":"ready","checks":{"config":"ok","database":"ok","migration":"not_applicable"}}`。  
二者均无 JWT、无 Envelope。

### P02 Schema

无独立 HTTP。事实：Alembic `20260908_0007`（身份/精灵/聊天/社交/RLS 等表已在本库）。  
iOS **禁止**直连业务表；只打 FastAPI。

### P03 身份与 Envelope

| 规则 | 说明 |
| :--- | :--- |
| 鉴权 | `Authorization: Bearer <access_token>`；只认 RS256 + JWKS；`role` 必须 `authenticated` |
| 身份 | 只来自 JWT `sub`，body 禁止 `user_id` |
| 登录 | iOS 匿名 Auth（`signInAnonymously`），无登录页 |
| Envelope | 见第 4 节 |
| 日志 | 不含 JWT、消息正文 |

无 token 或坏 token：所有 `/api/v1/*` → `401 UNAUTHENTICATED`。

### P04 契约 Harness

iOS 解码应对齐同一 SHA 与 `4_server/fixtures/`：

- `GET_api_v1_bootstrap/{success,business_error,missing_required_field}.json`
- `POST_api_v1_spirits/...`
- `POST_api_v1_chat/...`
- `POST_api_v1_onboarding_complete/...`
- `envelope/...`

fixture 里的 UUID/时间/`snapshot_version` 是**样例**，live 以响应为准（例如第一轮 chat 的 version 是 **2** 不是 fixture 的 1）。

SwiftData / SessionStore：以 bootstrap 原子替换；`snapshot_version` 合并。本阶段无分页消息接口。

### P05 创建精灵（S03 数据面）

`POST /api/v1/spirits` 已接线。客户端不生成邀请码、不写人格初值。

蛋温 → 服务端初值（只读展示，S03 不要显示属性/稀有度）：

| egg | UI | closeness | curiosity | sharpness | nocturnal | stubborn |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| `warm` | 暖 | 65 | 55 | 35 | 40 | 40 |
| `cold` | 冷 | 35 | 45 | 70 | 60 | 55 |
| `wild` | 不定 | 50 | 50 | 50 | 50 | 50 |

创建成功：`version=1`，`onboarding_step=0`，`hatched_at=null`，`invite_code` 为 8 位（字符集 `A-HJ-NP-Z2-9`）。  
`client_id` 稳定幂等；已有精灵换 ID → `409 CONFLICT`。

### P06 Bootstrap

`GET /api/v1/bootstrap`：先短写 settle（72h lost / 18h away|study 等，**不改 `last_interact_at`**），再 RR 只读聚合。  
无精灵：`spirit` 与 `room` **必须同为 null**，`snapshot_version=0`，`onboarding.required=true`，`step=0`。  
有精灵：二者都有值，`snapshot_version == spirit.version`。  
`latest_memories` / `unread_postcards` / `active_pact`：V0 本阶段为空/null。  
`report.status` 孵化前后多为 `"locked"`；S04 后 `completed_dialogue_rounds` 仍为 **0**。  
`feature_flags`：`remote_search`、`image_feed`（本阶段不必做对应功能）。

房间完整四态 UI 是 **P08**；bootstrap 里可能已有 `spirit.status`，S04 联调不要当 P08 验收。

### P07 孵化全链路（S02–S04）

| 页面 | 谁做 | 接口 |
| :--- | :--- | :--- |
| S02 协议 | iOS；AI 声明不可预勾 | 无 API |
| S03 选蛋 | iOS + `POST /spirits` | P05 |
| S04 五句文字 | iOS + `POST /chat` `onboarding=true` | 无麦克风 |
| 孵化完成 | `POST /onboarding/complete` 后 bootstrap | 动效 0.8–1.2s 不能代替 complete |

---

## 4. 统一 HTTP

```http
Authorization: Bearer <access_token>
Content-Type: application/json
X-Request-ID: <可选 UUID>
```

成功：`ok=true`，`data` 有值，`error=null`，`request_id` 与 header 对齐，`server_time` 为 UTC `...Z`。  
失败：`ok=false`，`data=null`，`error.code` / `retryable`。extra 字段拒绝。  
用 `code` 映射 UI，不要用短英文 `message`。

---

## 5. AppPhase 对照

```text
启动 → 恢复会话 / 匿名登录          （P03，直连 Supabase Auth）
     → GET /bootstrap               （P06）
     → 无精灵：needsAgreement S02   （P07-T04，无 API）
     → needsEggSelection S03        （POST /spirits）
     → needsFirstConversation(step) （POST /chat；step = onboarding.step）
     → complete + bootstrap         （hatched_at 有值）
     → ready S01                    （P07 出口；房间四态 UI 属 P08）
```

`needsFirstConversation` 的 `step` **只信服务端** `onboarding.step`（完整轮次数 0–5），不要用本地发送计数。

---

## 6. 全链路时序与 version

```text
匿名登录
  → GET  /bootstrap                 无精灵
  → S02
  → POST /spirits                   version=1，step=0
  → GET  /bootstrap                 有精灵+room，step=0
  → POST /chat × 5                  第 n 轮后 step=n，version=1+n
  → POST /onboarding/complete       expected_spirit_version = 6（五轮后）
  → GET  /bootstrap                 required=false，hatched_at 有值
```

| 动作 | version | step | hatched_at |
| :--- | ---: | ---: | :--- |
| 创建 | 1 | 0 | null |
| chat 1…5 | 2…6 | 1…5 | null |
| complete | 7 | 5 | 有值 |

Chat 的 `patch.room` **恒为 null**；房间看 bootstrap 或 complete 的 patch。

---

## 7. `GET /api/v1/bootstrap`

无 query。杀进程恢复也走它。

| 账号 | spirit/room | required | step |
| :--- | :--- | :--- | :--- |
| 未创建 | null/null | true | 0 |
| 孵化中 | 有值 | true | 0–5 |
| 已 complete | 有值 + hatched_at | false | 5 |

---

## 8. `POST /api/v1/spirits`

`client_id` 一次生成，重试/杀进程复用。

```json
{
  "client_id": "<uuid>",
  "egg": "wild",
  "name": "未名",
  "consents": {
    "ai_disclosure": { "document_version": "2026-09", "explicitly_accepted": true },
    "data_notice": { "document_version": "2026-09", "displayed": true },
    "user_terms": { "document_version": "2026-09", "displayed": true }
  }
}
```

- `egg` 仅 `warm`/`cold`/`wild`；非法 → 422
- `name` 1–20
- AI 声明必须字面 `explicitly_accepted: true`

---

## 9. `POST /api/v1/chat`（S04）

每句：`onboarding: true`，`source: "text"`。缺 `onboarding` → 422。

```json
{
  "client_message_id": "<本句稳定 UUID>",
  "content": "你好",
  "source": "text",
  "onboarding": true,
  "context": {
    "timezone": "Asia/Shanghai",
    "local_hour": 21,
    "weather": "cloudy",
    "city": null
  }
}
```

成功：`type=chat_turn`，`generation_source=stub`，`should_extract=false`，usage 0。  
成对保存后才 `onboarding_step+1`；半轮失败不推进。同 ID 同 payload 重放不连加。

Stub（发送前 step）：`0嗯。` `1好。` `2记下了。` `3继续。` `4+好的。`

无精灵 → 404。已孵化仍 `onboarding=true` → `ONBOARDING_ALREADY_COMPLETED`。孵化中 `false` → `ONBOARDING_INCOMPLETE`。

---

## 10. `POST /api/v1/onboarding/complete`

```json
{
  "client_id": "<与 spirits.client_id 不同的稳定 UUID>",
  "expected_spirit_version": 6
}
```

须 `step==5` **且** 库中 ≥5 个完整 onboarding 成对。version 用**当时** `spirit.version`（五轮后为 6）。  
同 ID 重放同一 `hatched_at`；换 ID → `ONBOARDING_ALREADY_COMPLETED`。

---

## 11. 错误码与幂等

| HTTP | code | retryable | 处理 |
| ---: | :--- | :--- | :--- |
| 401 | `UNAUTHENTICATED` | false | 登录；查 RS256 |
| 404 | `NOT_FOUND` | false | 先创建精灵 |
| 409 | `CONFLICT` | false | bootstrap 当前 version / 原 client_id |
| 409 | `IDEMPOTENCY_CONFLICT` | false | 勿改已发 id |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | true | 同一 id 稍后重试 |
| 409 | `ONBOARDING_INCOMPLETE` | false | 继续 S04 |
| 409 | `ONBOARDING_ALREADY_COMPLETED` | false | 进房间 |
| 422 | `INVALID_INPUT` | false | 修 body |
| 503 | `MODEL_UNAVAILABLE` / `DEPENDENCY_UNAVAILABLE` | true | 同一 id 重试 |
| 500 | `INTERNAL_ERROR` | false | 拉 bootstrap，勿本地假结算 |

| 操作 | 稳定 ID |
| :--- | :--- |
| 创建精灵 | `client_id` |
| 每一句 chat | `client_message_id` |
| complete | 另一个 `client_id` |

S04 单请求在途。重试禁止新 UUID。

---

## 12. 人工联调清单（按阶段）

**P01** 真机 `.../health/live` 与 `.../health/ready` → 200。  
**P03** 匿名登录后无 token bootstrap → 401；带 RS256 token → 200。  
**P05** 带 JWT `POST /spirits`（无需预插 `auth.users`）；重复同一 `client_id` 同一精灵；`wild` 映射正确。  
**P06** 无精灵 bootstrap：spirit/room 同 null；创建后两者都有，`snapshot_version` 等于 version。  
**P07** S02 未预勾；S04 五句进度只计完整轮；两句后杀进程 bootstrap 为 2/5；complete 后 `hatched_at` 有值、普通轮次仍 0；Stub 标识；日志无 JWT/正文。

---

## 13. 本范围明确不做

- P08–P10：见 [`p08-p10-integration.md`](p08-p10-integration.md)（房间四态、消息分页、真实 Chat/Extract）
- 麦克风、语音、Feed、记忆 CRUD、共学、社交、通知、鉴定卡、设置注销

常见坑：fixture version 当死数；chat 的 `patch.room` 当房间；complete 用 version=1；ES256 旧 token；ATS；pytest 清空库。

标准成功/失败 JSON 以仓库 fixture 为准（与 OpenAPI SHA 同锁），不要手改：

- `fixtures/GET_api_v1_bootstrap/success.json`（无精灵样例）
- `fixtures/POST_api_v1_spirits/success.json`
- `fixtures/POST_api_v1_chat/success.json`
- `fixtures/POST_api_v1_onboarding_complete/success.json`
- 各目录 `business_error.json`、`missing_required_field.json`

live 响应里的 id、version、`server_time` 以服务端为准。
