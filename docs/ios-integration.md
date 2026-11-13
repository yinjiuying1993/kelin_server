# 刻灵 iOS 联调说明

面向 **192.168.100.205** 上的 iOS 工程 ↔ 本机 FastAPI（`192.168.100.212:8000`）。  
现行公开面以本文和 `fixtures/manifest.json` 为准。分册里的步骤若与本文路由表冲突，以本文为准。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机工作区 `3_ios/` 无 App 源码。

| 项 | 当前值 |
| :--- | :--- |
| Alembic head | `20261104_0128` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/` |
| API | `http://192.168.100.212:8000` |
| iOS 设备 | `192.168.100.205` |
| Breaking 清单 | [`s1-openapi-breaking.txt`](s1-openapi-breaking.txt)（相对 V0，历史对照） |

联调不校验 OpenAPI SHA-256。DTO 按本文与 live 响应字段对接；契约变了改对应字段即可，不必同步指纹。

V0 的 OpenAPI SHA 不是现行锁，分册里的历史指纹也不是。S8 没有另行登记指纹。

传艺、问艺已接通，字段和对接口顺序见 [`v3-ios-integration.md`](v3-ios-integration.md)。本文路由表不含这两组接口。

运行时看 `api_version=v1`、`schema_version=2`。live 的 id、时间、`snapshot_version`、配额 `used` 以响应为准。

---

## 1. 环境与身份

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

本机库会在第一次写事务时用 JWT `sub` 补 `auth.users`。不要把 `user_id` 放进业务 body。没有单独的开户接口。

未知字段一律 422。DTO 用 `extra=forbid` 同款解码：多出来的键要能在调试里看见。

---

## 2. 进程

`compose.yaml`：**api + scheduler + worker**。`postgres-test` 只给会 `DROP SCHEMA` 的测试用，不要在联调账号上跑。

| 进程 | 做什么 |
| :--- | :--- |
| `api` | HTTP，`0.0.0.0:8000` |
| `scheduler` | 状态、串门、用量汇总入队 |
| `worker` | 串门结算、鉴定生成、抽取、账号注销。不发 APNs |

Chat、记忆、投喂、Pact、设置只要 api。明信片落地、鉴定离开 `generating`、注销清库需要 **worker 在跑**。改 Python 或 `.env` 后重启对应容器。

---

## 4. 进房主路径

`POST /api/v1/spirits` 成功后即可 `GET /api/v1/bootstrap` 进房间。没有五句引导，也没有完成引导的接口。

1. 匿名登录，拿到 RS256 access token。
2. `POST /api/v1/spirits`。body：`client_id`、`egg`（`warm` / `cold` / `wild`）、`name`（1–20 字）、`consents`。  
   `consents.ai_disclosure.explicitly_accepted` 必须是 `true`，且不能在 UI 上预勾。`data_notice` 与 `user_terms` 的 `displayed` 必须是 `true`。三份 `document_version` 都是 `2026-11-12`。意见书见 [`feedback-ios-integration.md`](feedback-ios-integration.md)。
3. 响应 `data.patch.spirit.hatched_at` 有值，`stage` 为 `whelp`，`status` 为 `home`。邀请码 8 位，字符集 `A-H`、`J-N`、`P-Z`、`2-9`。
4. `GET /api/v1/bootstrap` 整包替换本地会话。房间在 `data.room`，精灵在 `data.spirit`，与 `snapshot_version` 同级。写接口才用 `data.patch`。房间读四件套，不要自己算阶段或情绪。

房间四件套：

| 字段 | 含义 |
| :--- | :--- |
| `stage_image_key` | `whelp_image` / `formed_image` / `awake_image` |
| `primary_emotion_label` | 主情绪标签 |
| `letter_key` | 字条，可空 |
| `text_status` | 文字状态 |
| `status` | `home` / `away` / `study` / `lost` |

同一只精灵的房间、聊天、记忆页、鉴定读同一 `snapshot_version`。

---

## 5. 合入规则

服务端结算成长、状态、配额、鉴定和串门。客户端提交事实和稳定 id，不提交 delta。

只接受两种更新：

1. `GET /bootstrap`：整包替换
2. 写接口的 `data.patch`，加上同包的 `data.quotas` 和 `data.events`

```text
patch.snapshot_version  <  本地  →  丢弃，立刻 GET /bootstrap
patch.snapshot_version  >= 本地  →  合入（同版本是幂等回放）
```

`patch.spirit` / `room` / `social` / `report` / `pact` 为 null 表示本包不改那一块。

不要对 `bond`、`hunger`、`status`、`stage`、`quota.used`、Pact 分数做乐观加减。18h 出门、72h 走失、配额日界、Pact 的 `session_date` 用服务端时间。

写操作的 `client_id`（Chat 是 `client_message_id`）在第一次进入该动作时生成。失败、重试、杀进程后原样重放。新动作用新 UUID。同一 id 换 body → `409 IDEMPOTENCY_CONFLICT`。

分页 cursor 不透明。过滤条件变了就丢掉旧 cursor，否则 `INVALID_CURSOR`。`limit` 默认 30，最大 50。

---

## 6. 公开路由

Health 无 JWT：`GET /health/live`、`GET /health/ready`。  
`/health/ready` 的 `checks.migration` 现为 `not_applicable`；库不通时是 503，`database=unavailable`。

| 方法 | 路径 | 做什么 |
| :--- | :--- | :--- |
| GET | `/api/v1/bootstrap` | 整包快照 |
| POST | `/api/v1/spirits` | 创建并孵化 |
| POST | `/api/v1/chat` | 一轮对话。`source=voice` 成功包可带 `speech_audio` |
| GET | `/api/v1/messages` | 历史分页 |
| POST | `/api/v1/extract` | 对服务端窗口抽取。客户端不传消息起止 id |
| GET | `/api/v1/memories` | 记忆页 |
| PATCH | `/api/v1/memories/{memory_id}` | `correct` 或 `seal` |
| DELETE | `/api/v1/memories/{memory_id}` | 删除一条 |
| DELETE | `/api/v1/memories` | 清空。确认串必须是 `CLEAR_ALL_MEMORIES` |
| POST | `/api/v1/feed` | `food` / `knowledge` / `emotion` / `promise` |
| PATCH | `/api/v1/feeds/{feed_id}` | 改约定 |
| POST | `/api/v1/feeds/{feed_id}/complete` | 完成约定 |
| POST | `/api/v1/feeds/{feed_id}/cancel` | 取消约定 |
| POST | `/api/v1/transcribe` | ASR |
| POST | `/api/v1/synthesize` | TTS |
| GET | `/object/{bucket}/{object_path}` | 播放签名 URL。不进 OpenAPI |
| POST | `/api/v1/pacts` | 开共学 |
| POST | `/api/v1/pact-session` | 开一场 |
| POST | `/api/v1/pact-answer` | 答题。完成发生在合格的末题事务里 |
| POST | `/api/v1/pact-skip` | 跳过 |
| GET / POST | `/api/v1/friends` | 好友列表 / 加好友 |
| DELETE | `/api/v1/friends/{friend_id}` | 删除好友 |
| GET | `/api/v1/postcards` | 明信片 |
| PATCH | `/api/v1/postcards/{postcard_id}/read` | 已读 |
| GET | `/api/v1/report` | 鉴定。未合格是 200，`status=locked` |
| POST | `/api/v1/report/line` | 只重试签名句 |
| PATCH | `/api/v1/spirit` | 设置 |
| DELETE | `/api/v1/account` | 注销，成功是 202 |
| POST | `/api/v1/recall` | 外出召回。字段见 [`recall-ios-integration.md`](recall-ios-integration.md) |
| POST | `/api/v1/feedback` | 意见书。字段见 [`feedback-ios-integration.md`](feedback-ios-integration.md) |

`/api/v1/debug/*` 只在 `APP_ENV=dev` 注册。Release 必须当成不存在。

下列路径现在是 `404 NOT_FOUND`，不会结算：

- `POST /api/v1/onboarding/complete`
- `POST /api/v1/devices`
- `POST /api/v1/moderate-sight`
- `POST /api/v1/storage/sight-upload-url`

公开契约里也没有 `POST /visits`、`POST /search`、`POST /pacts/{id}/complete`。

---

## 7. 聊天、记忆、投喂、找回

Chat body：`client_message_id`、`content`、`source`（`text` / `voice`）、`context`。  
`context` 要 `timezone`、`local_hour`（0–23）、`weather`、`city`（可 null）。这些只影响表达，不参与配额日界和走失结算。不要再传 `onboarding`。

`should_extract=true` 表示窗口已满 3 个普通轮次，可以 `POST /extract`。抽取失败不回滚已经落库的聊天。记忆类型只有 `preference`、`knowledge`、`emotion`、`relationship`。封存或删除后，下一轮召回不含该条。迟到抽取不会把已封存或已删除的记忆写回来。

投喂 `kind=sight`、召回 `method=sight` 在校验阶段就被拒绝。精灵外出时用 `POST /api/v1/recall`，接法见 [`recall-ios-integration.md`](recall-ios-integration.md)。

语音 Chat 自己带 TTS。`speech_audio` 为空时仍是 200，界面回退到文字。不要把 `generation_source=stub` 显示成已接通的模型。

---

## 8. 不要做

- 不要直连 Postgres、百炼或 Storage。Key、模型名、Workspace、APP_ID 不得进 iOS、Git 或日志
- 不要把 JWT、Debug token、邀请码、记忆正文、Prompt 打进日志
- 不要提交 `hunger_delta`、`status`、`stage`、`bond` 让服务端照单改
- 不要本地重算阶段、情绪、去向或记忆准入
- 不要在 Release 打 `/api/v1/debug/*`
- 不要在联调期对同一库跑会 `DROP SCHEMA` 的 pytest
- 不要把设备登记或远程推送当成已接通。公开面没有 `POST /devices`，worker 不发 APNs
- 不要宣称阶段通过或真机推送通过

---

## 9. 错误码

用 `error.code` 映射 UI。`retryable=true` 才用同一个 `client_id` 自动重试。

| code | 典型处理 |
| :--- | :--- |
| `UNAUTHENTICATED` | 刷新或重登。确认 RS256 |
| `FORBIDDEN` | Debug 身份或来源 |
| `INVALID_INPUT` | 缺字段、多余字段、非法枚举 |
| `INVALID_CURSOR` | 丢掉 cursor，从第一页重拉 |
| `NOT_FOUND` | 不属于自己，或该路由已经不存在 |
| `CONFLICT` | 拉 bootstrap，或使用 `details` 里的当前版本 |
| `IDEMPOTENCY_IN_PROGRESS` | 同一 id 稍后重试 |
| `IDEMPOTENCY_CONFLICT` | 不要把同一 id 用到另一份 body |
| `QUOTA_EXCEEDED` / `RATE_LIMITED` | 展示配额和 `reset_at`。不要换 id 硬打 |
| `DEPENDENCY_UNAVAILABLE` / `MODEL_UNAVAILABLE` / `PROVIDER_TIMEOUT` | 可重试。Chat 走失败 UI，不落假回复 |
| `SPIRIT_NOT_LOST` | 现在不能口粮找回 |
| `ACCOUNT_DELETE_PENDING` | 注销已在进行。收到 202 后立刻清本地 |

鉴定未合格不是错误码，而是 `GET /report` 的 `status=locked`。

---

## 10. 联调清单

- [ ] `health/live` 真机可达；ATS 已放行 `192.168.100.212`
- [ ] 匿名 JWT `alg=RS256`；无 token 的业务接口 401
- [ ] DTO 按本文字段对接，不锁 OpenAPI SHA
- [ ] `POST /spirits` 后 `hatched_at` 有值，bootstrap 能进房间
- [ ] 房间用四件套，不读 `layers`
- [ ] Chat 不传 `onboarding`；`source=voice` 能解 `speech_audio` 为 null
- [ ] 记忆只有四类；投喂没有 `sight`；找回只有 `food`
- [ ] `onboarding/complete`、`devices`、`moderate-sight`、`sight-upload-url` 为 404
- [ ] Envelope 用 `error.code`；日志无 JWT、token、Prompt
- [ ] `client_id` 稳定；迟到 patch 丢弃并重新 bootstrap
- [ ] 需要明信片、鉴定生成或注销清库时，scheduler 和 worker 都在跑
- [ ] Debug 只在 DEBUG 编译里
- [ ] 不宣称验收通过或真机 APNs 通过
