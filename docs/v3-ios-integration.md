# 刻灵 V3 iOS 开发与联调

传艺、问艺给 iOS 对接用。登录、Base URL、ATS、`Authorization: Bearer` 沿用 [`ios-integration.md`](ios-integration.md)。房间、记忆、聊天、串门、鉴定不在本文。

字段以 `fixtures/manifest.json` 的 `openapi_sha256` 为准：

`ddd96b5a4f4b6350747a4561618d33fce193432221e13cf13fa2f82178921fdb`

App 不校验这段指纹。对 `data` 里的字段解码。多出来的键按 422 处理，调试时把原始 JSON 留下来。

人工验收已通过，当前待联调。库版本 `20261104_0128`。传艺、问艺业务路由都已接通。改完服务端后重启 **api** 和 **worker**，再开始对。

`/api/v1/v3/*` 仍是内部占位，返回 501，不要调用。没有传艺或问艺的推送。没有 App 内支付。`/api/v1/ops/craft-redeem` 不是客户端接口。

本机没配真实模型时，匹配会选中这次材料里的公开技能，再由服务端确认；自测回复固定是 `自测回复`，问艺回复固定是 `问艺回复`。配了真实模型后，以模型返回为准，不要把这两句写进 UI 文案。评分成功后，主人侧的分数和帮助次数要等 worker 跑完才变。

建议对接口的顺序：

1. 已有精灵后，`GET /api/v1/craft/skills` 看到空列表、总分 0。
2. `POST /api/v1/craft/skills` 创建。同一 `client_id`、同一内容再发一次，`id` 不变。同一 `client_id` 换内容是 409。
3. 列表、详情、`PATCH` 保存。`id` 不变。
4. 自测开始，再发一句。回复是 `自测回复`。这项不进问艺，不加分。
5. 另一个已有精灵的账号 `POST /api/v1/ask/questions`。匹配成功时 `matched` 为 true，`spirit_name` 是对方精灵名。
6. 最近三条、全部记录、详情、追问、评分。再评一次仍是第一次的等级。
7. 确认 worker 在跑，再拉主人的技能列表、`GET /api/v1/craft/redeem`、`GET /api/v1/ask/helped`。满意一次后总分是 5，帮助次数是 1。
8. 提问者拉 `GET /api/v1/spirits/{spirit_id}/public`，能看到标题和职称，看不到正文。
9. 主人把技能设为私密后再提问，落到官方精灵。`matched` 为 true，`spirit_name` 是「官方精灵」，`skill_title` 是「万能应答」。这是成功。
10. 删除后，主人列表为空；公开详情里 `skills` 为空，帮助次数还在。

---

## 1. 信封与身份

每个 `/api/v1` 请求带 RS256 access token。业务 body 里不放 `user_id`。建议带 `X-Request-ID`。`Content-Type: application/json`。

成功包：`ok=true`，`error=null`，业务在 `data`。失败包：`ok=false`，`data=null`。界面看 `error.code`，不要把英文 `message` 直接显示给用户。

| HTTP | `error.code` | 客户端 |
| :--- | :--- | :--- |
| 401 | `UNAUTHENTICATED` | 重新登录后再试 |
| 404 | `NOT_FOUND` | 没有这只精灵、这项技能、这场对话、这张自测凭证。别人的资源也是这个码，不要换一个码去探测 |
| 409 | `ASK_CLOSED` | 这场问艺已经评过分，不能再追问 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一个 `client_id` 换了内容。`message` 是 `request failed` |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | 上一笔还在处理。`retryable=true`，用原 `client_id` 再试 |
| 422 | `INVALID_INPUT` | 字段不合格。技能正文可能带 `error.details.field` |
| 422 | `INVALID_CURSOR` | 丢掉 cursor，从第一页重拉 |
| 422 | `MODERATION_REJECTED` | 内容未通过审核 |
| 500 | `INTERNAL_ERROR` | 带 `request_id` 回传 |
| 501 | `NOT_IMPLEMENTED` | 只出现在 `/api/v1/v3/*`，业务路由不会返回这个码 |
| 503 | `DEPENDENCY_UNAVAILABLE` | `retryable=true`，原请求再试 |

未知字段、类型不对，也是 422。

`client_id` 在用户确认这一次动作时生成，UUID。失败、超时、杀进程后原样重放。新动作用新的 UUID。

资源 id 都是 UUID：技能 `id`、问题 `id`、精灵 `id`、自测 `ticket`。响应里没有技能 slug。不要展示 slug，也不要自己编一个字段去要。

---

## 2. 传艺

先有精灵（既有 `POST /api/v1/spirits`）。未创建精灵时不要进传艺。

列表每页 20 条。`cursor` 不透明，原样回传。换了筛选就丢掉。技能列表没有筛选参数。

主人列表里，还没公开成功的技能以 `visibility=private` 出现。客户端只提交 `public` 或 `private`。

`description` 是主人写的详细描述，原样往返。服务端内部另有一段匹配说明，不出现在响应里。

写入时按这些规则拒绝，`details.field` 用内部名字：

| `details.field` | 对应请求字段 | 原因 |
| :--- | :--- | :--- |
| `title` | `title` | 空白，或含换行 |
| `keywords` | `keywords` | 没有关键词，或每一条都和标题相同 |
| `detail` | `description` | 空白，或只有「帮忙」「处理好」和标点 |
| `reference_links` | `reference_links` | 和标题、描述一起超过正文行数上限 |

`keywords` 至少一条。`reference_links` 可以是空数组。不要传 skill id、正文 markdown、积分。

### 2.1 创建技能

`POST /api/v1/craft/skills`。

```json
{
  "client_id": "00000000-0000-4000-8000-000000000001",
  "title": "泡茶",
  "keywords": ["水温"],
  "description": "先烧水，再闷三分钟。",
  "reference_links": []
}
```

成功 `data` 是技能详情，`visibility` 为 `public`。同一 `client_id` 且内容相同，返回第一次的技能，`id` 不变。内容不同是 409。没有精灵是 404。

### 2.2 技能列表

`GET /api/v1/craft/skills`。可选 `cursor`。

```json
{
  "total_points": 320,
  "items": [
    {
      "id": "00000000-0000-4000-8000-0000000000c1",
      "title": "泡茶",
      "visibility": "public",
      "points": 120,
      "rank_name": "搭手帮工",
      "points_to_next": 180,
      "at_max_rank": false,
      "updated_at": "2026-11-03T02:00:00Z"
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

`total_points` 是这只精灵的账本总分，不是当前页 `points` 相加。0 分时 `rank_name` 为 null，`points_to_next` 为 1，`at_max_rank` 为 false。已删除的技能不在列表里。

页顶「距兑换还差」用 `max(0, 1000 - total_points)`。不要把单技能的 `points_to_next` 当成兑换差额。

### 2.3 技能详情

`GET /api/v1/craft/skills/{craft_id}`。

详情比列表多 `keywords`、`description`、`reference_links`。别人的技能、已删除的技能都是 404。

### 2.4 保存

`PATCH /api/v1/craft/skills/{craft_id}`。body 与创建相同，必须带新的或重放用的 `client_id`。

成功后 `id` 不变。进行中的问艺仍用保存前的快照，不在这个响应里。

### 2.5 公开或私密

`POST /api/v1/craft/skills/{craft_id}/visibility`。

```json
{
  "client_id": "00000000-0000-4000-8000-000000000002",
  "visibility": "private"
}
```

`visibility` 只有 `public`、`private`。成功返回详情。私密后，新的提问匹配不到这项技能。已经开聊的对话不受影响。

### 2.6 删除

`DELETE /api/v1/craft/skills/{craft_id}`。body 只有 `client_id`。

有的 HTTP 客户端会丢掉 DELETE body，这个接口必须把 JSON body 发出去。

成功：

```json
{ "id": "00000000-0000-4000-8000-0000000000c1", "deleted": true }
```

删除后该项离开列表和匹配。已积累的分数留在服务端，不返回给这个接口。再教一门新技能会得到新的 `id`，不要复用旧的。

### 2.7 自测

自测不进问艺记录，不加分，不加帮助次数。没配真实模型时，`reply` 就是 `自测回复`。

开始：`POST /api/v1/craft/skills/{craft_id}/self-test`。没有 body。

```json
{
  "ticket": "00000000-0000-4000-8000-0000000000c3",
  "expires_at": "2026-11-03T00:30:00Z"
}
```

凭证 30 分钟有效。过期、别人的技能、不存在的技能，都是 404。保存技能之后，旧凭证仍指向开始时的正文；要测新正文就重新开始。

一句：`POST /api/v1/craft/self-test/turns`。

```json
{
  "ticket": "00000000-0000-4000-8000-0000000000c3",
  "text": "水温大概多少"
}
```

`text` 去掉首尾空白后 1–500 字。成功 `data` 只有 `reply`。服务端不保存自测对话，下一句也不会带上上一句。界面上的记录由客户端自己排，请求里不要加历史字段。

审核未过是 422 `MODERATION_REJECTED`，这时没有回复。模型没有给出正文是 503，用同一张凭证再发这一句。

### 2.8 兑换资格

`GET /api/v1/craft/redeem`。没有 body。

```json
{
  "eligible": false,
  "total_points": 5,
  "redeemed": false
}
```

`eligible` 为 true 的条件只有：`total_points >= 1000` 且 `redeemed` 为 false。已兑换时 `eligible` 为 false，`redeemed` 为 true，分数不变。没有精灵是 404。没有账本时总分是 0，`eligible` 为 false。

客户端没有兑付写入接口。资格够了只做「联系客服」。不要发支付、订单或 ops 请求。

---

## 3. 问艺

没配真实模型时，精灵回复文本是 `问艺回复`。下面示例里的长句是字段形状，不是本机固定文案。

提问者必须已经有自己的精灵。问自己的精灵按 404 处理，不要做「不能问自己」的单独错误码。

问题正文 1–500 字。列表和详情里的 `rank_name` 是开聊时冻结的职称，`skill_title` 是开聊时命中的技能标题。后来对方改职称、改标题、设为私密或删除技能，都不改这两项。没有快照时它们是 `null`，不要回读当前技能来填。`spirit_id` 是这场对话对手精灵的 id，用来打开 `GET /api/v1/spirits/{spirit_id}/public`。精灵改名后 id 不变。没有记下 id 时是 `null`，不要按名字去现表猜。

历史里只出现已经有回复的对话。整场没有回复的，不进最近三条，也不进全部记录。

### 3.1 提交问题

`POST /api/v1/ask/questions`。

```json
{
  "client_id": "00000000-0000-4000-8000-000000000003",
  "text": "茶叶用多少度的水"
}
```

匹配到并有回复：

```json
{
  "matched": true,
  "question": {
    "id": "00000000-0000-4000-8000-0000000000a1",
    "question": "茶叶用多少度的水",
    "spirit_name": "雾生",
    "spirit_id": "00000000-0000-4000-8000-0000000000a8",
    "rank_name": "门边学徒",
    "skill_title": "茶室品茗冲泡手记",
    "rated": false,
    "rating": null,
    "turns": [
      { "role": "user", "text": "茶叶用多少度的水" },
      { "role": "spirit", "text": "先烧开，再放凉到大约八十度。" }
    ]
  }
}
```

没有匹配到公开技艺时，服务端改问固定的官方精灵，仍然是 `matched` 为 true。`spirit_name` 是「官方精灵」，`skill_title` 是「万能应答」，`rank_name` 是 null。不要改问另一只精灵，也不要自动重试出新的 `client_id`。同一 `client_id` 重放返回第一次的结果。给这场对话评分只记在提问人的记录里，不加官方精灵的帮助次数。

`turns` 里至少有一条 `role=spirit`。`rank_name`、`skill_title` 和 `spirit_id` 可以是 null。空字符串不会出现。

审核未过是 422 `MODERATION_REJECTED`，没有对话。

### 3.2 最近三条

`GET /api/v1/ask/questions/recent`。没有 cursor。最多 3 条，字段与下面的列表项相同，包在 `data.items`。不足 3 条就返回实际条数，可以是空数组。

路径是字面量 `recent`。不要把它当成 `question_id`。

### 3.3 全部记录

`GET /api/v1/ask/questions?view=all`。`view` 为 `all`、`rated`、`unrated`，缺省 `all`。每页 20 条。可选 `cursor`。

列表项没有 `turns`：

```json
{
  "id": "00000000-0000-4000-8000-0000000000a1",
  "question": "茶叶用多少度的水",
  "spirit_name": "雾生",
  "spirit_id": "00000000-0000-4000-8000-0000000000a8",
  "rank_name": "门边学徒",
  "skill_title": "茶室品茗冲泡手记",
  "rated": false,
  "rating": null
}
```

已评价时 `rated` 为 true，`rating` 是 `不满意`、`满意` 或 `非常满意`。未评价时 `rating` 为 null。排序以服务端为准，客户端不要重排。

换 `view` 时丢掉 cursor。坏 cursor 是 422 `INVALID_CURSOR`。

### 3.4 详情

`GET /api/v1/ask/questions/{question_id}`。`data` 与提交成功时的 `question` 相同，含 `turns`。不是自己的、或还没有回复的，都是 404。

### 3.5 追问

`POST /api/v1/ask/questions/{question_id}/turns`。

```json
{
  "client_id": "00000000-0000-4000-8000-000000000004",
  "text": "闷多久"
}
```

成功只有：

```json
{
  "question_id": "00000000-0000-4000-8000-0000000000a1",
  "reply": "大约三分钟。"
}
```

界面把用户这句和 `reply` 接到已有 `turns` 后面。要完整记录再拉详情。已经评过分是 409 `ASK_CLOSED`。

### 3.6 评价

`POST /api/v1/ask/questions/{question_id}/rating`。

```json
{
  "client_id": "00000000-0000-4000-8000-000000000005",
  "rating": "满意"
}
```

`rating` 只有 `不满意`、`满意`、`非常满意`。成功返回 `question_id` 和 `rating`，没有分数。再提交一次，无论新等级是什么，都返回第一次的等级。

分数和帮助次数由 worker 入账，评价响应回来时主人侧的总分可能还没变。刷新列表和兑换资格即可，不要在客户端把 5 或 10 加到 `total_points` 上。

入账规则，供对界面，不要在 App 里结算：

| 评价 | 该技能积分 | 帮助次数 |
| :--- | :--- | :--- |
| 不满意 | +0 | +1 |
| 满意 | +5 | +1 |
| 非常满意 | +10 | +1 |

同一场对话只入账一次。未评价不加。技能后来被删除，已入账的分数还在，但这项不再被新问题匹配。

worker 没在跑时，评价可以成功，总分仍不变。联调分数之前先确认 worker 进程在跑。

### 3.7 它帮过的

`GET /api/v1/ask/helped`。没有分页。

有帮助时 `data.item` 为 `{ "spirit_name": "雾生", "help_count": 1 }`。一次都没有时 `item` 为 null，这是成功。没有精灵是 404。

没有标题、职称、时间、评分和问题原文。主人在这里看不到别人问了什么。

### 3.8 精灵公开详情

`GET /api/v1/spirits/{spirit_id}/public`。

```json
{
  "id": "00000000-0000-4000-8000-0000000000a8",
  "name": "雾生",
  "help_count": 2,
  "skills": [
    { "title": "泡茶", "rank_name": "门边学徒" },
    { "title": "温杯", "rank_name": null }
  ]
}
```

技能只有标题和职称。没有描述、积分、差额、关键词、正文、技能 id。没有公开技能时 `skills` 为空数组，精灵名和帮助次数仍在。不存在的精灵是 404。

---

## 4. 职称

列表和详情里的职称以响应为准。这张表只用来对文案。

| 技能积分 | `rank_name` | `points_to_next` | `at_max_rank` |
| :--- | :--- | :--- | :--- |
| 0 | null | 1 | false |
| 1–99 | 门边学徒 | 100 − 积分 | false |
| 100–299 | 搭手帮工 | 300 − 积分 | false |
| 300–599 | 上手匠人 | 600 − 积分 | false |
| 600–999 | 独当师傅 | 1000 − 积分 | false |
| 1000–1499 | 掌作名匠 | 1500 − 积分 | false |
| ≥ 1500 | 镇场宗师 | null | true |

问艺记录上的职称是开聊快照，不按这张表用对方的当前分重算。性格称号仍只在房间和鉴定，不写进传艺职称。

---

## 5. 联调时带回传

失败时记下 HTTP 状态、`error.code`、`request_id`、路径。不要附问题原文、技能正文、关键词、链接或 token。

传艺和问艺的业务路由都可以打。返回 501 的只有 `/api/v1/v3/*`。
