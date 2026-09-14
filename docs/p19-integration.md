# 刻灵 P19 联调说明（iOS 接入）

面向 iOS ↔ 本机 FastAPI，覆盖 **精灵名与偏好 PATCH、账号注销 202**。  
锁与进程见 [`p16-p20-integration.md`](p16-p20-integration.md)。计划名里的「绑定」**不是**公开 REST。  
**不是阶段验收**。本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `fed40c13c30d2203f20ed83c7ad208042c13f35e330d3491e407f22be0593230` |
| Alembic head | `20260908_0019` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/` |

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| 设置 | **已接线**。字段级 patch；`expected_version` = `snapshot_version` | `PATCH /api/v1/spirit` |
| DND | `HH:MM`（00:00–23:59） | `dnd_start` / `dnd_end` |
| timezone | **IANA**（如 `Asia/Shanghai`） | 非法 → 422 |
| `default_city` | 可显式 JSON `null` 清空 | `model_fields_set` |
| 注销 | **固定 HTTP 202**。`confirm` 必须整句 `DELETE_MY_ACCOUNT` | `DELETE /api/v1/account` |
| 注销落地 | worker：`accepted → deleting_storage → deleting_auth → deleting_database → completed` | 独立 worker |
| 门禁 | 有 deletion 行（含 completed）后，后续业务 API 被挡；DELETE 本身 `allow_account_deleting` | `claimed_transaction` |

系统通知权限、定位权限 **不存在** 这组接口里。identity linking / magic-link / `/bind` / `/link` **禁止**当作业务 REST。

---

## iOS 需要改什么

### 不要做

- 不要一次 PATCH 不带 `name` 且 `preferences` 为空对象（至少改一个真字段）  
- 不要发系统权限开关字段（多余 → 422）  
- 不要本地改 timezone 去「拨」鉴定日或配额日；以服务端返回的 preferences 为准  
- 不要把 409 `CONFLICT` 当普通失败：读 `error.details.current_snapshot_version`，拉 bootstrap 再带新 `expected_version`  
- 不要实现绑定邮箱/手机的 REST  
- 不要等注销 worker 完成才清本地：收到 **202** 立即擦 SessionStore / Keychain 用户态  
- 不要把 `confirm` 做成可编辑文案；必须精确 `DELETE_MY_ACCOUNT`  
- 不要注销进行中再用另一 `client_id` 重提（`ACCOUNT_DELETE_PENDING`）  

### 必须改：DTO 对齐 SHA

锁定 SHA：`fed40c13c30d2203f20ed83c7ad208042c13f35e330d3491e407f22be0593230`。

新增解码：

- `PATCH_api_v1_spirit/{success,business_error,missing_required_field,idempotent_replay,version_conflict,illegal_timezone,illegal_dnd,name_too_long}.json`
- `DELETE_api_v1_account/{success,business_error,missing_required_field,idempotent_replay,pending,confirm_mismatch}.json`

PATCH 请求：

```json
{
  "client_id": "<uuid>",
  "expected_version": 3,
  "name": "雾生",
  "preferences": {
    "tts_on": true,
    "push_on": true,
    "visit_on": true,
    "dnd_start": "23:00",
    "dnd_end": "08:00",
    "timezone": "Asia/Shanghai",
    "default_city": null,
    "location_weather_on": false,
    "remote_search_on": false
  }
}
```

`preferences` 里只发要改的键。`name` 1–20 字。成功合入 `patch.spirit` + `patch.preferences`。

DELETE：

```json
{ "client_id": "<uuid>", "confirm": "DELETE_MY_ACCOUNT" }
```

成功 `data`：`deletion_id`、`status=accepted`、`requested_at`。同一 `client_id` 重放同一 `deletion_id`。

### 必须改：SessionStore

PATCH：按 snapshot_version 合入 spirit / preferences。`patch.room` 等为 null 不改房间。

DELETE 202：立刻清空本地精灵/记忆/JWT 用户缓存（产品允许留登录壳则按产品，但业务快照必须扔掉）。之后任何 Chat/Feed/Friends 都可能被门禁挡住，不要当普通 401 去重登死循环而不展示「账号正在删除」。

---

## 建议联调顺序

1. `GET /bootstrap` 记下 `snapshot_version`  
2. `PATCH /spirit` 无 JWT → `401`  
3. 只改名，带当前 version → 200，version +1  
4. 用旧 `expected_version` → 409 `CONFLICT`，`details.current_snapshot_version`  
5. 非法 `25:00` DND、非法 `Foo/Bar` timezone、21 字名 → 422  
6. 同一 `client_id` 重放 PATCH → 幂等同一快照  
7. `DELETE /account` confirm 乱写 → 422  
8. 正确 confirm → **202**，立刻清本地  
9. 同一 `client_id` 再 DELETE → 同一 `deletion_id`  
10. 另一 `client_id` 在未完成时 DELETE → `ACCOUNT_DELETE_PENDING`  
11. worker 跑完后不要用该账号联后续业务  

---

## 错误码（用 `error.code` 映射 UI）

| code | 典型 UI |
| :--- | :--- |
| `CONFLICT` | 设置过期，按 `current_snapshot_version` 刷新 |
| `INVALID_INPUT` | DND / timezone / 空 patch / confirm 不对 |
| `NOT_FOUND` | 还没有精灵 |
| `ACCOUNT_DELETE_PENDING` | 注销已在进行，不要换 client 再提 |
| `IDEMPOTENCY_CONFLICT` | 同一 client_id 换了 payload |
| `IDEMPOTENCY_IN_PROGRESS` | 稍候重试同一 client_id |

英文 `message` 不要直接展示。

---

## 人工联调清单

- [ ] SHA `fed40c13…`  
- [ ] PATCH 带 `expected_version`；409 能恢复  
- [ ] DND 仅 `HH:MM`；timezone 仅 IANA  
- [ ] 无 `/link` `/bind` 客户端调用  
- [ ] DELETE 一律按 202 清本地，不把 200 当成功条件  
- [ ] confirm 固定字符串  
- [ ] 日志无 JWT  
- [ ] 不宣称注销真机验收通过  

---

## 本范围明确不做

- identity linking REST  
- 在本机 `3_ios/` 改工程  
- 改 OpenAPI 或 confirm 文案  
- 宣布阶段验收通过或已部署  

常见坑：expected_version 用精灵 `version` 却与 snapshot 混用（本接口要 **snapshot_version**）；收到 202 还轮询业务 API；实现一个「绑定手机」接口去打不存在的路由。
