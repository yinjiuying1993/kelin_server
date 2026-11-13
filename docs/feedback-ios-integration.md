# 刻灵 iOS 联调：意见书

登录、Base URL、ATS、`Authorization: Bearer` 沿用 [`ios-integration.md`](ios-integration.md)。本机 API 是 `http://192.168.100.212:8000`。

这次接两件事：新建精灵时三份同意书的版本号，以及 `POST /api/v1/feedback`。对本机新包联调。公网当前包没有意见书接口，新建精灵也还收旧版本号，不要对公网验证。本机要先把数据库升到 `20261112_0139`，再只重启 **api**。

协议正文仍在客户端。服务端不返回用户协议或隐私政策，也不要求已经有精灵的用户重新同意。

不要展示 `error.message`。

---

## 1. 新建精灵的同意版本

只影响还没有精灵、这次要 `POST /api/v1/spirits` 的用户。三份 `document_version` 都写成 `2026-11-12`。

```json
{
  "client_id": "00000000-0000-4000-8000-0000000000b1",
  "egg": "warm",
  "name": "雾生",
  "consents": {
    "ai_disclosure": {
      "document_version": "2026-11-12",
      "explicitly_accepted": true
    },
    "user_terms": {
      "document_version": "2026-11-12",
      "displayed": true
    },
    "data_notice": {
      "document_version": "2026-11-12",
      "displayed": true
    }
  }
}
```

`explicitly_accepted` 和两个 `displayed` 必须是 `true`。版本号不是 `2026-11-12`、布尔值为 false、缺字段，都是 422 `INVALID_INPUT`。这次不会创建精灵。

已经创建成功的 `client_id` 再提交，仍返回当时那次创建结果。不要为了新版本号让老用户再走一遍创建。设置里打开协议只在本地阅读，不要再打创建接口。

---

## 2. 提交意见

`POST /api/v1/feedback`。`Content-Type: application/json`。建议带 `X-Request-ID`。

用户点下发送时生成一个 UUID，放进 `client_id`。这个 id 只给意见书用，不要复用房间对话、问艺或召回的 `client_id`。

```json
{
  "client_id": "00000000-0000-4000-8000-0000000000c1",
  "text": "夜间回复太长了"
}
```

| 规则 | 要求 |
| :--- | :--- |
| 字段 | 只有 `client_id` 和 `text`。联系方式、图片、精灵 id、评分都会 422 |
| 正文 | 服务端去掉首尾空白后再计字。按 Unicode 码点，1 到 1000 |
| 空或超长 | 422 `INVALID_INPUT`。服务端不截断。这次还没记下，改正文后可以沿用同一个 `client_id` |
| 用户 | 只认登录令牌。请求体不要带 `user_id` |

---

## 3. 成功

HTTP 200。`ok` 为 true。`data` 只有 `id`。

```json
{
  "ok": true,
  "data": {
    "id": "00000000-0000-4000-8000-0000000000d1"
  },
  "error": null,
  "request_id": "00000000-0000-4000-8000-0000000000f4",
  "server_time": "2026-11-12T00:00:00Z"
}
```

没有 `patch`、`quotas`、`events`。精灵快照不变，不要刷新房间。没有列表和回复接口，拿到 `id` 后不用再查。界面可以清空输入。

同一个 `client_id`、去掉首尾空白后的正文也相同，再提交一次，返回同一个 `id`。这是重放，不是第二条意见。

---

## 4. `client_id` 怎么换

| 结果 | 下次请求 |
| :--- | :--- |
| 200，拿到 `id` | 用户再写一条新意见时换新的 `client_id` |
| 422 `MODERATION_REJECTED` | 保留输入。原文再发仍会拒绝。用户改了字，必须换新的 `client_id` |
| 409 `IDEMPOTENCY_CONFLICT` | 这个 `client_id` 已经用过另一段正文。换新的 `client_id` |
| 409 `IDEMPOTENCY_IN_PROGRESS` | 同一次还在处理。`retryable` 为 true，看 `error.details.retry_after_ms`（现为 500）后再发。`client_id` 和正文都不变 |
| 429 `RATE_LIMITED`、503 `DEPENDENCY_UNAVAILABLE`、504 `PROVIDER_TIMEOUT` | 没有记成终态。过后再用同一个 `client_id` 和同一正文 |
| 401 `UNAUTHENTICATED` | 先重新登录。`client_id` 可以保留 |

审核拒绝不是成功。不要清空输入，也不要把拒绝当成已提交。

---

## 5. 错误

| HTTP | code | 界面 |
| :---: | :--- | :--- |
| 401 | `UNAUTHENTICATED` | 重新登录 |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | 稍后原样重试 |
| 409 | `IDEMPOTENCY_CONFLICT` | 换新的 `client_id` |
| 422 | `INVALID_INPUT` | 正文空了、超过 1000 字，或带了多余字段 |
| 422 | `MODERATION_REJECTED` | 留下原文，请用户修改后再发 |
| 429 | `RATE_LIMITED` | 稍后原样重试。没有每日次数 |
| 503 | `DEPENDENCY_UNAVAILABLE` | 稍后原样重试 |
| 504 | `PROVIDER_TIMEOUT` | 稍后原样重试 |

---

## 6. 联调顺序

1. 确认打的是 `http://192.168.100.212:8000`，不是公网。
2. 新用户创建精灵，三份 `document_version` 都是 `2026-11-12`，然后能进房间。
3. 已有精灵的账号不要再调创建接口。
4. 提交一句正常意见，响应里只有 `data.id`。用同一个 `client_id` 和同一句再提交，`id` 不变。
5. 把这句改几个字后仍用旧 `client_id`，应得到 409 `IDEMPOTENCY_CONFLICT`。换新 `client_id` 可以再提交。
6. 空串、只含空白、超过 1000 字，都是 422，输入还在。
7. 未登录是 401。
