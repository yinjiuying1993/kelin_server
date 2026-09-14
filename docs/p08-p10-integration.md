# 刻灵 P08–P10 联调说明

V0 全量入口：[`ios-integration.md`](ios-integration.md)（当前 SHA `fed40c13…`）。本文 SHA 是加记忆/投喂等路由 **之前** 的快照。

面向 iOS ↔ 本机 FastAPI，覆盖 **P08 房间四态、P09 消息历史与 Chat 框架、P10 真实 Chat + Extract**。  
前置：已按 [`p01-p07-integration.md`](p01-p07-integration.md) 完成匿名登录、创建精灵、S04 五句、`POST /onboarding/complete`，`hatched_at` 有值。  
P11 记忆 list/correct/seal/delete 见 [`p11-integration.md`](p11-integration.md)。  
P12 五类 Feed / 见闻 / Promise 见 [`p12-integration.md`](p12-integration.md)（OpenAPI SHA 已换）。  
P13 成长 / 配额 / 迟到 patch 见 [`p13-integration.md`](p13-integration.md)。  
P14 语音契约见 [`p14-integration.md`](p14-integration.md)（**SHA 已换**）。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机工作区 `3_ios/` 仍无 App 源码，本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `f46da36ea05c7eb3a7d9a87ba9cd92c28ea4662a68c9986a9a41abcbcbe48843` |
| Alembic head | `20260908_0007` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/`（与 SHA 同锁） |

P01–P07 文档里的 SHA `fc79081f…` 是加 `/messages`、`/extract` **之前**的锁。P08–P10 解码请用上表 SHA。

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| P08 状态 settle | **已接线**。`GET /bootstrap` 前置结算 18h/72h；无 `PATCH /spirit` | `app/services/bootstrap.py`、`app/domain/spirit_state.py` |
| P08 房间投影 | **已接线**。`room.layers` / `letter` / `pending_sight` / `unread_footprint_count` | `app/domain/room.py` |
| P09 `GET /messages` | **已接线**。owner keyset，`limit` 默认 30、最大 50 | `app/api/v1/messages.py` |
| P09 `POST /chat` 框架 | **已接线**。`onboarding` 必填；window 服务端所有 | `app/api/v1/chat.py` |
| P10 真实 Chat | **S04 与普通轮都走百炼智能体**（已接线时 `generation_source=provider`） | `app/services/chat.py`、`app/integrations/bailian.py` |
| P10 Extract | **已接线**。`POST /extract`；无公开 `/search` | `app/api/v1/extract.py` |
| P10 Search | **仅 Chat 内部工具**。默认不启用百炼 Search；失败固定「这件事我还不知道」 | `app/domain/retrieval.py` |

进程仍是 `kelin-postgres-test` + `kelin-server-api`（`0.0.0.0:8000`）。本机 **没有**独立 worker 容器；Extract 靠 iOS 打 `POST /extract`（规格允许兼容触发）。改 Python 后 `docker restart kelin-server-api`。

**不要**在联调期间对该库跑会 `DROP SCHEMA` 的全量 pytest。

---

## iOS 需要改什么

### 不要做

- 不要直连 Postgres / 百炼；Key、模型名、APP_ID、Workspace ID 不得进 iOS / Git / 日志  
- 不要 `POST /search`、不要猜 Search URL；没有这条路由  
- 不要 `PATCH` 精灵 `status`；不要本地算 18h/72h  
- 不要把 `offline` 写成 `spirit.status`  
- 不要在 Extract / Chat 里提交 `start_message_id` / `end_message_id`  
- 不要用消息 ID 拼 `conversation_window_id`  
- 不要把 `generation_source=stub` 显示成百炼；`provider` 才是真实生成  
- 不要在重试时换 `client_message_id` / Extract `client_id`  
- 不要并行两句 Chat；队列最多 3 条等待 + 1 条在途  
- 不要打 Pact、社交、通知、鉴定卡、注销（P14+）；成长/配额见 [`p13-integration.md`](p13-integration.md)；记忆 CRUD 见 [`p11-integration.md`](p11-integration.md)；Feed 见 [`p12-integration.md`](p12-integration.md)

### 必须改：DTO 对齐 SHA

锁定 SHA：`f46da36ea05c7eb3a7d9a87ba9cd92c28ea4662a68c9986a9a41abcbcbe48843`。  
新增解码：

- `GET_api_v1_messages/{success,business_error,missing_required_field,page_empty,page_first,page_middle,page_last,invalid_or_expired_cursor}.json`
- `POST_api_v1_extract/{success,business_error,missing_required_field}.json`
- 继续用 `POST_api_v1_chat/`（注意 live 的 `generation_source` 可能是 `provider`，fixture 样例仍是 `stub`）

未知字段 / 缺字段要失败可观测。fixture 的 UUID、时间、`snapshot_version` 是样例，**live 以响应为准**。

`SessionStore`：bootstrap **整包替换**；Chat / Extract 只合入服务端 `patch`。

### 必须改：AppPhase（P07 出口之后）

```text
ready（hatched_at 有值）
  → S01 房间：spirit.status + room 投影     （P08）
  → S05 Chat：GET /messages + POST /chat     （P09）
  → S04 与普通轮：generation_source=provider   （P10；已接智能体）
  → should_extract=true → POST /extract      （P10）
```

离线是 **叠加层**，不改 `spirit.status`。文案规格：连不上时房间还在，不清屏。

### 建议联调顺序

1. 确认 P07：`GET /bootstrap` → `hatched_at` 非 null、`onboarding.required=false`  
2. P08：看 `spirit.status` 与 `room.layers`；不要提交状态  
3. P09：`GET /messages` 空/首/下一页；普通 Chat `onboarding=false`  
4. P10：确认 `generation_source=provider`；第 3 个普通轮次 `should_extract=true` 后 Extract  

环境、JWT、ATS、IP 与 P01–P07 第 2 节相同：`API_BASE_URL=http://192.168.100.212:8000`。

---

## 1. P0 能力：现在能联什么

| P0 能力 | 阶段 | 本机状态 | 联调注意 |
| :--- | :--- | :--- | :--- |
| `GET /bootstrap` room + `spirit.status` | P08 | **可联** | 无新 mutation；settle 在 bootstrap 里 |
| `GET /api/v1/messages` | P09 | **可联** | keyset；非法 cursor → `INVALID_CURSOR` |
| `POST /api/v1/chat` 普通轮 | P09/P10 | **可联** | 必须 `onboarding=false`；本机成功应为 `provider` |
| `conversation_window_id` / `should_extract` | P09/P10 | **可联** | 每 3 个普通有效轮次 ready |
| `POST /api/v1/extract` | P10 | **可联** | 只传 `client_id` + `conversation_window_id` |
| Search | P10 | **无公开接口** | 仅服务端内部；iOS 只展示 `source_refs` |

仍不要打：ASR/TTS、Pact、社交、通知。记忆四端点见 [`p11-integration.md`](p11-integration.md)。Feed 见 [`p12-integration.md`](p12-integration.md)。

---

## 2. P08 房间四态（S01）

无新公开写接口。权威在每次 `GET /api/v1/bootstrap`（先短写 settle，**不改 `last_interact_at`**）。

### 2.1 `spirit.status`

仅四值：`home` | `study` | `away` | `lost`。  
优先级（服务端）：**lost > study > away > home**。  
iOS 只做素材映射，不要重算。

| 规则（服务端） | 结果 |
| :--- | :--- |
| 孵化后有效互动（成对 Chat 等）刷新 `last_interact_at` | 可从 study/away 拉回（lost 规则更严，以 bootstrap 为准） |
| 无有效互动 ≥ 18h | 进入 `study` 或 `away`（由服务端资格决定，客户端不选） |
| 无有效互动 ≥ 72h | `lost` |
| `GET /bootstrap` | **不是**有效互动 |

`offline` **不是** status。无网时保留上一帧房间，叠加「连不上，房间还在」。

### 2.2 `room` 投影

有精灵时 `spirit` 与 `room` 同在；无精灵两者都是 null。

```text
room.weather          string
room.layers           string[]     层序由服务端排好，iOS 按数组画
room.letter           object|null  只有 type / resource_id / title_key / occurred_at
room.pending_sight    object|null  feed_id / source=photo|location / status=pending|processing
room.unread_footprint_count  int≥0
room.updated_at       ISO 8601 UTC
```

`layers` 可能出现的 token（实现）：

| token | 含义 |
| :--- | :--- |
| `spirit` / `study` / `away` / `lost` | 对应 status 的主层（home→`spirit`） |
| `pending_sight` | 有未完成见闻 |
| `letter` | 有字条指针 |
| `footprints` | `unread_footprint_count > 0` |
| `scholar` | 有 `scholar_marks` |

`letter.type`：`lost` | `promise` | `pact` | `postcard` | `care`。  
`title_key` 固定如 `room.letter.lost`。优先级：lost > promise > pact > postcard > care。  
**letter / footprints 不含对话正文、记忆全文。**

Chat / Extract 的 `patch.room` **仍常为 null**；房间以 bootstrap 为准。

### 2.3 iOS 联调动作

- 热区：精灵首击气泡、二击进 Chat；刻痕 / 字条 / 足迹 / 摆件 / 书架按 UI 规格导航；底部入口不超过三个  
- 刷新失败：旧场景保留、交叉淡入；Reduce Motion 停循环位移  
- 不要用 Chat 的 `patch.room` 当丢房间  

本机短时联调很难等到 18h/72h。先对齐 **home + layers 含 `spirit`**；四态切换可用服务端改库或等 settle，不要客户端造 status。

---

## 3. P09 消息历史与 Chat 框架（S05）

### 3.1 `GET /api/v1/messages`

```http
GET /api/v1/messages?limit=30
GET /api/v1/messages?cursor=<next_cursor>&limit=30
Authorization: Bearer <access_token>
```

- `limit`：1–50，默认 30  
- `cursor`：不透明签名 keyset，**不要当 SQL、不要手改**  
- 排序：`created_at desc, id desc`  
- 不含 system 消息  
- 同一分页会话用响应里的 `snapshot_at` 当上界；新消息不插进当前翻页中间造成重复/漏行  

成功 `data`：

```json
{
  "items": [ /* MessagePublic */ ],
  "next_cursor": "<opaque 或 null>",
  "has_more": false,
  "snapshot_at": "2026-09-10T00:00:00Z"
}
```

`has_more=true` 时必有 `next_cursor`；末页 `has_more=false` 且 `next_cursor=null`。

`MessagePublic`：

| 字段 | 规则 |
| :--- | :--- |
| `id` | 服务端消息 ID |
| `client_id` | 用户消息有值；精灵消息 **null** |
| `role` | `user` \| `spirit` |
| `content` | 1–4000 |
| `source` | `text` \| `voice` \| `onboarding` |
| `status` | `accepted` \| `generated` \| `failed` |
| `reply_to_message_id` | 精灵回复指向用户消息；用户消息为 null |
| `source_refs` | 见第 5 节 |
| `created_at` | UTC `...Z` |

本地缓存：最近约 50 条 + 未完成发送；账号隔离。翻页合并按 `id` 去重，排序与服务端一致。

非法 / 过期 cursor → `422 INVALID_CURSOR`。不要用 offset。

### 3.2 `POST /api/v1/chat`（孵化后）

与 S04 **同一路径**，差别是 **`onboarding: false`**。  
已孵化仍传 `true` → `409 ONBOARDING_ALREADY_COMPLETED`。  
未孵化传 `false` → `409 ONBOARDING_INCOMPLETE`。

```json
{
  "client_message_id": "<本句稳定 UUID>",
  "content": "今天过得怎么样",
  "source": "text",
  "onboarding": false,
  "context": {
    "timezone": "Asia/Shanghai",
    "local_hour": 15,
    "weather": "clear",
    "city": null
  }
}
```

成功资源：

| 字段 | 含义 |
| :--- | :--- |
| `type` | `chat_turn` |
| `onboarding` | 回显本轮种类 |
| `generation_source` | `stub` 或 `provider` |
| `user_message` / `spirit_message` | 成对；失败不推进 round |
| `conversation_window_id` | 服务端窗口；不足 3 普通轮也可能是 **open** 窗口 |
| `should_extract` | **仅**窗口 `ready` 时为 true |
| `usage` | 单位计数；不要当计费 UI |

Composer：一条在途、最多三条 FIFO；达上限禁用发送。超时/失败 **原 ID 重试**。新句子新 UUID。  
本阶段 Chat UI **不要**加录音/TTS 控件。

未接线时进程内 Stub 短句（`嗯。` / `好。` / …）仍可用于本地单测。本机已接智能体后，**S04 与普通轮都是 `provider`**，看 `generation_source`，不要把 stub 文案当成真回复。

---

## 4. P10 真实 Chat 与 Extract

### 4.1 真实 Chat

本机 API 在配齐百炼 Key、Chat 模型别名、智能体 `APP_ID`（及可选 Workspace）后，factory 为 **provider**。  
**S04 五句与孵化后普通轮走同一智能体**；成功时都是 `generation_source=provider`。每句大约 12–18s。  
iOS **只看响应**：

- `generation_source === "provider"` → 可标真实陪伴（不要写具体模型名）  
- `"stub"` → 仍是占位，不得宣传百炼  

上游 Chat 约 **18s**（S04 与普通轮相同）；超时 `504 PROVIDER_TIMEOUT`（`retryable=true`），同一 `client_message_id` 重试。  
其它：`503 MODEL_UNAVAILABLE`、`429 RATE_LIMITED` / `QUOTA_EXCEEDED`。  

智能体应用 HTTP 200 但 `output.text` 是自然语言、或 JSON 带 extra 字段时：服务端只收下白名单（`reply` / `intent` / `citations` / `safety` / `search_query`），其余丢掉，**会落成有效回复**。`safety` 非 `allow` 仍 503。compatible-mode 的严格 JSON Schema **不变**。  
同一 `client_message_id` 重放跟当前 factory（已接智能体则为 `provider`，含 S04）。

`source_refs`（气泡引用，可空）：

| `type` | 形状 |
| :--- | :--- |
| `memory` / `pact` / `builtin_bank` | 仅 `id`，无 url |
| `web` | 仅 `https://` `url` + `fetched_at`，无 id |

Search **没有**客户端开关接口；`feature_flags.remote_search` 来自用户偏好默认值，**不等于**已经打了公开 Search。无可靠来源时回复会是固定句 **「这件事我还不知道」**，不要本地编来源。

### 4.2 窗口与 `should_extract`

- 一轮 = 已落库用户消息 + 已落库精灵回复  
- **普通**有效轮次计入 `ordinary_dialogue_rounds`；S04 onboarding **不计**  
- 每 **3** 个普通有效轮次 → 窗口 `ready`，该次 Chat 的 `should_extract=true`  
- onboarding 窗口不会 ready  

iOS：`should_extract=true` 时用返回的 `conversation_window_id` 触发 Extract。不要自己切 3 条消息当窗口。

### 4.3 `POST /api/v1/extract`

```json
{
  "client_id": "<Extract 专用稳定 UUID，不要复用 spirits / complete / 某句 chat>",
  "conversation_window_id": "<Chat 返回的 window id>"
}
```

禁止任何 message ID 字段（带了 → 422）。

| 窗口状态 | 行为 |
| :--- | :--- |
| `ready` / `failed` | 可抽 |
| `extracted` | **200 回放**原 memories / style / patch，不重复落库 |
| `extracting`（同一 client） | 200，`resource.status=processing` |
| `extracting`（其它 client） | `409 IDEMPOTENCY_IN_PROGRESS` |
| `open` / onboarding 窗口 | `422 INVALID_INPUT` |
| 非 owner | `404 NOT_FOUND` |

`resource.status`：`processing` | `extracted`。  
`memories` 最多 2 条，`style_samples` 最多 1 条。`confidence < 0.75` 的记忆服务端不会入库。  
`patch.memories_upsert` 合入 SessionStore；性格变化只信 patch 里的 spirit 字段。

Extract 上游约 **30s**；失败窗口可稍后用 **同一** `client_id` + `conversation_window_id` 再打。

本机 Extract 走百炼 **模型补全**（`extract/v2`），不是智能体应用。iOS 无差别，只消费 JSON。

---

## 5. 统一 HTTP 与错误码

```http
Authorization: Bearer <RS256 access_token>
Content-Type: application/json
X-Request-ID: <可选 UUID>
```

Envelope 同 P01–P07：用 `error.code` 映射 UI。

| HTTP | code | retryable | 处理 |
| ---: | :--- | :--- | :--- |
| 401 | `UNAUTHENTICATED` | false | 重新匿名登录；确认 RS256 |
| 404 | `NOT_FOUND` | false | 无精灵或窗口不属于你 |
| 409 | `CONFLICT` | false | 拉 bootstrap；Extract client_id 冲突 |
| 409 | `IDEMPOTENCY_CONFLICT` | false | 勿改已发稳定 ID |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | true | 同一 ID 稍后重试 |
| 409 | `ONBOARDING_INCOMPLETE` | false | 回 S04 |
| 409 | `ONBOARDING_ALREADY_COMPLETED` | false | 用 `onboarding=false` |
| 422 | `INVALID_INPUT` | false | 修 body（漏 onboarding、多传 message id、窗口未 ready） |
| 422 | `INVALID_CURSOR` | false | 丢掉 cursor，从无 cursor 重拉第一页 |
| 429 | `RATE_LIMITED` / `QUOTA_EXCEEDED` | 见 retryable | 同 ID 退避；配额满不要假成功 |
| 503 | `MODEL_UNAVAILABLE` | true | 同 ID 重试 |
| 504 | `PROVIDER_TIMEOUT` | true | 同 ID 重试 |
| 500 | `INTERNAL_ERROR` | false | 拉 bootstrap，勿本地假结算 |

| 操作 | 稳定 ID |
| :--- | :--- |
| 创建精灵 | `spirits.client_id` |
| 每一句 chat | `client_message_id` |
| onboarding complete | 另一个 `client_id` |
| 每一个 extract 窗口 | **又一个** `client_id`（每窗口一个；重试复用） |

---

## 6. 全链路时序（孵化之后）

```text
GET  /bootstrap                      status + room；settle 18h/72h
GET  /messages                       历史（可空）
POST /chat  onboarding=false  ×1     window 多为 open，should_extract=false
POST /chat  ×2
POST /chat  ×3                       should_extract=true，记下 conversation_window_id
POST /extract                        同一 window 打两次 → 第二次回放
GET  /bootstrap                      合入记忆后 latest_memories / spirit 性格
GET  /messages?cursor=...            继续向上翻
```

Chat 第 n 个普通成对会 bump `spirit.version`；Extract 成功也会 bump。以 patch / bootstrap 为准，不要本地 +1。

---

## 7. 人工联调清单

**P08**

- [ ] bootstrap：`hatched_at` 有值，`room` 非 null，`layers` 至少含主层 token  
- [ ] 无网：房间不清空，status 仍是上一帧的 home/study/away/lost  
- [ ] letter 若存在：只有 title_key + id，没有聊天正文  

**P09**

- [ ] `GET /messages` 无 cursor 200；乱改 cursor → `INVALID_CURSOR`  
- [ ] 普通 Chat 漏 `onboarding` → 422；`onboarding=true`（已孵化）→ `ONBOARDING_ALREADY_COMPLETED`  
- [ ] 同一 `client_message_id` 重放同一对消息  
- [ ] 发送队列：1 在途、第 4 条被挡住  

**P10**

- [ ] S04 五句与孵化后普通 Chat：均为 `generation_source=provider`；同 ID 重放仍是 `provider`  
- [ ] 日志 / 抓包不要出现百炼 Key、APP_ID、Prompt、完整 JWT  
- [ ] 第 3 个普通轮次 `should_extract=true`  
- [ ] Extract 两次：记忆条数不翻倍；`status=extracted`  
- [ ] 假 citation / `safety` 非 allow 不会变成已保存的精灵回复  

---

## 8. 本范围明确不做

- P11 记忆 list/correct/seal/delete、tombstone UI：见 [`p11-integration.md`](p11-integration.md)  
- P12 五种投喂与见闻上传  
- P13 配额硬停产品确认以外的本地结算  
- P14 ASR/TTS  
- 公开 Search 端点、客户端指定 window 边界  

常见坑：继续用 P07 的 OpenAPI SHA；把 fixture 里的 `generation_source=stub` 当成 live；用 Chat `patch.room` 当 S01；Extract 复用 spirits 的 `client_id`；联调时跑全量 pytest 清空库。

标准 JSON 以仓库 fixture 为准（与本文 SHA 同锁）：

- `fixtures/GET_api_v1_messages/*.json`
- `fixtures/POST_api_v1_extract/*.json`
- `fixtures/POST_api_v1_chat/*.json`
- `fixtures/GET_api_v1_bootstrap/success.json`

live 的 id、version、`generation_source`、`server_time` 以服务端为准。
