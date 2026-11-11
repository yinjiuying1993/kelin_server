# 刻灵 V4 iOS 联调：精灵聊天

登录、Base URL、ATS、`Authorization: Bearer` 沿用 [`ios-integration.md`](ios-integration.md)。本机 API 是 `http://192.168.100.212:8000`，WebSocket 是 `ws://192.168.100.212:8000/api/v1/spirit-chats/stream`。探活仍是 `GET /health/live`。

这批接口只在本机。公网还没有。不要对公网地址联调精灵聊天。

房间对话 `POST /api/v1/chat` 不是这场聊天。不要调用它来发精灵聊天文字，也不要等模型回复。文字由人发送，服务端不生成对方的回复。

HTTP 字段以 `fixtures/manifest.json` 的 `openapi_sha256` 为准：

`357c2656f55d035debba7a03122143170ac1f32767eebab65b007cc3b9c2238f`

WebSocket 不在这份 OpenAPI 里。事件包以本文第 4 节为准。App 不校验指纹。`data` 里多出来的键先留原始 JSON，不要当成失败。

本机库版本 `20261110_0138`。改完服务端后只重启 **api**。不要重启 worker 来等聊天推送。聊天推送不走问艺评分的 worker。

当前头像地址的主机名是 `kelin.invalid`。这是服务端给出的地址，图片本体在对象存储。客户端原样保存这个字符串，不要自己改主机名。图下不来时用占位图，把地址打在调试日志里。

建议对接口的顺序。准备两个已有精灵的账号，下面称甲、乙：

1. 甲 `GET /api/v1/spirits/me`。看到原名、空昵称、默认头像地址、`avatar_is_default=true`。
2. 甲保存昵称，再读自己的精灵页。显示名变成昵称。超过 12 字被拒绝后，上一次的昵称还在。
3. 乙 `GET /api/v1/spirits/{甲的 spirit_id}/public`。`display_name` 是甲的昵称，`avatar_url` 是甲当前的头像。没有邀请码、阶段、相伴天数。
4. 甲连上 WebSocket，向乙的 `spirit_id` 发一条文字。收到 `message.ack`，再收到自己的 `thread.updated`。
5. 乙在线时收到 `message.created` 和 `thread.updated`。乙不在线时什么都不推。乙用列表和补洞接口能看到这条文字。
6. 乙打开窗口后发 `thread.read`。未读变 0，文字还在。拉消息的 HTTP 不会把未读清掉。
7. 甲侧滑删除，发 `thread.clear`。甲的列表里这场消失。乙仍能读到这些文字，乙的连接上没有删除事件。
8. 乙再写一条。甲的列表重新出现，甲只能看见这条新文字。
9. 甲改昵称或头像。乙在线时收到 `thread.updated`，里面是新的显示名和头像地址。甲自己的列表行仍是乙。

---

## 1. 信封与身份

每条 HTTP 带 RS256 access token。业务 JSON 里不放 `user_id`。建议带 `X-Request-ID`。`Content-Type: application/json`。

成功包：`ok=true`，`error=null`，业务在 `data`。失败包：`ok=false`，`data=null`。界面看 `error.code`，不要把英文 `message` 直接显示给用户。

| HTTP | `error.code` | 客户端 |
| :--- | :--- | :--- |
| 401 | `UNAUTHENTICATED` | 重新登录后再试 |
| 404 | `NOT_FOUND` | 没有这只精灵、这场会话，或这不是自己的会话。不要换一个码去探测别人的精灵 |
| 409 | `SPIRIT_UNAVAILABLE` | 会话已经存在，但对方精灵已不可用。旧文字仍可按水位读，不要发新文字 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一个 `client_id` 换了内容 |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | 上一笔还在处理。`retryable=true`，用原 `client_id` 再发 |
| 422 | `INVALID_INPUT` | 昵称、头像、文字或命令字段不合格 |
| 422 | `INVALID_CURSOR` | 丢掉 cursor，从第一页重拉 |
| 422 | `MODERATION_REJECTED` | 文字、昵称或头像未通过审核。已生效的内容不变 |
| 503 | `DEPENDENCY_UNAVAILABLE` | `retryable=true`。目录或安全服务暂时不可用 |
| 504 | `PROVIDER_TIMEOUT` | `retryable=true`。文字用原 `client_id` 再发 |

WebSocket 上的业务拒绝用同样的 `error.code`，但写在帧里，不使用这些 HTTP 状态。见第 4 节。

`client_id` 在用户确认这一次发送或删除时生成，UUID。超时、断线后原样重放。新动作用新的 UUID。`thread.read` 没有 `client_id`。

---

## 2. 自己的精灵页

入口是主人自己的精灵页。设置接口 `PATCH /api/v1/spirit` 只改原名和偏好，不写昵称、不传头像。原名改成功后，已有聊天里对方看见的显示名会跟着变：没设昵称时显示原名。

### 2.1 读取

`GET /api/v1/spirits/me`。

`data` 含 `id`、`name`、`nickname`、`avatar_url`、`avatar_is_default`、`stage`、`companion_days`、`invite_code`、`help_count`、`skills`。`skills` 只有 `title` 和 `rank_name`。没有房间对话，没有记忆正文。

`nickname` 未设置时是 `null`。对外显示名用昵称，否则用 `name`。`avatar_is_default=true` 时，`avatar_url` 是创建精灵时分配的那张默认图，同一只精灵不会因为重新请求而换成另一张。

没有精灵是 404。

### 2.2 保存昵称

`PUT /api/v1/spirits/me/nickname`。

```json
{ "nickname": "小名" }
```

去掉首尾空白后：空字符串清空昵称，显示名回到原名；1–12 字生效；超过 12 字是 422 `INVALID_INPUT`，上一次昵称不变。审核拒绝是 422 `MODERATION_REJECTED`，超时是 504，已生效的昵称不变。成功 `data` 与 2.1 相同。

### 2.3 上传头像与恢复默认

`POST /api/v1/spirits/me/avatar`。

```json
{ "content_base64": "<JPEG 原始字节的 base64，不要带 data: 前缀>" }
```

只接受 JPEG，最大 5MB。不是 JPEG、过大、审核拒绝：保持原头像，并返回失败。成功后 `avatar_is_default=false`。

`POST /api/v1/spirits/me/avatar/default` 没有 body。去掉自定义头像，回到创建时那张默认图。

### 2.4 公开详情

`GET /api/v1/spirits/{spirit_id}/public`。登录用户可看别人的精灵。

`data` 在原有字段外有 `display_name` 和 `avatar_url`：`id`、`name`、`display_name`、`avatar_url`、`help_count`、`skills`。没有邀请码、阶段、相伴天数，没有用户主键。

---

## 3. 只读 HTTP

三条都只读。不写文字，不清未读，不创建会话。分页默认 30，最大 50。`cursor` 不透明，原样回传。换了用户或换了一场会话就丢掉。服务端按时间从新到旧给出一页，客户端在这一页内再按 `seq` 从小到大显示。

### 3.1 会话列表

`GET /api/v1/spirit-chats?cursor=&limit=`。

只含自己还能看见的会话。每一条是对方精灵，不是自己。

```json
{
  "items": [
    {
      "thread_id": "00000000-0000-4000-8000-0000000000a4",
      "spirit_id": "00000000-0000-4000-8000-0000000000a6",
      "display_name": "小名",
      "avatar_url": "https://kelin.invalid/spirit-avatar/default-1",
      "peer_available": true,
      "text": "你好",
      "created_at": "2026-11-10T08:00:00Z",
      "unread": 1
    }
  ],
  "unread_total": 1,
  "has_more": false,
  "next_cursor": null
}
```

`unread_total` 是全部可见会话的未读之和，不只是这一页。空列表是成功，`items` 为空，`unread_total` 为 0。`peer_available=false` 时仍显示这一行，但不要再发新文字。

### 3.2 按精灵打开

`GET /api/v1/spirit-chats/by-spirit/{spirit_id}?cursor=&limit=`。

还没有会话，且对方可用：成功。`thread_id` 为 `null`，`messages` 为空，`peer_available` 为 true。这时不要插入本地会话，等第一条 `message.ack` 拿到 `thread_id`。

还没有会话，且对方不存在或不可用：404。已有会话时不再为了名称失败；不可用时 `peer_available=false`，旧文字仍按水位返回，码 `SPIRIT_UNAVAILABLE` 只出现在再次发送时。

`display_name`、`avatar_url` 是对方。`own_display_name`、`own_avatar_url` 是自己，用来画自己的气泡。给自己的精灵打开是 404。

### 3.3 补洞

`GET /api/v1/spirit-chats/{thread_id}/messages?cursor=&limit=`。

参与者才可读。只返回自己还能看见的文字。每条是 `id`、`seq`、`mine`、`text`、`created_at`。没有头像，没有用户主键。不是参与者是 404。

重连、冷启动、下拉刷新走这三条。在线推送可能丢失，以这里的序号为准。同一 `client_id` 已经成功过的文字，用 `seq` 或 `message_id` 认出，不要再画成第二条。

---

## 4. WebSocket

`GET /api/v1/spirit-chats/stream` 升级为 WebSocket。一个登录用户同时只留一条连接，覆盖他的全部精灵会话。新连接替换旧连接，旧连接关闭码 1000。

握手带和 HTTP 相同的 access token。优先放请求头 `Authorization: Bearer <token>`。做不到时用查询参数 `access_token`。不要把 token 打进日志。

鉴权失败时升级不完成，本机看到的是 HTTP 403。不要等一个 WebSocket 关闭码。连接建立之后：

| 关闭码 | 含义 |
| :--- | :--- |
| 1000 | 这条连接被同一用户的新连接替换 |
| 4402 | 连接期间 token 过期 |
| 4403 | 连续 60 秒没有收到应用层 `ping` |

业务拒绝不关闭连接。空字、审核拒绝、不是参与者、超限，都写在帧里。

每 30 秒发一帧 `{"type":"ping"}`。服务端回 `pong`。只有 `ping` 能重置 60 秒计时，发送和已读不能代替心跳。`pong` 的 `event_id` 是服务端生成的 UUID，`data` 为空对象。

帧不是 HTTP 信封。没有 `ok`、`request_id`、`server_time`。

| 字段 | 规则 |
| :--- | :--- |
| `type` | 见下表。不认识的服务端事件先忽略，不要断开 |
| `event_id` | 推送是服务端生成的 UUID。`message.ack` 沿用这次的 `client_id` |
| `thread_id` | 已有会话时带上。第一条还没有会话时可能为 `null` |
| `data` | 这一类事件自己的内容。没有用户主键 |

| 方向 | `type` | `data` |
| :--- | :--- | :--- |
| 客户端 → 服务端 | `message.send` | `client_id`、`peer_spirit_id`、`text` |
| 客户端 → 服务端 | `thread.read` | 只有 `thread_id` |
| 客户端 → 服务端 | `thread.clear` | `client_id`、`thread_id` |
| 客户端 → 服务端 | `ping` | 可省略 |
| 服务端 → 客户端 | `message.ack` | 成功时是落库结果和对方名片。失败且带了 `client_id` 时是 `error.code` |
| 服务端 → 客户端 | `message.created` | 发给还能看见这条文字的另一方 |
| 服务端 → 客户端 | `thread.updated` | 列表行。从自己列表消失时 `hidden=true` |
| 服务端 → 客户端 | `error` | 没有对应 `message.ack` 的失败 |
| 服务端 → 客户端 | `pong` | 心跳应答 |

10 秒内最多 30 条命令。`ping` 不算。超出时回 `error`，码 `RATE_LIMITED`，这条命令没有落库。未知 `type` 回 `error`，码 `INVALID_INPUT`，连接保持。

### 4.1 发送

```json
{
  "type": "message.send",
  "data": {
    "client_id": "00000000-0000-4000-8000-0000000000a5",
    "peer_spirit_id": "00000000-0000-4000-8000-0000000000a6",
    "text": "你好"
  }
}
```

去掉首尾空白后 1–500 字。空字和超长不落库。

成功：

```json
{
  "type": "message.ack",
  "event_id": "00000000-0000-4000-8000-0000000000a5",
  "thread_id": "00000000-0000-4000-8000-0000000000a4",
  "data": {
    "message_id": "00000000-0000-4000-8000-0000000000a7",
    "seq": 1,
    "text": "你好",
    "created_at": "2026-11-10T08:00:00Z",
    "peer_spirit_id": "00000000-0000-4000-8000-0000000000a6",
    "display_name": "小名",
    "avatar_url": "https://kelin.invalid/spirit-avatar/default-1",
    "available": true
  }
}
```

`message.ack` 先到。自己的 `thread.updated` 随后单独到，用来刷新列表。不要等推送才把这条显示在自己的窗口里。

失败且请求里有合法的 `client_id`：仍是 `message.ack`，`data` 只有 `error.code`。没有 `client_id` 时是 `error`，不是 `message.ack`。

| `error.code` | 含义 |
| :--- | :--- |
| `NOT_FOUND` | 没有对方精灵、不能看、或就是自己的精灵。没有会话时，不可用也是这个码 |
| `SPIRIT_UNAVAILABLE` | 会话已在，对方已不可用。不要写新文字 |
| `MODERATION_REJECTED` | 文字未通过。不落库 |
| `INVALID_INPUT` | 空字、超长，或字段不是 UUID / 字符串 |
| `IDEMPOTENCY_CONFLICT` | 同一个 `client_id` 换了文字 |
| `IDEMPOTENCY_IN_PROGRESS` | 用原 `client_id` 再发 |
| `PROVIDER_TIMEOUT` | 用原 `client_id` 再发 |
| `DEPENDENCY_UNAVAILABLE` | 目录或安全服务暂时不可用，可再发 |

同一 `client_id`、同一段文字再发，返回第一次的 `message.ack`，不会有第二条。

### 4.2 对方在线时收到的两帧

`message.created` 的 `data` 是 `message_id`、`seq`、`text`、`mine`。推送给对方时 `mine` 为 false。这帧没有 `created_at`。时间以补洞接口为准，排序以 `seq` 为准。

`thread.updated` 的 `data`：

| 字段 | 含义 |
| :--- | :--- |
| `peer_spirit_id` | 列表上的对方精灵 |
| `display_name` | 对方显示名 |
| `avatar_url` | 对方头像地址 |
| `available` | 对方是否还能收新文字 |
| `text` | 自己能看见的最后一条文字 |
| `unread` | 这一行的未读 |
| `hidden` | true 表示这场从自己的列表消失 |

对方离线时这两帧不会补推。对方下次打开用第 3 节补洞。未读还在。

### 4.3 清未读

```json
{
  "type": "thread.read",
  "data": { "thread_id": "00000000-0000-4000-8000-0000000000a4" }
}
```

成功没有直接应答。接着收一条自己的 `thread.updated`，`unread` 为 0，`hidden` 为 false，文字还在。重复发送结果相同。不是参与者时收 `error`，码 `NOT_FOUND`。

拉历史的 HTTP 不要顺便发已读。窗口打开后再发 `thread.read`。

### 4.4 侧滑删除

```json
{
  "type": "thread.clear",
  "data": {
    "client_id": "00000000-0000-4000-8000-0000000000a5",
    "thread_id": "00000000-0000-4000-8000-0000000000a4"
  }
}
```

成功没有直接应答。自己收到 `thread.updated`，`hidden` 为 true，`text` 为空。从列表拿掉这一行。服务端仍保留文字。对方收不到删除事件，对方的列表和历史不变。

同一个 `client_id` 再发，水位停在第一次。对方之后新写的一条会让自己的列表重新出现，自己只能看见这条新文字以及更晚的文字。

### 4.5 资料变化

甲改昵称、换头像、恢复默认、改原名之后，乙如果在线，会收到 `thread.updated`。`display_name` 和 `avatar_url` 换成甲的新资料。已经画在屏幕上的气泡也改用这场会话当前的头像，气泡里没有单独存过头像。

甲自己的列表行仍是乙。甲正开着窗口时，可能收到一条 `thread.updated`：`peer_spirit_id`、`display_name`、`avatar_url` 仍是乙，同时多出 `own_display_name` 和 `own_avatar_url`。用这两项换自己的气泡，不要把列表换成甲自己。

乙已不可用时，`available` 为 false。历史仍可按水位读。再发送会得到 `SPIRIT_UNAVAILABLE`。

两次发送之间，列表可能短暂显示旧名字或旧头像。下一次发送成功后，以 `message.ack` 和随后的 `thread.updated` 为准。

---

## 5. 联调核对

- [ ] 真机打开 `http://192.168.100.212:8000/health/live`，得到 `{"status":"live"}`
- [ ] 无 token 的 HTTP 聊天接口是 401。无 token 的 WebSocket 升级是 403
- [ ] 两个账号各有一只精灵。甲看不到自己的精灵公开页被当成聊天对象
- [ ] 昵称 13 字被拒绝，页上仍是上一次的昵称
- [ ] 乙的公开详情能看到甲的 `display_name` 和 `avatar_url`，没有邀请码
- [ ] 甲发送后先有 `message.ack`，列表出现乙。乙在线时收到 `message.created`，`mine` 为 false
- [ ] 乙断线后甲再发一条。乙重连后列表和补洞能看到，序号连续，没有重复气泡
- [ ] `thread.read` 之后未读为 0。只拉 HTTP 消息时未读不变
- [ ] `thread.clear` 后甲的列表没有这场。乙仍能读到旧文字
- [ ] 乙再发一条后，甲只看见这条新文字
- [ ] 甲改昵称后，乙在线时气泡和列表换成新名字。甲的列表仍显示乙
- [ ] 日志里没有 token、正文、昵称、头像地址和对方用户 id
