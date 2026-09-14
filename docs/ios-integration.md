# 刻灵 iOS 整体接入说明

面向 **192.168.100.205** 上的 iOS 工程 ↔ 本机 FastAPI（`192.168.100.212:8000`）。  
覆盖 V0 已接线的公开能力：匿名登录、孵化、房间/Chat、记忆、投喂、成长、语音、共学、社交、设备登记、鉴定、设置/注销、找回。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机工作区 `3_ios/` 无 App 源码。

分阶段字段、fixture 目录、联调步骤仍以同目录分册为准。本文只锁 **当前全量** 环境、契约指纹、进程、合入规则，以及「先看哪一份」。

| 项 | 当前值 |
| :--- | :--- |
| OpenAPI SHA-256 | `fed40c13c30d2203f20ed83c7ad208042c13f35e330d3491e407f22be0593230` |
| Alembic head | `20260908_0019` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/`（与 SHA 同锁，见 `manifest.json`） |
| API | `http://192.168.100.212:8000` |
| iOS 设备 | `192.168.100.205` |

分册表头里的旧 SHA（`fc79081f…`、`f46da36e…`、`5aea0b23…`、`cdb52009…`、`743b462b…`）是 **当时导出的快照**，给当时那一截联调用。现在解码、对 fixture、写报告，一律用上表。

---

## 1. SHA 怎么用

SHA 是规范化 OpenAPI JSON 的指纹，**不是**运行时版本，请求里不要带，服务器也不靠它鉴权。

- 运行时兼容看 `api_version=v1`、`schema_version=2`。
- 修 worker / SQL / 结算、重启容器、换 IP：**SHA 不变**，iOS 不用改。
- 只有公开契约变了（新路由、改字段/必填/枚举、改进 OpenAPI 的描述）指纹才会变。那时 iOS 要改的是 **DTO / fixture**，换 SHA 只是证明两边锁的是同一份规格。
- 已接好的 Chat / Pact 等，字段没破就不必为新 SHA 重写一遍。

标准 JSON 以仓库 fixture 为准。live 的 id、时间、`snapshot_version`、配额 `used` 以响应为准，不要把 fixture 钉死。

---

## 2. 环境与身份

| 项 | 改什么 |
| :--- | :--- |
| `API_BASE_URL` | Debug：`http://192.168.100.212:8000`。业务 `{API_BASE_URL}/api/v1/...`；探活 `{API_BASE_URL}/health/live` |
| `SUPABASE_URL` | `https://rnvfnvowjavtojvzgvtr.supabase.co` |
| `SUPABASE_ANON_KEY` | Dashboard 的 publishable / anon，只放本机 `.xcconfig`（gitignore） |
| `APP_ENV` | `dev` |
| ATS | 真机访问 IP 必须允许明文 HTTP。`Info.plist` 为 `192.168.100.212` 加 `NSExceptionDomains`（`NSExceptionAllowsInsecureHTTPLoads=true`），或 Debug 临时 `NSAllowsArbitraryLoads`（不要进 Release） |
| Auth | `signInAnonymously()`。恢复 Keychain 后再打 bootstrap。无登录页 |
| Token | 每个 `/api/v1`：`Authorization: Bearer <accessToken>`。`alg` 必须 **RS256**；换算法后退出再登录，否则一直 401 |
| Envelope | `ok` / `data` / `error.code` / `error.retryable` / `request_id` / `server_time`。UI 映射 `code`，不要展示英文 `message` |
| Header | 建议 `X-Request-ID` 为 UUID；`Content-Type: application/json` |

先验证：真机浏览器打 `http://192.168.100.212:8000/health/live` → `{"status":"live"}`。无 JWT 打 `/api/v1/bootstrap` → `401 UNAUTHENTICATED`。

本机库会在第一次写事务时用 JWT `sub` 补 `auth.users`。**不要**把 `user_id` / `sub` 放进业务 body，没有「开户」API。`prod` 不走这条补行。

---

## 3. 进程

`compose.yaml`：**api + scheduler + worker**。`postgres-test` 仅 tools profile，给会 `DROP SCHEMA` 的测试用，**不要**在联调账号上跑。

| 进程 | 做什么 |
| :--- | :--- |
| `api` | HTTP，`0.0.0.0:8000` |
| `scheduler` | 入队 state / visit / usage_rollup / notification_plan |
| `worker` | 串门规划与结算、通知规划、鉴定生成、账号注销。**不发 APNs** |

Chat / 记忆 / 投喂 / Pact / 设置只要 api。明信片落地、鉴定离开 `generating`、注销清库需要 **worker 在跑**。改 Python / `.env` 后重启对应容器，只重启 api 不够。

Windows 入站规则 `kelin-api-8000` 已加过。

---

## 4. 权威数据与 SessionStore

服务端是成长、状态、配额、孵化、鉴定、串门的唯一结算方。客户端交 **事实**（文本、client_id、邀请码），不交 delta。

只接受两种更新：

1. `GET /bootstrap`：**整包替换**
2. 写接口的 `data.patch` + 同包 `data.quotas` + `data.events`

```text
patch.snapshot_version  <  本地  →  丢弃，立刻 GET /bootstrap
patch.snapshot_version  >= 本地  →  合入（同版本 = 幂等回放）
```

`patch.spirit` / `room` / `social` / `report` / `pact` 为 **null** 表示本包不改那一块，不是清空。

ViewModel 禁止对 `bond/hunger/status/stage/quota.used/score` 做乐观加减。离线是叠加层，不要写成 `spirit.status`。

18h away / 72h lost、配额日界、Pact 的 `session_date` 用 **server time**，不要用设备日历。

### 幂等 `client_id`

写操作（创建精灵、Chat 的 `client_message_id`、Feed、记忆、Pact 每题、好友、已读、设置、注销、找回…）在首次进入该动作时生成 UUID，失败/重试/杀进程 **原样重放**。新动作新 UUID。同一 `client_id` 换 payload → `IDEMPOTENCY_CONFLICT`。

分页 cursor 不透明，不要当 SQL。过滤条件变了丢掉旧 cursor，否则 `INVALID_CURSOR`。`limit` 默认 30、最大 50。

---

## 5. 全局不要做

- 不要直连 Postgres / 百炼 / Storage service role；Key、模型名、Workspace、APP_ID 不得进 iOS / Git / 日志
- 不要打 JWT、APNs token、Debug token、邀请码当密码、记忆正文、Prompt 进日志
- 不要 `POST /visits`、`POST /search`、`POST /pacts/{id}/complete`、`/link`、`/bind`、identity magic-link
- 不要客户端选串门 host/NPC；不要提交 `hunger_delta` / `status` / `scholar_marks`
- 不要在 Release 打 `/api/v1/debug/*`
- 不要把 `generation_source=stub` 显示成百炼
- 不要在联调期对该库跑 db pytest（会清数据，需重新孵化）
- 不要宣称阶段通过、已部署、真机 APNs 通过（除非实际做到）

---

## 6. 分册（细节以分册为准）

| 阶段 | 文档 | 本机要点 |
| :--- | :--- | :--- |
| P01–P07 | [`p01-p07-integration.md`](p01-p07-integration.md) | 匿名 Auth、S02–S04、`POST /spirits`、`POST /chat`（孵化）、`POST /onboarding/complete`。S04 短入口 [`s04-ios-integration.md`](s04-ios-integration.md) |
| P08–P10 | [`p08-p10-integration.md`](p08-p10-integration.md) | 房间四态、`GET /messages`、真实 Chat + `POST /extract`。无 `POST /search` |
| P11 | [`p11-integration.md`](p11-integration.md) | 记忆 list / correct / seal / delete / clear |
| P12 | [`p12-integration.md`](p12-integration.md) | 五类 Feed、Promise、地点见闻。照片 PUT 本机桶仍是内存，真机上传常落不到 |
| P13 | [`p13-integration.md`](p13-integration.md) | 无新写接口。成长/进化/18h/72h/配额只合 patch |
| P14 | [`p14-integration.md`](p14-integration.md) | `POST /transcribe`、`POST /synthesize`；播放 URL 有签名过期 |
| P15 | [`p15-integration.md`](p15-integration.md) | Pact create/session/answer/skip；完成只发生在末题 answer 事务内 |
| P16 | [`p16-integration.md`](p16-integration.md) | 好友、明信片；串门 worker-only |
| P17 | [`p17-integration.md`](p17-integration.md) | `POST /devices`；**不发 APNs**；Promise 仍用 iOS 本地通知 |
| P18 | [`p18-integration.md`](p18-integration.md) | `GET /report` 未合格是 `status=locked` 的 200 |
| P19 | [`p19-integration.md`](p19-integration.md) | `PATCH /spirit`、`DELETE /account` 固定 202 |
| P20 | [`p20-integration.md`](p20-integration.md) | `POST /recall`；Debug 仅 `APP_ENV=dev` |
| P16–P20 索引 | [`p16-p20-integration.md`](p16-p20-integration.md) | 后五阶段进程与空洞 |

---

## 7. 公开路由（当前 api）

Health（无 JWT）：`GET /health/live`、`GET /health/ready`。

| 方法 | 路径 | 阶段 |
| :--- | :--- | :--- |
| GET | `/api/v1/bootstrap` | P06 |
| POST | `/api/v1/spirits` | P05 |
| POST | `/api/v1/chat` | P07 / P09 / P10 |
| POST | `/api/v1/onboarding/complete` | P07 |
| GET | `/api/v1/messages` | P09 |
| POST | `/api/v1/extract` | P10 |
| GET/PATCH/DELETE | `/api/v1/memories`、`/memories/{id}` | P11 |
| POST | `/api/v1/feed` | P12 |
| PATCH | `/api/v1/feeds/{feed_id}` | P12 |
| POST | `/api/v1/feeds/{feed_id}/complete`、`.../cancel` | P12 |
| POST | `/api/v1/storage/sight-upload-url` | P12 |
| POST | `/api/v1/moderate-sight` | P12 |
| POST | `/api/v1/transcribe`、`/synthesize` | P14 |
| GET | `/object/{bucket}/{object_path}`（播放，不进 OpenAPI） | P14 |
| POST | `/api/v1/pacts`、`/pact-session`、`/pact-answer`、`/pact-skip` | P15 |
| GET/POST | `/api/v1/friends` | P16 |
| DELETE | `/api/v1/friends/{friend_id}` | P16 |
| GET | `/api/v1/postcards` | P16 |
| PATCH | `/api/v1/postcards/{postcard_id}/read` | P16 |
| POST | `/api/v1/devices` | P17 |
| GET | `/api/v1/report` | P18 |
| POST | `/api/v1/report/line` | P18 |
| PATCH | `/api/v1/spirit` | P19 |
| DELETE | `/api/v1/account` | P19 |
| POST | `/api/v1/recall` | P20 |
| * | `/api/v1/debug/*` | 仅 dev；prod/默认 test OpenAPI 无 |

公开契约 **没有**：`POST /visits`、`POST /search`、`POST /pacts/{id}/complete`、identity `/link` `/bind`。

---

## 8. 本机空洞（按代码事实，不要当产品已完成）

| 现象 | 客户端怎么做 |
| :--- | :--- |
| `GET /bootstrap.active_pact` 恒 null | 以 Pact mutation 的 `patch.pact` 为准 |
| `unread_postcards` 恒 `[]` | `GET /postcards?unread_only=true` 与 `patch.postcards_upsert` |
| worker **不发 APNs** | 登记设备仍要做；远程推送收不到是预期。约定走本地通知 |
| 见闻 Storage 签发 URL 是 `kelin.invalid` | 照片链契约可接，真机 PUT 常落空 |
| Debug 仅 `APP_ENV=dev` | Release 必须当 404；入口 `#if DEBUG` |
| 注销 202 后业务 API 被门禁 | 收到 202 **立刻**清本地，不要轮询 Chat |

`report` 已在 bootstrap 里，未合格为 `status=locked`。

---

## 9. AppPhase 与建议全链路

```text
launching → restoringSession / signingInAnonymously
  → requestingBootstrap
  → spirit==null              → S02 协议（不打 API）→ S03 POST /spirits
  → onboarding.step<5         → S04 POST /chat（onboarding=true）
  → POST /onboarding/complete → hatched_at 有值 → ready
  → S01 房间（spirit.status + room）
  → S05 Chat：GET /messages + POST /chat；should_extract → POST /extract
  → S07 记忆 / S06 投喂 / S05 语音 / S08 共学
  → S09 好友明信片（串门等 worker）
  → 设备登记；S10 鉴定；S11 设置；走失 POST /recall
```

S02：AI 声明不可预勾。孵化完成以服务端 `hatched_at` 为准，不要本地数 5 句。

建议先打通 1→7（能进房间并 Chat），再按产品优先级接 11–15，社交/鉴定/设置依赖孵化后的账号。双账号串门需要两台 JWT + worker。

---

## 10. 公共错误码

用 `error.code` 映射 UI。`retryable=true` 才自动重试（同一 client_id）。

| code | 典型处理 |
| :--- | :--- |
| `UNAUTHENTICATED` | 刷新/重登；确认 RS256 |
| `FORBIDDEN` | Debug 身份/来源；或权限 |
| `INVALID_INPUT` | 缺字段、多余字段、格式 |
| `INVALID_CURSOR` | 丢 cursor，从第一页重拉 |
| `NOT_FOUND` | 资源不属于自己或邀请码无效 |
| `CONFLICT` | 拉 bootstrap / 用 `details.current_snapshot_version` |
| `IDEMPOTENCY_IN_PROGRESS` | 同一 client_id 稍后重试 |
| `IDEMPOTENCY_CONFLICT` | 不要把同一 id 用到另一 payload |
| `QUOTA_EXCEEDED` / `RATE_LIMITED` | 展示 `quota` + `reset_at`，勿改 client_id 硬打 |
| `DEPENDENCY_UNAVAILABLE` / `MODEL_UNAVAILABLE` / `PROVIDER_TIMEOUT` | 可重试；Chat 可走失败 UI |
| `ONBOARDING_INCOMPLETE` / `ALREADY_COMPLETED` | 回到孵化状态机 |
| `ACCOUNT_DELETE_PENDING` | 注销已在进行 |

业务码（Pact / 好友 / 找回 / 鉴定 line 等）见对应分册。鉴定 **locked 不是错误码**。

---

## 11. 人工联调清单

- [ ] `health/live` 真机可达；ATS 已放行 `192.168.100.212`
- [ ] 匿名 JWT `alg=RS256`；无 token 业务接口 401
- [ ] DTO / fixture 锁 SHA `fed40c13…`，不是分册里的历史值
- [ ] Envelope 用 `error.code`；日志无 JWT / token / Prompt
- [ ] `client_id` 稳定；未知字段解码失败可观测
- [ ] SessionStore：bootstrap 整包；patch 按版本合入；null 不当清空
- [ ] 不本地算成长/配额/孵化/鉴定/Pact 分数
- [ ] 无 visit create、无 Pact complete、无 search、无 bind
- [ ] 需要异步能力时 scheduler + worker 都在跑
- [ ] 联调期不对同一库跑 DROP SCHEMA 的 pytest
- [ ] Debug / 任意 HTTP 负载 仅 DEBUG 编译
- [ ] 不宣称验收通过或真机 APNs 通过

---

## 12. 本范围明确不做

- 在本机 `3_ios/` 改工程（到 205 做）
- 改 OpenAPI 字段或为联调新写 migration
- 更新 `STATUS.md`、写 `1_plan/reports/`、commit / push
- 把分册历史 SHA 当当前全量锁
- 宣布 V0 或任一阶段「通过」
