# 刻灵 P16 联调说明（iOS 接入）

面向 iOS ↔ 本机 FastAPI，覆盖 **好友、公开档案白名单、明信片、串门结果模型**。  
锁与进程见 [`p16-p20-integration.md`](p16-p20-integration.md)。设置里的 `visit_on` 见 [`p19-integration.md`](p19-integration.md)。  
**不是阶段验收**。本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af` |
| Alembic head | `20260908_0019` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/` |

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| 好友列表 / 加 / 删 | **已接线**。无向边 `low<high`；加自己 409 | `GET/POST /api/v1/friends`，`DELETE /api/v1/friends/{friend_id}` |
| 邀请码 | **已接线**。8 位，排除 `0/O/1/I`；trim + uppercase | 孵化后在自己的 `spirit.invite_code` |
| 公开档案 | **已锁**。只有 `id,title,stage,public_marks,status` | `PublicSpiritProfile` |
| 明信片 | **已接线**。仅接收方列表；已读幂等 | `GET /api/v1/postcards`，`PATCH /api/v1/postcards/{id}/read` |
| 串门 | **worker-only**。最多 2 目的地；NPC `fog\|lamp\|silent`。无公开创建路由 | scheduler 入队，worker 规划/结算 |
| 事件 | `friend.added` / `friend.removed` / `postcard.read` / `visit.settled` | mutation `events` |

默认 `visit_on=true`。away 满 18h 后由服务端规划串门，客户端 **不能** 选去谁家。

---

## iOS 需要改什么

### 不要做

- 不要调用任何 visit create；不要在请求里带 host / NPC / score / 坐标  
- 不要把好友档案当成自己的 `SpiritPublic`（没有 hunger / 对话 / 记忆 / email / user_id）  
- 不要把 `friend_id` 当成对方精灵 id；删好友用边上的 `friend_id`  
- 不要本地编造明信片正文或 visit 结算  
- 不要把 `GET /bootstrap.unread_postcards` 当未读源（本机恒 `[]`）  
- 不要把 fixture UUID / cursor / `snapshot_version` 钉死  
- 不要在日志里打 JWT；不要把邀请码当密码存日志  
- 不要宣称真机串门已验收或阶段通过  

### 必须改：DTO 对齐 SHA

锁定 SHA：`98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af`。

新增解码：

- `GET_api_v1_friends/{success,business_error,missing_required_field,page_empty,page_first,page_middle,page_last,invalid_or_expired_cursor}.json`
- `POST_api_v1_friends/{success,business_error,missing_required_field,self_friend,invalid_invite,idempotent_replay}.json`
- `DELETE_api_v1_friends_{friend_id}/{success,business_error,missing_required_field,not_found,idempotency_conflict}.json`
- `GET_api_v1_postcards/{success,business_error,missing_required_field,page_empty,page_first,page_middle,page_last,invalid_or_expired_cursor}.json`
- `PATCH_api_v1_postcards_{postcard_id}_read/{success,business_error,missing_required_field,not_found,read_replay}.json`

OpenAPI 路径表里 **不能出现** `POST /visits`。公开档案字段集合必须正好是 `id,title,stage,public_marks,status`。未知字段 / 缺字段要失败可观测。

### 必须改：SessionStore

好友加删、明信片已读只合入：

1. `data.patch`（`social` 和/或 `postcards_upsert`；`spirit`/`room` 常为 null）  
2. 同包 `data.events`  
3. `data.quotas`（成功包经常是 `[]`）

`GET /friends`、`GET /postcards` 是分页快照，不是 mutation：用 `items` + `next_cursor` + `has_more` 替换列表，不要当 patch 合入。

cursor 是不透明 HMAC；`unread_only` 变了必须丢掉旧 cursor，否则 `INVALID_CURSOR`。`limit` 默认 30、最大 50。

### 邀请码与加好友

自己的码在孵化后的 `SpiritPublic.invite_code`（以及鉴定卡快照里的 `invite_code`）。对方输入后 `POST /friends`：

```json
{ "client_id": "<uuid>", "invite_code": "ABCD2345" }
```

服务端会 trim + uppercase。重复加同一边幂等（同一 `client_id` 回放同一边）。加自己 → `SELF_FRIEND_NOT_ALLOWED`。未知码 → `NOT_FOUND`。非法字符（含 `0/O/1/I`）→ `422 INVALID_INPUT`。

### 串门与明信片

客户端只展示结算后的白名单：`VisitPublic` 必须恰好一个 `host` 或一个 `npc`。明信片同样恰好一个 `sender` 或 `npc`，正文最长 300。已读：

```
PATCH /api/v1/postcards/{postcard_id}/read
{ "client_id": "<uuid>" }
```

已读重放仍 200，同一 `read_at`。删好友不删历史明信片；进行中的 visit 可以结束，但不再规划新 visit。

要看到明信片：两边都孵化、互加好友、`visit_on` 保持开、精灵进入 away（18h 无互动）、**scheduler + worker 在跑**。不要用客户端 POST 制造 visit。

---

## 建议联调顺序

1. 两台账号都孵化完成；记下双方 `invite_code`  
2. `GET /friends` 无 JWT → `401`  
3. `POST /friends` 自己的码 → `SELF_FRIEND_NOT_ALLOWED`  
4. 用对方码加好友 → 200；`patch.social.friends_upsert` 含该边；公开档案无 vitals  
5. 同一 `client_id` 重放 → 同一 `friend_id`  
6. `GET /friends` 能看到对方 `title/stage/status`  
7. 等 worker 结算（或第二天再看）→ `GET /postcards?unread_only=true`  
8. `PATCH .../read` → `read_at` 有值；再读仍同一时间  
9. `DELETE /friends/{friend_id}` → `friends_removed`；历史明信片仍在列表  

---

## 错误码（用 `error.code` 映射 UI）

| code | 典型 UI |
| :--- | :--- |
| `SELF_FRIEND_NOT_ALLOWED` | 不能加自己 |
| `FRIEND_NOT_ALLOWED` | 契约保留；本机加好友失败主路径是 `NOT_FOUND` / `SELF_FRIEND_*` |
| `NOT_FOUND` | 邀请码不存在，或不是这条边的参与者 |
| `INVALID_CURSOR` | 换过滤条件或过期分页，从第一页重拉 |
| `INVALID_INPUT` | 邀请码格式、缺 `client_id`、多余字段 |
| `IDEMPOTENCY_IN_PROGRESS` | 同一 `client_id` 稍候重试 |
| `IDEMPOTENCY_CONFLICT` | 同一 `client_id` 换了 payload |

英文 `message` 是 fixture 用的，不要直接展示。

---

## 人工联调清单

- [ ] 解码 SHA 是 `fed40c13…`，不是 P15 的 `743b462b…`  
- [ ] 无 `POST /visits`；客户端不选 host/NPC  
- [ ] 公开档案只有四字段 + id，无坐标/记忆/对话  
- [ ] 分页 `has_more` 与 `next_cursor` 同真同假  
- [ ] 未读走 `GET /postcards?unread_only=true`，不依赖 bootstrap 数组  
- [ ] 加删好友 / 已读走 FIFO `client_id`  
- [ ] 日志无 JWT、无对方 user_id  
- [ ] 不宣称串门真机或阶段通过（除非实际做到）  

---

## 本范围明确不做

- 在本机 `3_ios/` 改工程  
- 改 OpenAPI 字段或新增 migration  
- 把 bootstrap `unread_postcards` 填上  
- 让 API 进程规划串门（必须独立 worker）  
- 宣布阶段验收通过或已部署  

常见坑：继续用 P15 SHA；把 `friend_id` 当精灵 id；向服务端 POST visit；联调时跑 db pytest 清空库；worker 没起却等明信片。
