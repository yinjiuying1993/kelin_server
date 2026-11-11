# 刻灵 iOS 接入：外出召回

登录、Base URL、ATS、`Authorization: Bearer` 沿用 [`ios-integration.md`](ios-integration.md)。本机 API 是 `http://192.168.100.212:8000`。探活仍是 `GET /health/live`。

这次只接召回。对本机 API 联调。公网旧包不要用来验证这次召回。改完服务端后只重启 **api**。

精灵去向只有 `home` 和 `away`。房间 `status` 为 `away` 时，精灵在外出，界面显示召回。`home` 时不显示。去向以服务端房间和精灵的 `status` 为准，不要用本地空闲时长自己切成外出。

召回是 `POST /api/v1/recall`。房间对话 `POST /api/v1/chat` 不负责把精灵叫回来。

---

## 1. 请求

`Content-Type: application/json`。建议带 `X-Request-ID`。

用户点下召回时生成一个 UUID，放进 `client_id`。超时、断线后用同一个 UUID 再发。一次新的召回才换新 UUID。

```json
{
  "client_id": "00000000-0000-4000-8000-0000000000c1",
  "method": "food"
}
```

`method` 只有 `food`。这个值是现行契约里的固定写法，这次调用不扣口粮配额。不要省略后改成别的字符串。不要附带 `hunger`、`hunger_delta`、`bond`、`status`、`stage`、`memory_id`。多出来的字段是 `422` `INVALID_INPUT`，精灵仍留在外出。

`client_id` 缺失同样是 `422` `INVALID_INPUT`。

---

## 2. 成功

HTTP `200`。`ok=true`，`error=null`。

看这三处：

- `data.patch.spirit.status` 为 `home`
- `data.patch.room.status` 为 `home`，`text_status` 为 `home`
- `data.events` 里有一条 `type` 为 `recall.returned`，它的 `id` 等于这次的 `client_id`

`data.resource.type` 为 `recall`，`data.resource.id` 等于 `client_id`，`data.resource.method` 为 `food`。`data.resource.version` 与 `data.patch.snapshot_version` 相同，用它更新本地快照版本。

`data.patch.preferences`、`report`、`social`、`pact` 为 `null`。`memory_tombstones` 和 `postcards_upsert` 为空数组。

`memories_upsert` 可能是空数组。里面有条目时，按已有记忆补丁合并。客户端不要自己写一条「你离开过」。

`quotas` 是当天用量快照。召回成功不增加 `food` 的 `used`。口粮余量维持原样。

示例：

```json
{
  "ok": true,
  "data": {
    "resource": {
      "type": "recall",
      "id": "00000000-0000-4000-8000-0000000000c1",
      "version": 2,
      "method": "food"
    },
    "patch": {
      "snapshot_version": 2,
      "spirit": {
        "id": "00000000-0000-4000-8000-000000000010",
        "name": "雾生",
        "egg": "warm",
        "invite_code": "ABCD2345",
        "stage": "formed",
        "status": "home",
        "version": 2,
        "hatched_at": "2026-09-08T00:00:00Z",
        "created_at": "2026-09-08T00:00:00Z"
      },
      "preferences": null,
      "room": {
        "stage_image_key": "formed_image",
        "letter_key": null,
        "text_status": "home",
        "status": "home"
      },
      "report": null,
      "social": null,
      "memories_upsert": [],
      "memory_tombstones": [],
      "postcards_upsert": [],
      "pact": null
    },
    "quotas": [],
    "events": [
      {
        "id": "00000000-0000-4000-8000-0000000000c1",
        "type": "recall.returned",
        "occurred_at": "2026-11-11T04:00:00Z"
      }
    ]
  },
  "error": null,
  "request_id": "00000000-0000-4000-8000-0000000000f1",
  "server_time": "2026-11-11T04:00:00Z"
}
```

成功后用 `patch.spirit` 和 `patch.room` 覆盖本地房间。进行中的拜访由服务端结束，客户端不用再调别的接口取消拜访。随后的 `GET /api/v1/bootstrap` 也应看到 `home`。

界面看 `error.code`，不要把英文 `message` 直接显示给用户。

---

## 3. 失败

| HTTP | `error.code` | 客户端 |
| :--- | :--- | :--- |
| 401 | `UNAUTHENTICATED` | 重新登录后再试 |
| 404 | `NOT_FOUND` | 当前账号没有精灵 |
| 409 | `SPIRIT_NOT_AWAY` | 精灵已经在家。藏起召回，以随后的房间状态为准 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一个 `client_id` 不能换内容。保留第一次的结果 |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | `retryable=true`。用原 `client_id` 再发 |
| 422 | `INVALID_INPUT` | 字段不合格。外出状态不变 |
| 503 | `DEPENDENCY_UNAVAILABLE` | `retryable=true`。用原 `client_id` 再发 |

`SPIRIT_NOT_LOST` 不是这个接口的现行错误码。

已经在家时不要连点、不要换新的 `client_id` 重试。那一次会得到 `SPIRIT_NOT_AWAY`，房间保持 `home`。

---

## 4. 建议顺序

1. 打开房间。`status` 为 `away` 时显示召回。
2. 点一次。保存这个 `client_id`，直到这一次得到成功或不可重试的失败。
3. 成功后房间变为 `home`，召回入口消失。
4. 用同一个 `client_id` 再发一次。仍是成功，`patch.spirit.version` 不比第一次更大。
5. 在家时再发一个新的 `client_id`。得到 `409` `SPIRIT_NOT_AWAY`。
