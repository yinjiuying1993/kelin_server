# 刻灵 P13 联调说明（iOS 接入）

面向 iOS ↔ 本机 FastAPI，覆盖 **成长账本、进化、18h/72h 状态、日配额、成本脱敏、权威 patch**。  
前置：已按 [`p01-p07-integration.md`](p01-p07-integration.md) 孵化（`hatched_at` 有值）。房间/Chat 见 [`p08-p10-integration.md`](p08-p10-integration.md)；记忆见 [`p11-integration.md`](p11-integration.md)；五类投喂见 [`p12-integration.md`](p12-integration.md)。  
P14 语音契约见 [`p14-integration.md`](p14-integration.md)。P15 共学 Pact 见 [`p15-integration.md`](p15-integration.md)（**SHA 已换**）。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机工作区 `3_ios/` 仍无 App 源码，本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `cdb52009a4ec253303a483f7a706d0439bc3fae8c728f146bbb7fa89fec71ae2` |
| Alembic head | `20260908_0011` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/`（与 SHA 同锁） |

P13 **没有新公开写接口**。P15-T01 起解码改用 [`p15-integration.md`](p15-integration.md) 的 SHA。P08/P11/P13/P14 文档里的旧 SHA 不要再用。

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| P13-T01 事件目录 | **已接线**。客户端只交事实，不交 delta | `app/domain/growth.py` |
| P13-T02 成长账本 | **已接线**。`(source_type, source_id, event_type)` 一次结算，属性 clamp 0…100 | `app/services/growth.py` |
| P13-T03 进化 / 学者纹 | **已接线**。`whelp→formed→awake` 单向；学者纹去重。Pact 授纹见 [`p15-integration.md`](p15-integration.md) | `app/services/evolution.py` |
| P13-T04 状态 settle | **已接线**。bootstrap 前置 settle；18h/72h 用 **server time** | `app/services/spirit_state.py` |
| P13-T05 原子配额 | **已接线**。条件 UPDATE；429 带 `quota` + `reset_at` | `app/services/quota.py` |
| P13-T06 成本 | **已接线**。`ai_usage` 无 Prompt；100% **只告警不硬停**。iOS 看不到此表 | `app/services/ai_usage.py` |
| P13-T07 权威 patch | **已接线**。status/stage/marks/quotas 进 Bootstrap 与 mutation | `app/api/v1/*_map.py` |
| P13-T08 复查 | **测试已跑**。scheduler 须真跑；本机 **无**独立 worker 容器 | `tests/db/test_p13_t08_review.py` |

进程仍是 `kelin-postgres-test` + `kelin-server-api`（`0.0.0.0:8000`）。无独立 scheduler/worker。改 Python 后 `docker restart kelin-server-api`。

**不要**在联调期间对该库跑会 `DROP SCHEMA` 的全量 pytest（会清精灵/记忆，需重新孵化）。T08 复查刚跑过 db 测试时，**必须先重新孵化**再联。

环境、JWT、ATS、IP 与 P01–P07 第 2 节相同：`API_BASE_URL=http://192.168.100.212:8000`。

---

## iOS 需要改什么

### 不要做

- 不要直连 Postgres；不要本地算成长 / 状态 / 配额 / 学者纹  
- 不要在任何请求里提交 `hunger_delta`、`bond`、`stage`、`status`、`scholar_marks`  
- 不要用设备时间、`local_hour`、日历日去 settle 18h/72h 或刷新配额  
- 不要把 `offline` 写成 `spirit.status`  
- 不要用 Chat / Extract 的 `patch.room`（本机 **仍为 null**）当丢房间；房间以 bootstrap / Feed patch 为准  
- 不要把 `room.layers` 里的 `interview-v1` 当层名；学者纹层 token 只有 `scholar`，mark 在 `spirit.scholar_marks`  
- 不要 `patch.snapshot_version` 比本地旧还强行合入  
- 不要打社交、鉴定卡、注销、找回（P16 / P20）。ASR/TTS 见 [`p14-integration.md`](p14-integration.md)；Pact 见 [`p15-integration.md`](p15-integration.md)

### 必须改：DTO 对齐 SHA

锁定 SHA：`cdb52009a4ec253303a483f7a706d0439bc3fae8c728f146bbb7fa89fec71ae2`（P14-T01 起；见 [`p14-integration.md`](p14-integration.md)）。  
P13 没有新 fixture 目录。继续解码：

- `GET_api_v1_bootstrap/`（`spirit.status/stage/scholar_marks`、`room.layers`、`quotas`）
- `POST_api_v1_chat/`、`POST_api_v1_extract/`（`patch.spirit`；`patch.room` 为 null）
- `POST_api_v1_feed/` 与 Promise 三端点（`patch.spirit` + `patch.room` + `quotas`）
- `POST_api_v1_onboarding_complete/`（孵化后 `room.layers` 应为 `["spirit"]`）

未知字段 / 缺字段要失败可观测。fixture 的 UUID、时间、`snapshot_version` 是样例，**live 以响应为准**。

### 必须改：SessionStore

只接受两种更新：

1. `GET /bootstrap` **整包替换**  
2. 写接口返回的 `data.patch` + 同包 `data.quotas`

合入规则（规格 §5.5）：

```text
patch.snapshot_version  <  本地 snapshot_version  →  丢弃，立刻 GET /bootstrap
patch.snapshot_version  >= 本地 snapshot_version  →  合入（同版本是幂等回放）
```

`patch.spirit` 非空时，`spirit.version` 应等于 `patch.snapshot_version`。  
ViewModel **禁止**对 `bond/hunger/status/stage/quota.used` 做乐观加减。  
`RoomRules` 只把 `room.layers` 的 token 映射成素材，不重算成长。

### 必须改：AppPhase

```text
ready（hatched_at 有值）
  → S01 房间：只显示 bootstrap/feed 的 status + layers + quotas
  → S05 Chat / S07 刻痕 / S06 五卡：合入 patch，不本地结算
```

配额文案用 `quotas[].used/limit/reset_at`。超限只展示服务端 429，不要本地再扣一次。

### 建议联调顺序

1. 重新孵化 → `GET /bootstrap`：`hatched_at` 有值、`status=home`、`stage=whelp`、`scholar_marks=[]`、`room.layers` 含 `spirit`  
2. **food** → `patch.spirit.hunger/energy` 只信服务端；`quotas` 含 `food`（limit **3**）  
3. 同一 `client_id` 重放 food → 属性与配额 **不再变第二次**  
4. food 第 4 次当日 → `429 QUOTA_EXCEEDED`，`retryable=false`，`details.quota=food`，`details.reset_at` 有值  
5. **knowledge** + Extract 记忆 + 另一类 active 记忆 → 凑 **3 种 active type**；再 **complete Promise 四次**（bond+5×4=20）→ `stage` 变为 `formed`（见第 4 节）  
6. Chat `local_hour` 乱填不影响配额日界；改系统时区后 **不要**指望刷出新的当日额度  
7. 迟到 patch：本地已是 v10 时收到 v9 → 丢弃并 bootstrap  

---

## 1. P0 能力：现在能联什么

| P0 能力 | 本机状态 | 联调注意 |
| :--- | :--- | :--- |
| Bootstrap 权威 status/stage/quota/marks | **可联** | 无新路由；读 `GET /bootstrap` |
| Feed / Chat / Extract 成长 patch | **可联** | 走现有 POST；重试不二次成长 |
| 日配额 429 | **可联** | food 3、knowledge 10、emotion 10、sight 2、chat **100** |
| 孵化 `room.layers=["spirit"]` | **可联** | 不要把 marks 当 layers |
| `formed` | **可联**（要凑条件） | bond≥20 且 ≥3 种 **active** 记忆类型 |
| `awake` | **短时很难** | 还要知识成长 +（串门 **或** 走丢）。串门 P16；72h 走丢短时联调等不到 |
| 学者纹层 `scholar` | **可联（P15 完成后）** | 有 marks 时层 token 是 `scholar`；见 [`p15-integration.md`](p15-integration.md) |
| 18h/72h 自动切态 | **bootstrap 可 settle** | 本机无 worker 扫 tick；打开 App 打 bootstrap 才会校正。短时等不到 18h |
| 成本 70%/100% 告警 | **服务端内部** | iOS 无此 API；100% **不会** ban Chat |

仍不要打：社交、通知 APNs、鉴定卡、注销、找回。ASR/TTS 改看 [`p14-integration.md`](p14-integration.md)；Pact 改看 [`p15-integration.md`](p15-integration.md)。

---

## 2. 客户端只交事实

成长唯一账本是服务端 `growth_events`。iOS 继续提交 Chat 正文、Feed kind、Promise complete 等 **事实**。

| 禁止出现在请求 body | 原因 |
| :--- | :--- |
| `hunger` / `energy` / `mood` / `bond` 及 `*_delta` | 服务端结算 |
| `stage` / `status` / `scholar_marks` | 服务端结算 |
| `user_id` | JWT claim |

多一个 extra 字段 → **422**。  
同一 Feed/Chat/Extract `client_id`（或 `client_message_id`）重试：属性只变一次。不要为了「再涨一点」换新 ID 重放同一事实。

Chat 的 `context.local_hour` / `weather` 只影响表达，**不**用于配额日界、18h/72h、成长 delta。

---

## 3. `snapshot_version` 与迟到 patch

`snapshot_version` = `spirits.version`，初值 1；无精灵 bootstrap 为 **0**。  
会影响 bootstrap 的 mutation 同事务只 +1。纯 GET 不加；bootstrap 前置 settle **仅当状态真的变了** 才 +1。

| 响应 | 必带 |
| :--- | :--- |
| 所有 MutationResult | `patch.snapshot_version` |
| Chat / Extract | `patch.spirit`（含 status/stage/marks）；`patch.room` **null** |
| Feed / Promise | `patch.spirit` + `patch.room` + `quotas` |
| 孵化 complete | `patch.spirit` + `patch.room`（layers 已投影） |

`409 CONFLICT` 的 `details.snapshot_version` 是当前聚合版本。Promise 的 `expected_version` 仍是 **spirit.version**，不是 feed 行版本。

---

## 4. 进化（`spirit.stage`）

仅三值：`whelp` | `formed` | `awake`。只能前进，不能回退、不能跳级。  
封存 / 删除的记忆 **不计入** formed 的类型数。

| 阶段 | 服务端条件（同时满足） |
| :--- | :--- |
| `formed` | `bond ≥ 20` 且至少 **3 种** `status=active` 的 memory `type` |
| `awake` | `bond ≥ 50` 且有知识或共学成长 且（`has_visited` 或 `has_been_lost`） |

本机可凑 `formed` 的一条路径：

1. `POST /feed` knowledge → 一种 `type=knowledge`  
2. Extract 出其它 type（如 preference / emotion）  
3. 地点见闻 accepted → `type=sight`  
4. `POST /feeds/{id}/complete` 四次（每次 bond+5；每次须先有 active Promise）

`awake` 的「知识成长」= 有过 **accepted** 的 knowledge feed。共学 Pact 见 [`p15-integration.md`](p15-integration.md)。串门留 P16。走丢要 72h idle 或改库，短时联调不要客户端造 `lost`。

学者纹：`spirit.scholar_marks` 是稳定 key 数组（如 `interview-v1`）。房间只多一层 token **`scholar`**，不要把 key 画进 `layers`。未完成 Pact 时 live 通常是 `[]`；完成契约后见 P15 末题 `patch.spirit`。

---

## 5. 状态与房间（P13 补丁）

四态与投影细节仍以 [`p08-p10-integration.md`](p08-p10-integration.md) 第 2 节为准。P13 强调：

- 优先级 **lost > study > away > home**；home 的主层 token 是 **`spirit`**  
- `GET /bootstrap` **不是**有效互动，不刷新 `last_interact_at`  
- 成对 Chat 等有效互动才会拉回 study/away（lost 更严，以 bootstrap 为准）  
- 本机没有 cron worker：18h/72h 依赖下次 bootstrap 的短写 settle。打开 App 请打 bootstrap，不要本地倒计时切态  

孵化 `POST /onboarding/complete` 的 `patch.room.layers` 现为 **`["spirit"]`**（空 marks 时）。旧样例若仍是 `[]`，以 live 为准。

---

## 6. 配额

`data.quotas[]`：`capability` / `used` / `limit` / `reset_at`（UTC `Z`）。  
`reset_at` 是该行 `usage_date` 在用户 timezone 下 **次日 00:00** 的 UTC。改 timezone **不能**刷出新的一天（沿用未过期窗口）。

本机日限额（命名默认，不是线上牌价）：

| capability | limit |
| :--- | ---: |
| `food` | 3 |
| `knowledge` | 10 |
| `emotion` | 10 |
| `sight`（photo + location 合计） | 2 |
| `chat` | 100 |
| promise | **无**日配额 |

超限：

```text
HTTP 429
error.code = QUOTA_EXCEEDED
error.retryable = false
error.details.quota = <capability>
error.details.reset_at = <ISO 8601 UTC>
```

不要本地 `used+1`。Chat 配额在成功路径消耗；失败/429 后 UI 用服务端数字。

---

## 7. 成本（iOS 不用接）

`ai_usage` 只记 units / model alias / `prompt_version`（如 `chat/v3`）/ 微单位成本。  
**没有** Prompt、消息正文、原图。日预算 100% 只出内部告警，**Chat 不会被服务端硬停**。不要为此做客户端「额度用尽不能说话」除非产品另有文案且仍以 429 为准。

---

## 8. 统一错误码（P13 相关）

| HTTP | code | retryable | 处理 |
| ---: | :--- | :--- | :--- |
| 401 | `UNAUTHENTICATED` | false | 重新匿名登录 |
| 404 | `NOT_FOUND` | false | 无精灵 |
| 409 | `CONFLICT` | false | `expected_version` 过期；读 `details.snapshot_version` 后 bootstrap 或带新版本重试 |
| 409 | `IDEMPOTENCY_CONFLICT` | false | 同 client_id 改了 body |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | true | 稍后原 ID 重试 |
| 422 | `INVALID_INPUT` | false | 多了 delta/stage 等字段 |
| 429 | `QUOTA_EXCEEDED` | false | 展示 `details.reset_at` |
| 503 | `MODEL_UNAVAILABLE` | true | 不假写成长 |

属性上下界由服务端 clamp；客户端不要先截断再提交「补差值」。

---

## 9. 全链路时序

```text
GET  /bootstrap                         status/stage/layers/quotas；可能 settle
POST /feed  food                        hunger/energy；quota.food；snapshot+1
POST /feed  food  （同一 client_id）      回放，不再加 hunger
POST /feed  food  （第 4 次新 ID）        429 QUOTA_EXCEEDED
POST /chat  onboarding=false            patch.spirit；patch.room=null；quota.chat
POST /extract                           patch.spirit + memories_upsert
POST /feed  knowledge                   知识记忆 + 知识成长标记
POST /feeds/{id}/complete ×4            bond 到 20；条件够则 stage=formed
GET  /bootstrap                         确认 stage/status/scholar_marks/layers
```

迟到包：

```text
本地 snapshot=10，收到 patch.snapshot_version=9  →  不合入，GET /bootstrap
```

---

## 10. 人工联调清单

- [ ] 解码 SHA 是 `cdb52009…`，不是 P08/P11/P13 旧值  
- [ ] 孵化后 `room.layers` 含 `spirit`，不含 mark key  
- [ ] food 成功前 UI 饱食不变；同 `client_id` 不二次加 hunger  
- [ ] food 第 4 次 429，`retryable=false`，有 `reset_at`  
- [ ] Chat/Extract 后房间不丢（忽略 null `patch.room`，必要时 bootstrap）  
- [ ] 本地已更新版本后，旧 `snapshot_version` 不合入  
- [ ] 无网时房间保留上一帧，`offline` 不是 status  
- [ ] 不在请求里带 delta / stage / status  
- [ ] 日志无 JWT、无 Prompt、无记忆全文  
- [ ] 条件够时 `stage` 只出现 `formed`，不会自己改回 `whelp`  
- [ ] 不宣称 awake / 学者纹 / 18h 四态已在真机等到（除非实际等到或改库）  

---

## 11. 本范围明确不做

- 在本机 `3_ios/` 改工程（到 192.168.100.205）  
- 改 OpenAPI 字段（SHA 已锁）  
- 宣称串门、找回已可联；Pact 学者纹联调见 [`p15-integration.md`](p15-integration.md)
- 把本机无 worker 写成「18h 会在后台自动切态且不需要打开 App」  
- 把成本 100% 告警写成 Chat 硬停  

常见坑：继续用旧 SHA；Chat `patch.room=null` 把房间清掉；把 `scholar_marks` 写进 `layers`；Promise `expected_version` 误用 feed 版本；用设备时钟切 study/lost；联调时跑 pytest 清空库。

标准 JSON 以仓库 fixture 为准（与本文 SHA 同锁）。live 的 id、version、`reset_at`、`server_time` 以服务端为准。
