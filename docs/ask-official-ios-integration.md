# 刻灵 iOS 联调：问艺官方精灵标识

登录、Base URL、ATS、`Authorization: Bearer` 沿用 [`ios-integration.md`](ios-integration.md)。问艺的提交、列表、追问和评分仍以 [`v3-ios-integration.md`](v3-ios-integration.md) 为准。精灵聊天以 [`v4-ios-integration.md`](v4-ios-integration.md) 为准。本机 API 是 `http://192.168.100.212:8000`。

这次只接 `official`。对本机 API 联调。公网当前包还没有这个字段，不要对公网验证。改完服务端后只重启 **api**。

`official` 表示这场问艺对手是不是官方精灵。为 `true` 时隐藏精灵对话按钮。为 `false` 时可以按 `spirit_id` 打开精灵聊天。不要用精灵名字判断，名字以后可以改。

---

## 1. 字段出现的位置

布尔值，必有，不会是 `null`。

| 接口 | 位置 |
| :--- | :--- |
| `POST /api/v1/ask/questions` | `data.question.official`。只在 `matched` 为 true 时有 `question` |
| `GET /api/v1/ask/questions/recent` | `data.items[].official` |
| `GET /api/v1/ask/questions` | `data.items[].official` |
| `GET /api/v1/ask/questions/{question_id}` | `data.official` |

提交没有匹配到公开技艺时，服务端改问官方精灵，仍然是 `matched: true`。这时 `official` 为 true，`spirit_name` 是「官方精灵」，`skill_title` 是「万能应答」，`rank_name` 是 null。这是成功，不要换 `client_id` 重试。

`matched` 为 false 时没有 `question`，也没有 `official`。界面按未匹配处理。

---

## 2. 按钮

| `official` | `spirit_id` | 界面 |
| :--- | :--- | :--- |
| `false` | 有值 | 显示精灵对话。用这个 `spirit_id` 走精灵聊天 |
| `true` | 有值 | 隐藏精灵对话。不要给这只精灵发精灵聊天，也不要打开它的聊天窗口 |
| `false` | `null` | 隐藏精灵对话。没有对手 id，不要按名字去查 |

问艺里的追问仍走 `POST /api/v1/ask/questions/{question_id}/turns`。官方精灵这场也可以追问、也可以评分。追问成功只返回 `question_id` 和 `reply`，没有 `official`；按钮状态沿用打开这场对话时记下的值。

房间对话 `POST /api/v1/chat` 不是精灵聊天，也不要用来给官方精灵发消息。

公开资料 `GET /api/v1/spirits/{spirit_id}/public` 没有 `official`。是否隐藏对话按钮只看问艺记录上的 `official`。

---

## 3. 示例

匹配到普通精灵：

```json
{
  "matched": true,
  "question": {
    "id": "00000000-0000-4000-8000-0000000000a1",
    "question": "茶叶用多少度的水",
    "spirit_name": "雾生",
    "spirit_id": "00000000-0000-4000-8000-0000000000a8",
    "official": false,
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

落到官方精灵：

```json
{
  "matched": true,
  "question": {
    "id": "00000000-0000-4000-8000-0000000000a2",
    "question": "再问一次水温",
    "spirit_name": "官方精灵",
    "spirit_id": "00000000-0000-4000-8000-0000000000f2",
    "official": true,
    "rank_name": null,
    "skill_title": "万能应答",
    "rated": false,
    "rating": null,
    "turns": [
      { "role": "user", "text": "再问一次水温" },
      { "role": "spirit", "text": "官方回复" }
    ]
  }
}
```

本机没配真实模型时，普通匹配的回复文本是 `问艺回复`，官方精灵的回复文本是 `官方回复`。配了真实模型后以模型返回为准。示例里的 `spirit_id` 只说明字段形状。

列表项没有 `turns`，其余字段相同，包括 `official`。最近三条、全部记录和详情都要按同一规则决定按钮。

---

## 4. 建议顺序

1. 用一个公开技艺匹配成功。`official` 为 false，对话按钮可见。
2. 把该技艺设为私密后再提一个新问题。`matched` 为 true，`official` 为 true，对话按钮不可见。
3. 打开最近三条和这条详情。`official` 仍为 true。
4. 对这场官方问艺发一条追问。追问成功，按钮仍然隐藏。
5. 同一 `client_id` 重放第 2 步。`official` 仍为 true，不要新开一场。
