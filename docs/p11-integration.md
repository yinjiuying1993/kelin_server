# 刻灵 P11 联调说明

面向 iOS ↔ 本机 FastAPI，覆盖 **S07 记忆列表 / 纠正 / 封存 / 删除 / 全清**，以及封存删除后 **不再召回、SourceBadge 消失**。  
前置：已按 [`p01-p07-integration.md`](p01-p07-integration.md) 孵化，并按 [`p08-p10-integration.md`](p08-p10-integration.md) 打通过 `POST /chat` + `POST /extract`，库里已有 Extract 落库的记忆。  
P12 五类投喂 / 见闻见 [`p12-integration.md`](p12-integration.md)（OpenAPI SHA 已换）。  
P13 成长 / 配额见 [`p13-integration.md`](p13-integration.md)。  
P14 语音契约见 [`p14-integration.md`](p14-integration.md)（OpenAPI SHA 已换）。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机工作区 `3_ios/` 仍无 App 源码，本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `5aea0b23c6f4f8a49528acc05c8dd9b744a0bcee7a4c1c33332a509b8a9326d6` |
| Alembic head | `20260908_0007` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/`（与 SHA 同锁） |

P08–P10 文档里的 SHA `f46da36ea05c7eb3a7d9a87ba9cd92c28ea4662a68c9986a9a41abcbcbe48843` 是加记忆四端点 **之前** 的锁。P11 解码请用上表 SHA。T02–T04 **没有**改字段，SHA 与 T01 相同。

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| P11-T01 契约 | **已锁定**。四端点在真实 OpenAPI | `app/schemas/memory.py`、`fixtures/` |
| P11-T02 列表 | **已接线**。owner keyset，五筛选，无 OFFSET | `app/api/v1/memories.py`、`app/domain/memory_list.py` |
| P11-T03 mutation | **已接线**。correct / seal / delete / clear all，`client_id` 幂等 | `app/services/memory.py` |
| P11-T04 不召回 | **已接线**。sealed/deleted 退出 Prompt、引用展示、报告候选 | `app/repositories/memory_recall.py`、`app/services/messages.py` |

进程仍是 `kelin-postgres-test` + `kelin-server-api`（`0.0.0.0:8000`）。改 Python 后 `docker restart kelin-server-api`。

**不要**在联调期间对该库跑会 `DROP SCHEMA` 的全量 pytest。

环境、JWT、ATS、IP 与 P01–P07 第 2 节相同：`API_BASE_URL=http://192.168.100.212:8000`。

---

## iOS 需要改什么

### 不要做

- 不要直连 Postgres；不要把 JWT、记忆正文打进日志  
- 不要用 offset / 页码；cursor 当不透明字符串，前缀是 `kelin.mem.v1.`  
- 不要把 messages 的 cursor 塞进 `/memories`  
- 不要换筛选后继续用旧 cursor  
- 不要改 salience / confidence / type / personality delta（PATCH 只允许 correct 的 `summary` 或 seal）  
- 不要离线假装全清成功；`DELETE /memories` 必须在线  
- 不要在重试时换 `client_id`  
- 不要用 `GET /bootstrap` 的 `latest_memories` 当 S07 数据源（本机仍返回 `[]`）  
- 不要把 Extract 回放里的 memories 当成「当前仍 active」；S07 以 `GET /memories` + mutation `patch` 为准  
- 不要打 Pact、社交、通知、鉴定卡、注销（P14+）；Feed / 见闻见 [`p12-integration.md`](p12-integration.md)；成长/配额见 [`p13-integration.md`](p13-integration.md)

### 必须改：DTO 对齐 SHA

锁定 SHA：`5aea0b23c6f4f8a49528acc05c8dd9b744a0bcee7a4c1c33332a509b8a9326d6`。  
新增解码：

- `GET_api_v1_memories/{success,business_error,missing_required_field,page_empty,page_first,page_middle,page_last,invalid_or_expired_cursor,filter_mismatch_cursor}.json`
- `PATCH_api_v1_memories_{memory_id}/{success,business_error,missing_required_field,version_conflict,idempotency_conflict,not_found,illegal_state}.json`
- `DELETE_api_v1_memories_{memory_id}/{success,business_error,missing_required_field,version_conflict,idempotency_conflict,not_found}.json`
- `DELETE_api_v1_memories/{success,business_error,missing_required_field,idempotency_conflict}.json`

未知字段 / 缺字段要失败可观测。fixture 的 UUID、时间、`snapshot_version` 是样例，**live 以响应为准**。

`SessionStore`：列表分页按 `id` 去重合并；mutation **只合入** `patch.memories_upsert` / `patch.memory_tombstones`；全清看 `resource.type=memory_clear`，清空本地记忆缓存（含 sealed）。旧 bootstrap 快照 **不得**把已删/已封记忆救活。

### 必须改：AppPhase

```text
ready（hatched_at 有值）
  → S01 房间
  → S05 Chat / Extract
  → S07 刻痕：GET /memories?filter=...
       PATCH /memories/{id}   correct | seal
       DELETE /memories/{id}  单条软删
       DELETE /memories       全清（在线）
```

空态固定文案 **「还没有刻痕」**，并提供去 Chat 的入口。不要本地编记忆。

### 建议联调顺序

1. Extract 成功后 `GET /memories?filter=all` 能看到 `status=active` 的条目  
2. `filter=relationship` / `knowledge` / `speech` / `sight`；换 filter 必须丢掉旧 cursor  
3. `PATCH` correct → 列表 summary 变、`version+1`；同一 `client_id` 重放不双写  
4. `PATCH` seal → 列表再也拉不到这条；回 Chat，旧气泡的 memory `source_refs` 消失  
5. `DELETE /{id}` → `tombstones` 只有 `id` + `deleted_at`，无 summary  
6. 无网时禁用全清；联网 `DELETE /memories` + `confirm=CLEAR_ALL_MEMORIES`

---

## 1. P0 能力：现在能联什么

| P0 能力 | 阶段 | 本机状态 | 联调注意 |
| :--- | :--- | :--- | :--- |
| `GET /api/v1/memories` | P11 | **可联** | `filter` 必填；keyset |
| `PATCH /api/v1/memories/{id}` | P11 | **可联** | 仅 `correct` / `seal` |
| `DELETE /api/v1/memories/{id}` | P11 | **可联** | **DELETE 带 JSON body** |
| `DELETE /api/v1/memories` | P11 | **可联** | 必须在线；`confirm` 精确匹配 |
| Chat / 消息 `source_refs` | P11-T04 | **可联** | 读取时丢掉 sealed/deleted 的 memory 引用 |
| 下一轮 Chat Prompt | P11-T04 | **服务端已过滤** | iOS 无新字段；不要本地塞记忆进请求 |
| 报告候选 | P11-T04 | **无公开报告 API** | 不要猜 `GET /report` |

仍不要打：ASR/TTS、Pact、社交、通知、注销。Feed 见 [`p12-integration.md`](p12-integration.md)。

---

## 2. `GET /api/v1/memories`（S07 列表）

```http
GET /api/v1/memories?filter=all&limit=30
GET /api/v1/memories?filter=relationship&cursor=<next_cursor>&limit=30
Authorization: Bearer <access_token>
```

| 查询 | 规则 |
| :--- | :--- |
| `filter` | **必填**：`all` \| `relationship` \| `knowledge` \| `speech` \| `sight` |
| `cursor` | 不透明签名 keyset；不要当 SQL、不要手改 |
| `limit` | 1–50，默认 30 |

`relationship` **服务端映射** `preference` + `relation` + `emotion`。不要自己再并一次。

排序：`created_at desc, id desc`。同一分页会话用响应里的 `snapshot_at` 当上界。

成功 `data`：

```json
{
  "items": [ /* MemoryPublic，仅 active */ ],
  "tombstones": [ /* { "id", "deleted_at" } */ ],
  "next_cursor": "<kelin.mem.v1.… 或 null>",
  "has_more": false,
  "snapshot_at": "2026-09-11T00:00:00Z"
}
```

`has_more=true` 时必有 `next_cursor`；末页 `has_more=false` 且 `next_cursor=null`。

无精灵 / 尚无记忆：**200 空页**（`items=[]`，`tombstones=[]`，`next_cursor=null`），不是 404。

### 2.1 `MemoryPublic`（仅 `items`）

| 字段 | 规则 |
| :--- | :--- |
| `id` | 服务端记忆 ID |
| `type` | `preference` \| `knowledge` \| `emotion` \| `relation` \| `speech` \| `sight` |
| `summary` | 1–500 |
| `tags` | string[] |
| `salience` | 0–100，只读 |
| `confidence` | 0–1，只读 |
| `status` | `items` 里只会是 **`active`** |
| `version` | ≥1；PATCH/DELETE 用这个当 `expected_version` |
| `created_at` | UTC `...Z` |

### 2.2 三种状态怎么出现

| status | `GET` `items` | `GET` `tombstones` | 本地缓存 |
| :--- | :--- | :--- | :--- |
| `active` | 有正文 | 无 | 展示 |
| `sealed` | **无** | **无** | 靠 seal 的 `patch.memories_upsert`（`status=sealed`）隐藏；再拉列表也不会回来 |
| `deleted` | **无** | 只有 `id` + `deleted_at` | 按 tombstone 删除；**不要展示 summary** |

tombstone **禁止**带 summary / type / tags。解码到这些字段应失败可观测。

### 2.3 cursor 失效

换 `filter`、换账号、篡改、过期 → `422 INVALID_CURSOR`。丢掉 cursor，从该 filter 无 cursor 重拉第一页。  
消息列表的 `kelin.msg.v1.` cursor 用在记忆上同样 `INVALID_CURSOR`。

---

## 3. Mutation（纠正 / 封存 / 删除 / 全清）

统一：

- Envelope 同其它 mutation：`data.resource` + `data.patch`  
- 合入 `patch.snapshot_version`；`patch.spirit` 本机常为 **null**（只 bump 版本，列表不必等 spirit 整包）  
- 每个操作一个 **稳定 `client_id`**（UUID）。超时/失败 **原 ID 重试**。新操作新 UUID  
- 不要复用 spirits / chat / extract / onboarding 的 `client_id`  
- 同 `client_id` 不同 body → `409 IDEMPOTENCY_CONFLICT`  
- 越权或 id 不存在 → `404 NOT_FOUND`（不要提示「属于别人」）

`DELETE` **必须带 JSON body**。iOS `URLSession` 要显式设 `httpBody`，不要用只能发 query 的 DELETE 封装。

### 3.1 `PATCH /api/v1/memories/{memory_id}`

纠正：

```json
{
  "client_id": "<本操作稳定 UUID>",
  "expected_version": 1,
  "action": "correct",
  "summary": "请叫我阿年"
}
```

`summary` 必填，1–500 字。只能改 summary 和 `version`。

封存：

```json
{
  "client_id": "<另一个稳定 UUID>",
  "expected_version": 2,
  "action": "seal"
}
```

**不要**带 `summary`（带了 → 422）。

成功：

- `resource.type=memory`，`status` 为 `active`（correct）或 `sealed`（seal）  
- `patch.memories_upsert` 含完整 `MemoryPublic`  
- `patch.memory_tombstones` 为空  

对已封存 / 已删除再 correct 或 seal → `409 MEMORY_NOT_ACTIVE`。  
`expected_version` 对不上 → `409 CONFLICT`。拉最新 `version` 后再决定是否重试（**新 client_id**）。

封存会停用该记忆来源窗口对应的 style sample。iOS 无单独 style API，不必展示样本。

### 3.2 `DELETE /api/v1/memories/{memory_id}`

```json
{
  "client_id": "<本操作稳定 UUID>",
  "expected_version": 2
}
```

成功：`resource.status=deleted`，`patch.memories_upsert=[]`，`patch.memory_tombstones=[{id, deleted_at}]`。  
已删除再删：**仍 200**，tombstone 的 `deleted_at` 不变。  
`expected_version` 只约束 **尚未删除** 的那一次；已删除的重复删除不因 version 再 409。

### 3.3 `DELETE /api/v1/memories`（全清，必须在线）

```json
{
  "client_id": "<全清专用稳定 UUID>",
  "confirm": "CLEAR_ALL_MEMORIES"
}
```

`confirm` 必须 **整串相等**。缺了或写成别的 → 422。

成功：

```json
{
  "resource": {
    "type": "memory_clear",
    "id": "<就是请求的 client_id>",
    "version": 1
  },
  "patch": {
    "snapshot_version": 12,
    "memories_upsert": [],
    "memory_tombstones": []
  }
}
```

**不要**等 tombstones 数组来清缓存：全清的 patch 里 tombstones 为空。本地应：

1. 识别 `resource.type == "memory_clear"`  
2. 清空该账号全部 CachedMemory / sealed / tombstone  
3. 列表回到空态「还没有刻痕」

不清 messages。旧聊天气泡还在，但 memory 类 SourceBadge 再拉历史时会被服务端滤掉。  
全清会停用 **全部** style sample。失败整单回滚，不要本地先清空再请求。

无网：禁用全清按钮，不要排队成「已成功」。

---

## 4. 不召回与 SourceBadge（P11-T04）

服务端事实：

1. 下一轮 Chat Prompt **只注入 active** 记忆和 active 风格样本  
2. 模型引用 sealed/deleted/他人 id → 该轮不落库（`503 MODEL_UNAVAILABLE`），不要把半句当成功  
3. `GET /messages` 与 Chat **重放**时，memory 类 `source_refs` 若已封存/删除则 **从响应里拿掉**  
4. 报告生成尚未接线；不要实现鉴定卡候选  
5. 内部 Search 不读 `memories` 表；仍然没有 `POST /search`

iOS：

- SourceBadge **只信当前响应**里的 `source_refs`，不要用本地记忆缓存去「补」徽标  
- 封存/删除成功后，已打开的 Chat 时间线应按最新 `GET /messages` 或本地：对该 id 的 memory 引用隐藏徽标  
- 不要把 sealed 摘要再拼进下一条 Chat 请求（请求里本来也没有记忆字段）

---

## 5. 统一 HTTP 与错误码

```http
Authorization: Bearer <RS256 access_token>
Content-Type: application/json
X-Request-ID: <可选 UUID>
```

| HTTP | code | retryable | 处理 |
| ---: | :--- | :--- | :--- |
| 401 | `UNAUTHENTICATED` | false | 重新匿名登录 |
| 404 | `NOT_FOUND` | false | 无精灵，或记忆不属于你 / 不存在 |
| 409 | `CONFLICT` | false | 版本冲突；拉列表取新 `version` |
| 409 | `MEMORY_NOT_ACTIVE` | false | 已封存/删除，不能再 correct/seal |
| 409 | `IDEMPOTENCY_CONFLICT` | false | 同 client_id 改了 body；不要复用该 ID |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | true | 同一 client_id 稍后重试 |
| 422 | `INVALID_INPUT` | false | 漏 filter、漏 summary、confirm 不对、seal 带了 summary |
| 422 | `INVALID_CURSOR` | false | 丢掉 cursor，无 cursor 重拉 |
| 503 | `DEPENDENCY_UNAVAILABLE` | true | 稍后重试；不要本地假写 |

| 操作 | 稳定 ID |
| :--- | :--- |
| 每一句 chat | `client_message_id` |
| 每一个 extract 窗口 | Extract `client_id` |
| 每一次 correct / seal / 单删 / 全清 | **各自**一个 `client_id` |

---

## 6. 全链路时序（Extract 之后）

```text
POST /extract                         窗口 extracted，最多 2 条记忆
GET  /memories?filter=all             items 为 active
GET  /memories?filter=relationship    preference/relation/emotion
PATCH /memories/{id}  action=correct  patch.memories_upsert
PATCH /memories/{id}  action=seal     列表再拉不到；Chat 徽标消失
DELETE /memories/{id}                 tombstone 无正文
GET  /messages                        旧引用的 memory id 被滤掉
POST /chat  onboarding=false          下一轮不召回已封存/删除内容
DELETE /memories  confirm=CLEAR_ALL   本地整表清空；消息还在
GET  /bootstrap                       latest_memories 仍可能是 []
```

---

## 7. 人工联调清单

**列表**

- [ ] 漏 `filter` → 422  
- [ ] `filter=all` 200；空库/无精灵也是 200 空页  
- [ ] `relationship` 能看到 preference/emotion/relation，看不到 knowledge  
- [ ] 乱改 cursor / 换 filter 沿用旧 cursor → `INVALID_CURSOR`  
- [ ] `items` 无 `deleted`；tombstone 无 summary  

**mutation**

- [ ] correct 后 summary 变、`version` +1；salience/type 不变  
- [ ] 同一 `client_id` 再 PATCH 不双写  
- [ ] 改 summary 却复用旧 `client_id` → `IDEMPOTENCY_CONFLICT`  
- [ ] 旧 `expected_version` → `CONFLICT`  
- [ ] 对 sealed 再 correct/seal → `MEMORY_NOT_ACTIVE`  
- [ ] B 的 token PATCH A 的 id → 404，A 数据不变  
- [ ] DELETE 带 body；tombstone 只有 id/deleted_at  
- [ ] 已删再删仍 200  
- [ ] 全清必须 `confirm=CLEAR_ALL_MEMORIES`；离线不能点成成功  
- [ ] 全清后列表空；style 不再进下一轮（用 Chat 观察即可，无独立 API）  

**不召回**

- [ ] 封存后回 Chat：该记忆 SourceBadge 消失  
- [ ] 再问已封存内容：新回复不应再引用该 id  
- [ ] 日志 / 抓包无 JWT 全文、无记忆 summary 明文（除 HTTPS 请求体本身）  

---

## 8. 本范围明确不做

- P11-T05+ iOS Domain/缓存/S07 UI（在 192.168.100.205 工程做，不在本机 `3_ios/`）  
- P12 投喂与见闻（见 [`p12-integration.md`](p12-integration.md)）  
- P14 ASR/TTS  
- 公开 Search、客户端指定 window 边界  
- 用 bootstrap `latest_memories` 撑 S07  
- 改 OpenAPI 字段（SHA 已锁）  

常见坑：继续用 P08–P10 的 OpenAPI SHA；DELETE 没带 body；全清靠 tombstones 数组（实际为空）；把 sealed 当成 tombstone；离线全清假成功；Extract 回放当现行列表；联调时跑全量 pytest 清空库。

标准 JSON 以仓库 fixture 为准（与本文 SHA 同锁）：

- `fixtures/GET_api_v1_memories/*.json`
- `fixtures/PATCH_api_v1_memories_{memory_id}/*.json`
- `fixtures/DELETE_api_v1_memories_{memory_id}/*.json`
- `fixtures/DELETE_api_v1_memories/*.json`

live 的 id、version、`snapshot_at`、`server_time` 以服务端为准。
