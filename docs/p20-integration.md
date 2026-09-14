# 刻灵 P20 联调说明（iOS 接入）

面向 iOS ↔ 本机 FastAPI，覆盖 **走失找回、dev Debug**。  
锁与进程见 [`p16-p20-integration.md`](p16-p20-integration.md)。走失状态来自 P13 settle（服务端时间）。  
**不是阶段验收**。本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `fed40c13c30d2203f20ed83c7ad208042c13f35e330d3491e407f22be0593230` |
| Alembic head | `20260908_0019`（P20 **未改库**） |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/` |

本机联调 `APP_ENV=dev` 时 **会注册** Debug 路由。`APP_ENV=prod` **永不**注册；默认 test OpenAPI **无** `/api/v1/debug/*`。Release / TestFlight / prod 必须当这些路径不存在（404）。

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| Recall | **已接线**。仅 `status=lost`；`food` 或 `sight_memory` | `POST /api/v1/recall` |
| 成功 | 置 `home`；关系记忆「你离开过」；growth `returned_from_lost`（`source_id=client_id`）；event `recall.returned` | 合入 patch |
| food 满额 | **仍回家**；不计普通 food 的 hunger/energy；配额记满额免费 | `quota_full_food.json` |
| sight | 必须本人、`type=sight`、`status=active` | 否则 `RECALL_SOURCE_INVALID` |
| Debug | **仅 dev**（或测试旗标）。JWT allowlist **或** `X-Debug-Token`；来源 loopback/私网 | `app/api/v1/debug.py` |
| Debug 请求 | `extra=forbid`，拒绝任意 `user_id` / SQL / URL / Secret | 422 |

---

## iOS 需要改什么

### 不要做

- 不要本地把 status 改成 home；没成功包就保持 lost  
- 不要对 home/away/study 打 recall（`SPIRIT_NOT_LOST`）  
- 不要提交成长 delta；bond/mood/closeness 只合入 `patch.spirit`  
- 不要用别人的、已封存、已删除、非 sight 记忆  
- 不要在 Release 编译打 `/debug/*`；不要把 Debug token 打进日志或打进 App Store 包  
- 不要在 Debug body 里带 `user_id`、连接串、任意 URL  
- 不要把 fixture 的记忆 id / snapshot 钉死  

### 必须改：DTO 对齐 SHA

锁定 SHA：`fed40c13c30d2203f20ed83c7ad208042c13f35e330d3491e407f22be0593230`。

Recall 解码：

- `POST_api_v1_recall/{success,business_error,missing_required_field,idempotent_replay,not_lost,quota_full_food,sealed_memory,deleted_memory,wrong_owner,non_sight}.json`

请求（判别联合，`extra=forbid`）：

```json
{ "client_id": "<uuid>", "method": "food" }
```

```json
{ "client_id": "<uuid>", "method": "sight_memory", "memory_id": "<uuid>" }
```

成功合入：`patch.spirit`、`patch.room`、`patch.memories_upsert`、`quotas`、`events`（必有 `recall.returned`）。关系记忆按 `(owner, client_id)` 幂等，同一 `client_id` 回放不重复插入。

food 日限额仍是 **3**（与普通投喂共用能力名 `food`）。满额 recall **成功回家**，不要当成 429。

### Debug（仅 #if DEBUG）

本机 `APP_ENV=dev` 已挂路由。来源 `192.168.100.205` 属私网，host 检查可通过。身份还要：

1. JWT `sub` 在服务端 `DEBUG_ALLOWLIST`，或  
2. 请求头 `X-Debug-Token` 与服务端 `DEBUG_TOKEN` 一致  

路径（前缀 `/api/v1/debug`）：

| 方法 | 路径 | 作用 |
| :--- | :--- | :--- |
| POST | `/spirit/state` | 白名单改 status/vitals/traits |
| POST | `/spirit/time` | `last_interact_at` / `away_until` / `study_until` / `hatched_at` |
| POST | `/memories` | 插入一条记忆 |
| POST | `/report/eligibility` | `hatched_days_ago` + `ordinary_dialogue_rounds` |
| POST | `/failures` | 注入 chat/extract/asr/tts/vision/safety/search/storage/apns 失败 |
| GET | `/usage` | 当前配额快照 |
| DELETE | `/reset` | `confirm` 必须 `RESET_MY_DEBUG_ACCOUNT` |

prod 构建：这些路径必须 404，且客户端不展示入口。

### 必须改：SessionStore

Recall 失败保持 lost，不要乐观改 UI。成功按 snapshot_version 合入；`patch.preferences/report/social/pact` 为 null 不改那些块。

---

## 建议联调顺序

1. 精灵 `status=lost`（真实 72h 或 Debug `POST /spirit/state`）  
2. `POST /recall` 无 JWT → `401`  
3. home 时 food recall → `SPIRIT_NOT_LOST`  
4. lost + food → 200，`status=home`，有「你离开过」，有 `recall.returned`  
5. 同一 `client_id` 重放 → 同一记忆 id  
6. food 打满 3 次后再 lost + food recall → 仍 200 回家（满额样例）  
7. sight：active 自己的 sight → 200；sealed/deleted/他人/非 sight → `RECALL_SOURCE_INVALID`  
8. Debug：Release 配置打任意 `/debug/*` → 以 **不存在** 为准（本机 dev 会 401/403/200，**不要**拿 dev 当 prod 证据）  
9. Debug reset 错 confirm → 422  

---

## 错误码（用 `error.code` 映射 UI）

| code | 典型 UI |
| :--- | :--- |
| `SPIRIT_NOT_LOST` | 现在不用找回 |
| `RECALL_SOURCE_INVALID` | 换食物找回，或换一条可用见闻 |
| `NOT_FOUND` | 没有精灵 |
| `INVALID_INPUT` | 缺 method / 缺 memory_id / 多余字段 |
| `IDEMPOTENCY_CONFLICT` / `IN_PROGRESS` | 同其它 mutation |
| `FORBIDDEN` | Debug 身份或来源不对 |
| `UNAUTHENTICATED` | 重新登录 |

英文 `message` 不要直接展示。

---

## 人工联调清单

- [ ] SHA `fed40c13…`  
- [ ] recall 两种 body 都 `extra=forbid`  
- [ ] 非 lost 不乐观回家  
- [ ] 满额 food 仍回家，不走投喂 429 UI  
- [ ] 关系记忆文案以服务端为准（「你离开过」）  
- [ ] Debug 仅 DEBUG 编译；confirm `RESET_MY_DEBUG_ACCOUNT`  
- [ ] 日志无 JWT、无 Debug token  
- [ ] prod OpenAPI / Release 无 debug 入口  
- [ ] 不宣称 V0 全链路或阶段通过  

---

## 本范围明确不做

- 在本机 `3_ios/` 改工程  
- 给 prod 打开 Debug  
- 新增 migration  
- 宣布阶段验收通过或已部署  

常见坑：把满额 food recall 当成配额错误；Release 打 debug；用错误记忆 id 还本地清 lost；联调时跑 db pytest 清库。
