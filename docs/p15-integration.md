# 刻灵 P15 联调说明（iOS 接入）

面向 iOS ↔ 本机 FastAPI，覆盖 **S08 共学契约：create / 每日 session / 逐题 answer / skip / 末题内部 finalize**。  
前置：已按 [`p01-p07-integration.md`](p01-p07-integration.md) 孵化（`hatched_at` 有值）。房间/Chat 见 [`p08-p10-integration.md`](p08-p10-integration.md)。记忆（notes 主题要 active knowledge）见 [`p11-integration.md`](p11-integration.md)。成长 / 学者纹合入规则见 [`p13-integration.md`](p13-integration.md)。语音仍见 [`p14-integration.md`](p14-integration.md)。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机工作区 `3_ios/` 仍无 App 源码，本文给 **192.168.100.205** 上的 iOS 工程改（对应计划 **P15-T07 / T08**）。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `743b462becbdce5d2ea062632e776318277c05c36007bfa718867e40bad0bb27` |
| 题库 SHA-256 | `ece1b114f5017149ddf0b93bc46951f3858205ae0e038df3a9606ab151a7cab4`（`interview-v1`） |
| Alembic head | `20260908_0011`（P15 **未改库**） |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/`（与 SHA 同锁） |

P14 文档里的 SHA `cdb52009…` 在 **P15-T01 加四条 Pact 路由后作废**。Pact 解码请用上表。P13 文末「不要打 Pact」已被本文取代：本机 **可以**打下面四个端点。

P16 起公开契约继续加路由，OpenAPI SHA 换成 [`p16-p20-integration.md`](p16-p20-integration.md) 的 `fed40c13…`。社交 / 鉴定 / 设置 / 注销 / 找回以那一套为准；**不要**再把本文 SHA 当当前全量锁。进程模型也以 P16–P20 为准（现有独立 **scheduler / worker**）。

公开 API **没有** `POST /pacts/{id}/complete` 或任何同义 complete。完成只由使三题齐全的最后一次 `POST /pact-answer` 在服务端事务内 finalize 隐式发生。

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| P15-T01 契约 | **OpenAPI / fixture 已锁**。负断言无 complete 路由；公开题面 key 是 `text`，禁止 `prompt` | 四端点 + `fixtures/POST_api_v1_pact-*/` |
| P15-T02 Create | **已接线**。同时一个 active；notes 必须 owner + active knowledge | `POST /api/v1/pacts` |
| P15-T03 Session | **已接线**。日期用 **server timezone/day**；同日幂等；exactly 3 题 | `POST /api/v1/pact-session` |
| P15-T04 Answer | **已接线**。前两题 `finalized=false`；乱序 422；已答不可覆盖 | `POST /api/v1/pact-answer` |
| P15-T05 Finalize | **已接线**。末题同一事务评分 / 错题 / 完整度 / 成长；5 场且 ≥70 才 complete、bond、学者纹 | 最后一次 `POST /pact-answer` |
| P15-T06 Skip | **已接线**。不计 `completed_sessions`；降低 completeness；过期 `PACT_DAY_CLOSED` | `POST /api/v1/pact-skip` |
| P15-T07 / T08 | **本机不做** | iOS DTO / FIFO / S08 四 phase / UI |
| P15-T09 | **未做** | 跨端 7 日复查 |

Pact 联调仍打 `kelin-server-api`（`0.0.0.0:8000`）。当前 compose 已有独立 scheduler / worker，详见 [`p16-p20-integration.md`](p16-p20-integration.md)。改 Python / `.env` 后重启对应容器。

`GET /bootstrap` 的 `active_pact` 本机 **仍恒为 null**（聚合里有 `active_pact_id`，公开快照未填）。重启后的权威 pact 以 **create/session/answer/skip 的 `patch.pact`** 为准；不要等 bootstrap 回填。完成契约后的学者纹在 **末题 `patch.spirit.scholar_marks`**，以及之后的 bootstrap `spirit.scholar_marks` / `room.layers` 含 `scholar`。

**不要**在联调期间对该库跑会 `DROP SCHEMA` 的 db pytest（会清精灵/契约，需重新孵化）。

环境、JWT、ATS、IP 与 P01–P07 第 2 节相同：`API_BASE_URL=http://192.168.100.212:8000`。

---

## iOS 需要改什么

### 不要做

- 不要本地算 `score` / `completeness` / `scholar_mark` / `bond` / `finalized`  
- 不要发 `score`、`mark`、`finalized`、`completeness`（请求 `extra=forbid`，会 422）  
- 不要调用任何 complete 端点；不要做「完成契约」网络按钮  
- 不要用设备日历改 session 日；`session_date` 只能用服务端返回的 `resource.date` 或 **当天 server 日**（skip 的未来日期是 `422 INVALID_INPUT`）  
- 不要上传题库；不要改 `question_id`；公开字段是 `text` 不是 `prompt`  
- 不要用不同 `client_id` 覆盖同一题；不要乱序答第 2/3 题  
- 不要把 skip 当成完成场次；不要本地把 `completed_sessions` +1  
- 不要把 `patch.spirit` / `patch.room` 为 null 当成清房间或清学者纹  
- 不要把 fixture 里的 UUID / `score=82` / `snapshot_version` 钉死（样例；live 以响应为准）  
- 社交、鉴定卡、注销、找回不要按本文 SHA 解码；见 [`p16-p20-integration.md`](p16-p20-integration.md)

### 必须改：DTO 对齐 SHA

锁定 SHA：`743b462becbdce5d2ea062632e776318277c05c36007bfa718867e40bad0bb27`。

新增解码：

- `POST_api_v1_pacts/{success,business_error,missing_required_field,active_conflict,notes_not_found}.json`
- `POST_api_v1_pact-session/{success,business_error,missing_required_field,day_closed,idempotent_replay}.json`
- `POST_api_v1_pact-answer/{success,business_error,missing_required_field,last_question_finalize,already_answered,question_not_found,version_conflict,out_of_order,last_question_in_progress,provider_fallback}.json`
- `POST_api_v1_pact-skip/{success,business_error,missing_required_field,already_submitted,skip_replay,completeness}.json`

未知字段 / 缺字段要失败可观测。OpenAPI 路径表里 **不能出现** `/pacts/{id}/complete`。

题库：iOS `interview.json` 的 `question_id` 必须与服务端 `interview-v1` 一致。服务端文件 `4_server/app/domain/interview_bank.json`，SHA 上表。公开题只有 `question_id` + `text`。

### 必须改：SessionStore

Pact mutation **只合入**：

1. `data.patch`（`pact` 必有；`spirit` 仅在 `pact_status=completed` 时必有）  
2. 同包 `data.events`  
3. `data.quotas`（本机成功包经常是 `[]`；有则合入，不要本地编造 `capability=pact`）

合入规则与 P13 相同：

```text
patch.snapshot_version  <  本地 snapshot_version  →  丢弃，立刻 GET /bootstrap
patch.snapshot_version  >= 本地 snapshot_version  →  合入（同版本是幂等回放）
```

`patch.pact` 非空时整包替换本地 pact（含 `status` / `completed_sessions` / `skipped_sessions` / `completeness` / `scholar_mark`）。  
ViewModel **禁止**对 score / completeness / bond / scholar_marks 做乐观加减。  
`patch.room` 在 Pact 响应里恒为 null；学者层等下次 bootstrap，或 completed 时用 `patch.spirit.scholar_marks`。

### 必须改：离线 FIFO（T07）

每题一个稳定且不同的 `client_id`。重启后必须仍能读到：

| 保留 | 说明 |
| :--- | :--- |
| 每题 `client_id` | 重试同一题必须复用，禁止 `UUID()` |
| `session_id` | 来自 `POST /pact-session` |
| `expected_session_version` | 来自上一成功包的 `row_version` |
| `question_id` + 正文 | 按 session 题序 FIFO |

按序重放：q1 → q2 → q3。第 3 题成功包 `finalized=true`，解码 `score`、`pact_status`、`mistakes`、`patch.pact`。  
`IDEMPOTENCY_IN_PROGRESS`（`retryable=true`）稍后再用 **同一** `client_id` 重试。  
`PACT_QUESTION_ALREADY_ANSWERED`：该题已有权威答案，不要覆盖，跳到下一题或拉 session。  
`CONFLICT`（版本）：用最新 `row_version` 重放，不要改正文。

### 必须改：S08 四 phase（T08）

用 **服务端** `pact.status` + 当日 session `status`，不要本地推算完成：

```text
unsigned          无 active pact（create 前；或已 completed/abandoned 且未新开）
active            pact.status=active，且今日 session 不是 answered/skipped
todayCompleted    今日 session 已 answered 或 skipped（pact 仍可 active）
completed         pact.status=completed（5 场且 completeness≥70 之后）
```

跳过确认固定文案：**「今天不学了？」** 说明：**「完整度会下降，但不会伤害关系。」** 确认后再 `POST /pact-skip`。

---

## 1. P0 能力：现在能联什么

| P0 能力 | 本机状态 | 联调注意 |
| :--- | :--- | :--- |
| `POST /pacts` | **可联** | 同时一个 active |
| `POST /pact-session` | **可联** | server day；exactly 3 |
| `POST /pact-answer` | **可联** | 前两题不 finalize；末题才 finalize |
| `POST /pact-skip` | **可联** | 不计完成场次 |
| 无 complete 路由 | **已锁** | 客户端禁止再调 |
| 题库 `interview-v1` | **服务端内置** | hash 上表 |
| `bootstrap.active_pact` | **本机恒 null** | 用 patch.pact |
| 7 日 5 场 70 + skip 边界 | **服务端已测** | iOS 不要本地复算 |
| S08 UI / 离线队列 | **iOS 未交** | T07–T08 |
| 跨端 7 日复查 | **未做** | T09 |

---

## 2. `POST /api/v1/pacts`

JSON，`extra=forbid`。

| 字段 | 规则 |
| :--- | :--- |
| `client_id` | 创建幂等键；重试不要换 |
| `theme` | 只允许 `interview` \| `notes` |
| `title` | 1…80 |
| `notes_memory_id` | `notes` 必填；`interview` 不要带 |

`notes`：memory 必须是 **当前用户**、`type=knowledge`、`status=active`。否则 `404 NOT_FOUND`（fixture `notes_not_found`）。  
已有 active → `409 PACT_ALREADY_ACTIVE`（`retryable=false`）。

成功 `data.resource` / `data.patch.pact`：`status=active`，`question_bank_version=interview-v1`（或 `notes-v1`），`completed_sessions=0`，`completeness=0`。  
`events` 含 `pact.created`。`patch.spirit` 为 null。

---

## 3. `POST /api/v1/pact-session`

请求只有 `client_id` + `pact_id`。

- 日期 = 服务端 timezone 的当天，**不是**设备日历  
- 同日再请求（可换 `client_id`）→ **同一** `session.id`  
- `resource.questions` **恰好 3 条**：`question_id` + `text`  
- `row_version` 新场为 `1`；之后每成功答一题 +1  
- 周期结束 / 第 8 天 → `409 PACT_DAY_CLOSED`

同一 `client_id` 拿到另一天会 `IDEMPOTENCY_CONFLICT`。换日请换新 `client_id`。

`explain` 给一屏文案。不要把 `questions[].text` 改成 prompt 字段。

---

## 4. `POST /api/v1/pact-answer`

```json
{
  "client_id": "<每题稳定 UUID>",
  "session_id": "<session.id>",
  "expected_session_version": 1,
  "question_id": "interview-v1-q1",
  "text": "……"
}
```

| 题序 | `expected_session_version` | 成功后 `row_version` | `finalized` |
| ---: | ---: | ---: | :--- |
| 第 1 题 | 1 | 2 | `false`；`score`/`pact_status` 为 null |
| 第 2 题 | 2 | 3 | `false`；`completed_sessions` 仍不变 |
| 第 3 题 | 3 | 4 | `true`；必有 `score`、`pact_status`、`patch.pact` |

前两题 `feedback.summary` 可能是模板「方向清楚，可以再具体一点。」；Provider 失败降级「先把这件事讲完整，再补一句结果。」（`finalized` 仍为 false）。  
第三题公开 feedback 固定为 **「三题已齐，本场结束。」**（场次级，不是逐题点评）。

`pact_status=completed` 时：`patch.spirit` 必有，`scholar_marks` 含 `interview-v1`，`bond` 已 +5（本机默认 0→5）。`events` 含 `pact.completed`（同场还有 `pact.answered`）。  
仅一场结束、未达 5×70：`pact_status=active`，`patch.spirit` 为 null，`completed_sessions=1`。

| 情况 | HTTP | code |
| :--- | ---: | :--- |
| 乱序（先答第 2 题） | 422 | `INVALID_INPUT` |
| 题不属于本场 | 404 | `PACT_QUESTION_NOT_FOUND` |
| 同题换 `client_id` 或换正文 | 409 | `PACT_QUESTION_ALREADY_ANSWERED` |
| `expected_session_version` 过期 | 409 | `CONFLICT` |
| 同题并发未完成 | 409 | `IDEMPOTENCY_IN_PROGRESS`（可重试） |
| 场次已 skipped / 非 ready | 409 | `CONFLICT` |

---

## 5. `POST /api/v1/pact-skip`

请求：`client_id` + `pact_id` + `session_date`。

- `session_date` 必须 ≤ 服务端今天；未来日 `422 INVALID_INPUT`  
- 不在 7×24h 窗口 / pact 已结束 → `409 PACT_DAY_CLOSED`  
- 今日已 `answered` → `409 PACT_SESSION_ALREADY_SUBMITTED`  
- 重复 skip（同 `client_id` + 同日）→ 同一 `resource.id`，`skipped_sessions` **不**再 +1  
- `completed_sessions` **不增加**；`completeness` 会下降（首 skip 从 0 落到 50）

与末题并发：终态只能是 `answered` **或** `skipped` 之一。skip 赢了再答末题是 `CONFLICT`；answer 赢了再 skip 是 `PACT_SESSION_ALREADY_SUBMITTED`。

---

## 6. 完成规则（只信服务端）

契约 `status=completed` 当且仅当：**7×24h 内 `completed_sessions ≥ 5` 且 `completeness ≥ 70`**。

| 构造（服务端已测） | 结果 |
| :--- | :--- |
| 4 场满分 | 仍 `active` |
| 5 场完整度 69 | 仍 `active` |
| 5 场完整度 70 | `completed`；bond +5；`scholar_marks` 含 `interview-v1` |
| skip | 不算完成场次 |

客户端不要用本地平均分去预判能不能完成。

---

## 7. 建议联调顺序

1. 孵化完成（`hatched_at` 有值）  
2. `POST /pacts` 无 JWT → `401`  
3. `theme=interview` create → `active`；再 create → `PACT_ALREADY_ACTIVE`  
4. `POST /pact-session` → `date` 为 server 日、`questions.length==3`、`row_version=1`  
5. 再 session（新 `client_id`）→ **同一** session id  
6. 改设备日期再 session → **日不变**  
7. 答第 1 题并重放同一 `client_id` → 同一 feedback；换 ID 改正文 → `PACT_QUESTION_ALREADY_ANSWERED`  
8. 答第 2 题 → `answered_count=2`，`finalized=false`，`completed_sessions=0`  
9. 答第 3 题 → `finalized=true`，有 `score` / `pact_status` / `patch.pact`  
10. 另一天 skip（先确认文案）→ `skipped`，`completed_sessions` 不变  
11. 已答日 skip → `PACT_SESSION_ALREADY_SUBMITTED`  
12. 刷新 bootstrap：`active_pact` 可能仍是 null；`scholar_marks` 只在契约 completed 后出现  

---

## 8. 错误码（用 `error.code` 映射 UI）

| code | 典型 UI |
| :--- | :--- |
| `PACT_ALREADY_ACTIVE` | 已有进行中的契约 |
| `PACT_DAY_CLOSED` | 今天不能再开场 / 本周已结束 |
| `PACT_QUESTION_ALREADY_ANSWERED` | 这题已经交过 |
| `PACT_QUESTION_NOT_FOUND` | 题目不属于本场 |
| `PACT_SESSION_ALREADY_SUBMITTED` | 今天已经答完，不能跳过 |
| `CONFLICT` | 版本过期或场次已结束，按最新 `row_version` 恢复 |
| `IDEMPOTENCY_IN_PROGRESS` | 稍候用同一 `client_id` 重试 |
| `IDEMPOTENCY_CONFLICT` | 不要把同一 client_id 用到另一天/另一 payload |
| `INVALID_INPUT` | 乱序、缺字段、skip 未来日 |
| `NOT_FOUND` | 他人 pact/session，或 notes memory 不可用 |
| `MODEL_UNAVAILABLE` | 前两题点评不可用；本机仍可能用模板成功返回 |

英文 `message` 是 fixture 用的，不要直接展示。

---

## 9. 人工联调清单

- [ ] Pact 解码 SHA 是 `743b462b…`，不是 P14 的 `cdb52009…`；当前全量锁是 P16–P20 的 `fed40c13…`  
- [ ] 路由表无 `/pacts/{id}/complete`；客户端无 complete 请求  
- [ ] 题库 `question_id` 与 `interview-v1` / SHA `ece1b114…` 一致  
- [ ] 公开题面只有 `text`，没有 `prompt`  
- [ ] 每题独立 `client_id`；重启后 FIFO 仍按序重放  
- [ ] 前两题 `finalized=false`；第三题才有 `score` / `pact_status`  
- [ ] skip 确认文案固定；skip 后 `completed_sessions` 不增加  
- [ ] `patch.spirit=null` 不丢房间；completed 才合入 `scholar_marks`  
- [ ] 不本地加减 score / completeness / bond  
- [ ] 日志无 JWT、无答案正文、无 Prompt  
- [ ] 不宣称 T07/T08 UI 或 T09 真机 7 日复查已完成（除非实际做到）  

---

## 10. 本范围明确不做

- 在本机 `3_ios/` 改工程（到 192.168.100.205 做 T07/T08）  
- 改 OpenAPI 字段（SHA 已锁）或新增 migration  
- 把 `GET /bootstrap.active_pact` 填上（本机仍为 null）  
- 为 Pact 成功包编造 `quotas`（live 常为 `[]`）  
- 把 fixture 的 `score=82` 当成唯一合法分数  
- 宣布阶段验收通过或已部署  

常见坑：继续用 P14 SHA；本地算分；给第 3 题换新 `client_id`；用设备日期开 session；skip 后把完成场次 +1；去打不存在的 complete；联调时跑 db pytest 清空库。

标准 JSON 以仓库 fixture 为准（与本文 SHA 同锁）。live 的 id、`score`、`completeness`、`snapshot_version`、`server_time` 以服务端为准。
