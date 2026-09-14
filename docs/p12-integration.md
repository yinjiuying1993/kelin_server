# 刻灵 P12 联调说明（iOS 接入）

面向 iOS ↔ 本机 FastAPI，覆盖 **S06 五类投喂、地点见闻、Promise、照片上传契约与 moderate**。  
前置：已按 [`p01-p07-integration.md`](p01-p07-integration.md) 孵化（`hatched_at` 有值）。房间/Chat 见 [`p08-p10-integration.md`](p08-p10-integration.md)；S07 记忆见 [`p11-integration.md`](p11-integration.md)。  
P13 成长 / 进化 / 配额见 [`p13-integration.md`](p13-integration.md)。  
P14 语音契约见 [`p14-integration.md`](p14-integration.md)（**SHA 已换**）。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机工作区 `3_ios/` 仍无 App 源码，本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `cdb52009a4ec253303a483f7a706d0439bc3fae8c728f146bbb7fa89fec71ae2` |
| Alembic head | `20260908_0008` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/`（与 SHA 同锁） |

P11 文档里的 SHA `5aea0b23…` 是加 Feed / Storage / moderate **之前** 的锁。P12 解码请用上表 SHA。

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| P12-T01 五类 Feed | **已接线**。判别联合；food/knowledge/emotion 一次结算 | `app/schemas/feed.py`、`app/services/feed.py` |
| P12-T02 Promise | **已接线**。创建 / PATCH / complete / cancel；仅 complete 增 bond | `app/api/v1/feeds.py` |
| P12-T03 Location sight | **已接线**。仅 `label`/`city`/`category`；隐私拒绝仍 HTTP 200 | `_settle_location_sight` |
| P12-T04 上传会话 | **已接线**。四段 path、10 分钟 PUT、1 byte–5 MiB、JPEG | `POST /storage/sight-upload-url` |
| P12-T05 Storage | **仅进程内存**。签发 URL 是 `https://kelin.invalid/...`，真机 PUT **落不到** API 内存桶 | `PrivateSightStorage` |
| P12-T06 moderate | **HTTP 已接线**。Safety 别名仍 unset → live `MODEL_UNAVAILABLE` | `POST /moderate-sight` |

进程仍是 `kelin-postgres-test` + `kelin-server-api`（`0.0.0.0:8000`）。无独立 worker。改 Python 后 `docker restart kelin-server-api`。

**不要**在联调期间对该库跑会 `DROP SCHEMA` 的全量 pytest（会清精灵/记忆，需重新孵化）。

环境、JWT、ATS、IP 与 P01–P07 第 2 节相同：`API_BASE_URL=http://192.168.100.212:8000`。

---

## iOS 需要改什么

### 不要做

- 不要直连 Postgres / 百炼 / Supabase Storage service role；Key、模型名不得进 iOS / Git / 日志  
- 不要本地改 `hunger` / `energy` / `mood` / `bond` / `weather` / 配额；只合入服务端 `patch` + `quotas`  
- 不要在地点 payload 里带 `latitude` / `longitude` / `address` / `provider` / 门牌（多余字段 → 422）  
- 不要客户端拼 `object_path` 或任意 PUT URL；path 只能用签发响应里的四段  
- 不要把 `resource.status=rejected` 当成 HTTP 失败；安全/隐私拒绝经常是 **200 + rejected**  
- 不要用 Stub/默认 `lamp` 当真实见闻；本机照片链 **尚未** 可联  
- 不要在重试时换该操作的 `client_id`  
- 不要一张卡的 loading 锁住整页 S06  
- 不要服务端成功前调度/替换 Promise 本地通知  
- 不要打 Pact、社交、ASR/TTS、鉴定卡、注销（P14+）

### 必须改：DTO 对齐 SHA

锁定 SHA：`cdb52009a4ec253303a483f7a706d0439bc3fae8c728f146bbb7fa89fec71ae2`（P14-T01 起；见 [`p14-integration.md`](p14-integration.md)）。  
新增 / 续用解码：

- `POST_api_v1_feed/{success,business_error,missing_required_field}.json`
- `PATCH_api_v1_feeds_{feed_id}/{success,business_error,missing_required_field}.json`
- `POST_api_v1_feeds_{feed_id}_complete/{success,business_error,missing_required_field}.json`
- `POST_api_v1_feeds_{feed_id}_cancel/{success,business_error,missing_required_field}.json`
- `POST_api_v1_storage_sight-upload-url/{success,business_error,missing_required_field}.json`
- `POST_api_v1_moderate-sight/{success,business_error,missing_required_field}.json`
- 继续用 `GET_api_v1_bootstrap/`（`room.pending_sight`、`room.letter`、`quotas`、`feature_flags.image_feed`）

未知字段 / 缺字段要失败可观测。fixture 的 UUID、时间、`snapshot_version` 是样例，**live 以响应为准**。

`SessionStore`：mutation **只合入** `patch`（`spirit` / `room` / `memories_upsert` / `memory_tombstones`）和同包 `quotas`。不要用本地日期倒数配额。

### 必须改：AppPhase

```text
ready（hatched_at 有值）
  → S01 房间（pending_sight 蒙布、promise 字条）
  → S05 Chat / S07 刻痕
  → S06 五卡：POST /feed
       口粮 / 知识 / 情绪 / 约定 / 地点见闻   ← 本机可联
       照片：PhotoProcessor → feed → upload-url → PUT → moderate
         ← 契约要接；真机 PUT + 真实审核本机还不通
```

五卡独立 `SubmissionState`，互不锁。配额文案用 `quotas[].used/limit`，不要本地日历计数。

### 建议联调顺序

1. 重新孵化后 `GET /bootstrap`：`hatched_at` 有值、`feature_flags.image_feed=true`  
2. **food** → 看 `patch.spirit.hunger/energy`、`quotas.capability=food`（limit **3**）  
3. **knowledge** → `memories_upsert` 一条 `type=knowledge`；S07 `filter=knowledge` 能看到  
4. **emotion** `happy` → `patch.room.weather` 来自服务端（happy→`clear`），不要本地改天气  
5. **promise** 创建 → `promise_status=active`；到期后 bootstrap `room.letter.type=promise`（**不会**自动 complete）  
6. **complete** → `bond+5`、`events[].type=promise.completed`；**cancel** bond 不变  
7. **location** `外滩/上海/landmark` → `accepted` + sight 记忆；再试带坐标/门牌的字符串 → 200 `rejected`、无记忆  
8. 照片：先接 DTO 与状态机；真 PUT / moderate 等 Storage + Safety 别名（见第 6 节）

---

## 1. P0 能力：现在能联什么

| P0 能力 | 阶段 | 本机状态 | 联调注意 |
| :--- | :--- | :--- | :--- |
| `POST /api/v1/feed` food | T01 | **可联** | `payload` 必须是 `{}` |
| `POST /api/v1/feed` knowledge | T01 | **可联** | `text` 1–10000；无网只留草稿 |
| `POST /api/v1/feed` emotion | T01 | **可联** | 枚举固定；天气只信 patch |
| `POST /api/v1/feed` promise | T02 | **可联** | 同时只能一个 active |
| `PATCH /feeds/{id}` | T02 | **可联** | `expected_version` = **spirit.version** |
| `POST /feeds/{id}/complete` | T02 | **可联** | 仅此路径增 bond（+5） |
| `POST /feeds/{id}/cancel` | T02 | **可联** | 不增 bond；与 complete 竞争仅一胜 |
| `POST /feed` sight location | T03 | **可联** | 与 photo **共享** sight 日限额 **2** |
| `POST /storage/sight-upload-url` | T04 | **可签发** | URL 主机是 `kelin.invalid`，真机 PUT 无效 |
| `POST /moderate-sight` | T06 | **路由在** | Safety unset → `503 MODEL_UNAVAILABLE` |
| 真实 bucket PUT | T05 | **不可联** | 无 live Storage |
| 真实 Vision/Safety | T06 | **不可联** | 不得标真实见闻 |

仍不要打：Pact、社交、ASR/TTS、通知 APNs、鉴定卡、注销。

---

## 2. 统一规则

```http
Authorization: Bearer <RS256 access_token>
Content-Type: application/json
X-Request-ID: <可选 UUID>
```

成功包：`data.resource` + `data.patch` + `data.quotas` + `data.events`。  
`POST /moderate-sight` 额外有 `data.prop`（`lamp|plant|book|object|other` 或 `null`）。

| 规则 | 做法 |
| :--- | :--- |
| 幂等 | 每个动作一个稳定 UUID `client_id`；超时用 **同一 ID** 重试 |
| 新动作 | 新 UUID；不要复用 spirits / chat / extract / 记忆 mutation 的 ID |
| 同 ID 不同 body | `409 IDEMPOTENCY_CONFLICT` |
| 合入 | 只信 `patch`；失败不要本地假写饱食/天气/bond/记忆 |
| 配额 | `used/limit/reset_at`；超限 `429 QUOTA_EXCEEDED`（`retryable=false`） |
| 日限额 | food **3**；knowledge **10**；emotion **10**；sight（photo+location 合计）**2**；promise **无日配额** |
| extra | 请求 `extra=forbid`，多一个字段就是 422 |

Promise 的 `PATCH` / `complete` / `cancel`：

```text
expected_version  ==  上一帧 patch.spirit.version（或 bootstrap.spirit.version）
```

**不是** `resource.version`（那是 feed 行版本）。对不上 → `409 CONFLICT`，`error.details.snapshot_version` 为当前 spirit 版本。

---

## 3. `POST /api/v1/feed`

```http
POST /api/v1/feed
```

`kind` 判别联合。未知 kind / 缺 `client_id` → 422。无精灵 → 404。

### 3.1 food

```json
{
  "client_id": "<本操作稳定 UUID>",
  "kind": "food",
  "payload": {}
}
```

- 成功：`resource.status=accepted`，`hunger+10`、`energy+10`（clamp 0–100），`events[].type=feed.accepted`  
- **成功前不要改饱食/精力**；可离线排队，成功后再合 patch  
- 第 4 次当日 → `429 QUOTA_EXCEEDED`，`details.quota=food`

### 3.2 knowledge

```json
{
  "client_id": "<UUID>",
  "kind": "knowledge",
  "payload": { "text": "……1–10000 字……" }
}
```

- 成功：`memories_upsert` 一条 `type=knowledge`（summary 截到 500）  
- **无网只存草稿，不排队提交**，不要伪装已投喂

### 3.3 emotion

```json
{
  "client_id": "<UUID>",
  "kind": "emotion",
  "payload": { "emotion": "happy", "note": null }
}
```

`emotion` 仅：`calm` | `happy` | `tired` | `anxious` | `angry`。`note` 可省略，最长 200。

| emotion | mood_delta（服务端） | `patch.room.weather` |
| :--- | ---: | :--- |
| happy | +12 | `clear` |
| calm | +6 | `cloudy` |
| tired | −8 | `cloudy` |
| anxious | −10 | `rain` |
| angry | −15 | `rain` |

单纯快捷可离线排队；**带 note 只留草稿**。天气只显示 patch，不要本地映射。

### 3.4 promise（创建）

```json
{
  "client_id": "<UUID>",
  "kind": "promise",
  "payload": {
    "text": "1–500 字",
    "remind_at": "2026-09-12T04:00:00Z"
  }
}
```

- `remind_at` 必须是带时区的未来时间（相对 **服务端 now**），否则 422  
- 已有 `promise_status=active` 再创建 → `409 CONFLICT`  
- 成功：`resource.status=accepted`，`promise_status=active`；**此时 bond 不变**  
- 创建成功后再调度本地通知，id 建议 `promise-{feed_id}`；正文不要放进通知  
- 到期：下次 `GET /bootstrap` 可出现 `room.letter`（`type=promise`，`title_key=room.letter.promise`，`resource_id=feed_id`）。**到期不会自动 complete**

### 3.5 sight / photo（只建 pending）

```json
{
  "client_id": "<UUID>",
  "kind": "sight",
  "payload": { "source": "photo" }
}
```

- 成功：`status=pending`，**无** memory、**无** `feed.accepted` 事件；已扣 sight 日限额  
- `patch.room.pending_sight`：`source=photo`，`status=pending`（审核中会变 `processing`）  
- 房间蒙布信 `pending_sight`，不要本地编摆件

### 3.6 sight / location

```json
{
  "client_id": "<UUID>",
  "kind": "sight",
  "payload": {
    "source": "location",
    "label": "外滩",
    "city": "上海",
    "category": "landmark"
  }
}
```

| 字段 | 规则 |
| :--- | :--- |
| `label` | 1–80 |
| `city` | 1–40 |
| `category` | 可省略；仅 `landmark` \| `park` \| `cafe` \| `school` \| `workplace` \| `other` |

**禁止**经纬度、门牌、地图原对象、URL。`label`/`city` 里出现坐标、`lat`/`lng`、门牌、`address`/`place_id`、控制字符、URL → **仍 200**，`status=rejected`，无 memory / 无 growth。  
接受：summary 形如 `外滩 · 上海`，`type=sight`，tags 为 category（若有）。  
location **不走** 上传、不走 Vision。与 photo 共享 `capability=sight` limit 2。  
配额在隐私检查 **之前** 已扣：被拒绝也算一次。

---

## 4. Promise 编辑 / 完成 / 取消

均要 `client_id` + `expected_version`（**spirit.version**）。仅 **active** 可操作，否则 `409 PROMISE_NOT_ACTIVE`。

### 4.1 `PATCH /api/v1/feeds/{feed_id}`

```json
{
  "client_id": "<新操作 UUID>",
  "expected_version": 12,
  "text": "1–200 字",
  "remind_at": "2026-09-13T04:00:00Z"
}
```

编辑 `text` 上限 **200**（比创建的 500 更严）。`remind_at` 仍须未来。  
成功后再 **替换** 本地通知；失败不要先改通知。不增 bond。

### 4.2 `POST /api/v1/feeds/{feed_id}/complete`

```json
{ "client_id": "<UUID>", "expected_version": 13 }
```

成功：`promise_status=completed`，`patch.spirit.bond` **+5**，`events[].type=promise.completed`。然后移除本地通知。

### 4.3 `POST /api/v1/feeds/{feed_id}/cancel`

```json
{ "client_id": "<UUID>", "expected_version": 13 }
```

成功：`promise_status=cancelled`，**bond 不变**，无 `promise.completed`。移除本地通知。

complete 与 cancel 并发：只有一个终态。失败的一方 `PROMISE_NOT_ACTIVE` 或幂等回放已有终态。不要本地先改 bond。

---

## 5. 照片链（契约要接，本机真链不通）

规格流水线：

```text
PhotosPicker
 → 修正方向（T07）
 → 去掉 EXIF/GPS
 → 长边 1280 px，JPEG quality 0.75
 → 对处理后的 JPEG 做 SHA-256（64 位小写 hex）
 → POST /feed  kind=sight, source=photo     → feed_id，status=pending
 → POST /storage/sight-upload-url
 → PUT  响应里的 url + headers（不要加 Authorization）
 → POST /moderate-sight
 → accepted / rejected
 → 删本地临时文件
```

### 5.1 `POST /api/v1/storage/sight-upload-url`

```json
{
  "client_id": "<UUID>",
  "feed_id": "<上一步 feed.id>",
  "mime_type": "image/jpeg",
  "size_bytes": 1024,
  "sha256": "<64 位小写 hex>"
}
```

成功 `data`：

| 字段 | 含义 |
| :--- | :--- |
| `upload_session_id` | 下一步 moderate 用 |
| `method` | 固定 `PUT` |
| `url` | 短期单对象 URL |
| `headers` | 目前只有 `content-type: image/jpeg`，PUT 时原样带上 |
| `object_path` | 只读核对：`sight-temp/{user_id}/{feed_id}/{upload_id}.jpg`（四段） |
| `expires_at` | 签发后 **10 分钟** |
| `max_size_bytes` | 5242880（5 MiB） |

约束：`mime_type` 只能 `image/jpeg`；`size_bytes` 1–5242880；`sha256` 64 位 `[0-9a-f]`。  
feed 必须是 **自己的** photo pending。过期 → `410 UPLOAD_EXPIRED`。同 feed 有效会话唯一。

**本机 live：** `url` 主机是 `kelin.invalid`。真机 PUT 不会进 API 进程内存，随后 moderate 会 `409 UPLOAD_NOT_READY`（feed 仍 pending，对象到了可重试）。不要把这条 URL 写进日志全文。

### 5.2 `POST /api/v1/moderate-sight`

```json
{
  "client_id": "<与 feed / upload 都不同的 UUID>",
  "feed_id": "<feed.id>",
  "upload_session_id": "<upload_session_id>"
}
```

| 结果 | HTTP | iOS |
| :--- | :--- | :--- |
| 通过 | 200 `status=accepted`，`prop` 白名单之一，`memories_upsert` 含 `type=sight` | 合入 patch；去掉蒙布 |
| Safety / 未知 prop / 对象 mismatch | 200 `status=rejected`，`prop=null`，无 memory | 当拒绝，不要当网络失败 |
| 对象还没到 | 409 `UPLOAD_NOT_READY` | 可重试同一 `client_id` |
| 会话过期 | 410 `UPLOAD_EXPIRED` | 不要再 PUT |
| Provider 超时 | 504 `PROVIDER_TIMEOUT`（retryable） | feed 保持 `processing`；重开 App 用同一 client_id 再 moderate |
| Safety 未接线 | 503 `MODEL_UNAVAILABLE` | **当前本机 live 就是这条** |

重复 moderate：同一 `client_id` 回放，零二次 growth。  
拒绝/成功都不要本地加 prop。白名单：`lamp|plant|book|object|other`。

杀进程于 `processing`：bootstrap `pending_sight.status=processing`，恢复 moderate，**不要**再 `POST /feed`（会再扣配额）。

### 5.3 PhotoProcessor（iOS T07，本机无工程）

必须在上传前完成，主线程不要做重压缩：

- 修正方向后再编码  
- 去掉 EXIF / GPS；抽检文件无坐标  
- 长边 1280，JPEG 0.75  
- hash / `size_bytes` 对准 **处理后的** JPEG，不是相册原图  
- 取消、失败、成功都清临时文件；不要长期留原图

---

## 6. 统一错误码（P12）

| HTTP | code | retryable | 处理 |
| ---: | :--- | :--- | :--- |
| 401 | `UNAUTHENTICATED` | false | 重新匿名登录 |
| 404 | `NOT_FOUND` | false | 无精灵，或 feed/upload 不是你的 |
| 409 | `CONFLICT` | false | 已有 active Promise，或 spirit `expected_version` 过期 |
| 409 | `PROMISE_NOT_ACTIVE` | false | 已 complete/cancel |
| 409 | `IDEMPOTENCY_CONFLICT` | false | 同 client_id 改了 body |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | true | 稍后原 ID 重试 |
| 409 | `UPLOAD_NOT_READY` | false | 对象未到；照片链可等/重试 moderate |
| 410 | `UPLOAD_EXPIRED` | false | 重新签发（新 upload client_id） |
| 422 | `INVALID_INPUT` | false | 缺字段、过期 remind_at、非法 kind、地点多了坐标字段 |
| 429 | `QUOTA_EXCEEDED` | false | 展示 `details.reset_at`；不要本地再扣 |
| 503 | `MODEL_UNAVAILABLE` / `DEPENDENCY_UNAVAILABLE` | true | 稍后重试；不要假 accepted |
| 504 | `PROVIDER_TIMEOUT` | true | 保持 processing，原 moderate `client_id` 重试 |

`rejected` **不是** 4xx。用 `resource.status` 更新 UI。

---

## 7. 全链路时序

```text
GET  /bootstrap                         hatched；quotas；pending_sight?
POST /feed  food                        hunger/energy patch；quota.food
POST /feed  knowledge                   memories_upsert knowledge
POST /feed  emotion  happy              room.weather=clear
POST /feed  promise                     active；然后才排本地通知
GET  /bootstrap  （remind_at 已过）      room.letter.type=promise；仍是 active
POST /feeds/{id}/complete               bond+5；撤通知
POST /feed  sight location              200 accepted 或 200 rejected
GET  /memories?filter=sight             仅 accepted 的 sight

照片（契约；本机 live 会在 PUT/Safety 停住）
POST /feed  sight photo                 pending + 扣 sight 配额
POST /storage/sight-upload-url          四段 path
PUT  {url}                              本机 kelin.invalid → 无效
POST /moderate-sight                    现为 503 MODEL_UNAVAILABLE
```

---

## 8. 人工联调清单

**五类 / Promise / 地点（本机可做）**

- [ ] food 成功前 UI 饱食不变；重放同一 `client_id` 不二次扣配额  
- [ ] food 第 4 次 `QUOTA_EXCEEDED`  
- [ ] knowledge 无网不能显示已投喂；成功后 S07 看得到  
- [ ] emotion 天气只随 patch 变  
- [ ] 第二个 active promise → 409  
- [ ] PATCH 用错成 `resource.version` → CONFLICT；改用 `spirit.version` 成功  
- [ ] complete 后 bond+5；再 complete → `PROMISE_NOT_ACTIVE`  
- [ ] cancel 后 bond 与取消前相同  
- [ ] 到期字条出现且约定仍是 active  
- [ ] location 抓包 body 无 lat/lng/address  
- [ ] location 拒绝：200 + rejected，S07 无新 sight  
- [ ] 当日第 3 条 sight（含 photo pending）→ `QUOTA_EXCEEDED`  
- [ ] 日志无 JWT 全文、无照片、无约定正文  

**照片（接 DTO；不要标真实见闻通过）**

- [ ] 处理后 JPEG 无 EXIF/GPS；长边 ≤1280  
- [ ] upload-url 的 path 四段且 user_id 是自己  
- [ ] 不把 service role 放进 App  
- [ ] 当前 live：PUT 失败或 moderate `MODEL_UNAVAILABLE` / `UPLOAD_NOT_READY` 时 UI 可恢复，不本地 accepted  

---

## 9. 本范围明确不做

- 在本机 `3_ios/` 改工程（到 192.168.100.205）  
- 把 Stub / `kelin.invalid` / 未接线 Safety 写成真实见闻已通  
- thumbnail、公开读原图、客户端 list/delete bucket  
- Promise APNs（P17）；本阶段只在 **服务端成功后** 调度/替换/移除 **本地** 通知  
- 改 OpenAPI 字段（SHA 已锁）

常见坑：继续用 P11 的 OpenAPI SHA；Promise `expected_version` 误用 feed 版本；地点把地图对象整包上传；photo 成功前当已有记忆；拒绝走了错误态而不是 `status=rejected`；knowledge 离线假成功；联调时跑全量 pytest 清空库。

标准 JSON 以仓库 fixture 为准（与本文 SHA 同锁）：

- `fixtures/POST_api_v1_feed/*.json`
- `fixtures/PATCH_api_v1_feeds_{feed_id}/*.json`
- `fixtures/POST_api_v1_feeds_{feed_id}_complete/*.json`
- `fixtures/POST_api_v1_feeds_{feed_id}_cancel/*.json`
- `fixtures/POST_api_v1_storage_sight-upload-url/*.json`
- `fixtures/POST_api_v1_moderate-sight/*.json`

live 的 id、version、`reset_at`、`server_time` 以服务端为准。
