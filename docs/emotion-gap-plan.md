# 情绪模块修改方案

对照三份材料：

- 产品规则：`0_kelin/v1/情绪/kelin_v1_emotion_system_spec.md`
- 算法施工：`0_kelin/v1/情绪/情绪算法模块施工说明书.md`
- 业务施工：`0_kelin/v1/情绪/情绪业务模块施工说明书.md`
- 现行代码：`4_server/docs/emotion.md`

只排「算法或业务施工说明书已经写了、代码没接上」的改动。产品规格有、施工事件表没有的，放在文末，不进实现批次。

情绪仍只写 `spirit_emotion`。饥饿、精力、去向继续由去向模块先算完再读入。不改 iOS，不做动作、光线、天气、通知。不在情绪仓库里写房间；房间仍由现有 `room_public_from_aggregate` 读结算后的 `primary_label` 再翻译成中文。

一次只做下面的一个批次。每批都要有纯函数测试；碰库的批次再补一条 db 测试。不顺手改相邻行为。

## 不改的部分

这些已经和施工说明书一致，不要重写：

- 主标签短路顺序，含「有点饿 / 有点困」。
- 走失固定 `uneasy`；找回 `hurt`，心情 +8，2 小时；一次有效对话清掉 `hurt`。清掉之后按身体档或 `calm`，不强制改成 `close`。算法写的是「允许自然降级」。
- 口粮 `satisfied`、知识 `curious`、约定和共学 `happy`、串门 `satisfied`。
- 默认 TTL、8 小时心情向 50 靠 2、同一 `event_id` 不重复加心情、不延长 TTL。
- 用户投喂 `anxious` 得到 `missing`，不会得到 `uneasy`。
- 生气投喂维持 `missing` 候选，不改成 `irritated`。产品 §6.9 和算法事件表冲突，以算法表为准。情绪投喂会换掉上一次瞬时情绪；走失时仍然无效。
- 投喂 `calm` 维持写成 `close`。算法写的是 `calm/close` 候选，不在同一次事件里随机二选一。

## 批次 1：聊天预算认结算 key

结算写出 `satisfied`、`irritated`。`app/domain/chat/mode.py` 的句数表和提问表用的是 `content`、`irritable`，英文 key 对不上，满足和烦躁会落到普通陪伴的 3 句。

改 `_emotion()`：`satisfied` 按 `content` 取预算，`irritated` 按 `irritable` 取预算。中文「满足」「烦躁」已有映射，保留。

完成后：

- 满足最多 2 句、不问问题。
- 烦躁最多 1 句、不问问题，锋利高时轻刺上限仍是 1。
- 低落仍是不问，且轻刺上限是 0。

不改 TTL，不改标签优先级。测试放在现有聊天 mode 单测。

## 批次 2：低落原因跨请求还在

`resolve_label` 要 `mood <= 19` 且 `low_reason_active`。锁行时这个标记恒为假，所以下一次 bootstrap 不可能留下低落。原因码不能兼任这个标记：主标签变成「饿了」时原因码会换成 `PLAN_HUNGRY`，低落原因仍应留着，等饥饿过去后再显示。

加列，不复用 `emotion_reason_code`：

```text
spirit_emotion.low_reason_active  boolean  NOT NULL  DEFAULT false
```

用新的 Alembic revision。`lock_emotion_for_owner` 读入 `EmotionSnapshot.low_reason_active`。`apply_emotion_decision` 把 `settle` 结束时的值写回。没变化时不写，和现在一样。

`settle` 里把标记清成假的时机，只限这些已有正向事件成功进入映射之后：

- `feed.food.accepted`
- `feed.emotion.accepted` 且用户输入是 `happy`
- `promise.completed`
- `pact.completed`
- `recall.completed` 不清除。找回本身不是「低落原因解除」。

负向记忆触发把标记设成真，见批次 6。本批次先把读写接上，并加一条单测：标记为真、心情 10、饥饿和精力都是 80 时主标签是 `low`；下一次用写回的快照再 `settle`，4 小时内仍是 `low`。标记为假时不得是 `low`。

不在本批次用「长期饥饿」直接把标记设真。严重饥饿的主标签本来就是「饿了」，施工伪代码没有「饥饿本身打开 low_reason」这一句。

## 批次 3：对话只消费确定性信号

`dialogue.completed` 现在不带用户情绪，只负责清 `hurt`。算法要求：明确好消息、夸奖、安抚、口头焦虑，要产生候选；又要求不能伪造用户情绪，不能把模型猜测写入心情。

新增纯函数 `app/domain/emotion/signals.py`。输入是用户原文，输出至多一个信号。命中才填 `EmotionFact.user_emotion` 或安抚标记；都不命中就保持现在的行为。禁止调用模型，禁止让聊天模块自己算标签。

封闭词表，宁漏勿造：

| 信号 | 命中 | 候选 | 心情 | TTL | 原因码 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 好消息 | `面试过了`、`录取了`、`升职了`、`好消息` | `happy` | +6 | 1 小时 | `EVENT_DIALOGUE_HAPPY` |
| 夸奖 | `谢谢你`、`你真好`、`做得好` | `happy` | +4 | 1 小时 | `EVENT_DIALOGUE_PRAISE` |
| 安抚 | `别难过`、`我回来了`、`我在` | `close` | +2 | 2 小时 | `EVENT_DIALOGUE_COMFORT` |
| 焦虑 | `我很焦虑`、`好焦虑`、`我好慌` | `missing` | −2 | 2 小时 | `EVENT_DIALOGUE_ANXIOUS` |

一句里按上表从上到下，只取第一项。「别安慰我」不产生信号，也不产生 `hurt`。现有聊天 overlay 继续只把提问次数打成 0。

`publish_dialogue_completed` 把用户原文传进 `emotion_fact_from_event`。事实仍标记 `valid_interaction=True`。

另外改对话映射：有效对话除了清 `hurt`，也清未过期的 `missing`。算法对惦记的提前结束是「用户回来或有效互动」，不是等满 2 小时。不要复用 `ends_topic`，否则同一次普通对话会把开心、好奇、亲近、烦躁一起清掉。

安全回复、走失引导、出门短句不发布这套抽取。现有「这些回复不改变普通情绪抽取窗口」保持。

测试：

- 「我很焦虑」→ `missing`，不是 `uneasy`。
- 「面试过了」且饥饿 10 → 库内候选可以是 `happy`，主标签仍是 `hungry`。
- 普通「在吗」且当前是 `hurt` → 清掉委屈，不写新的瞬时情绪。
- 普通对话且当前是 `missing` → 清掉惦记。
- 普通对话且当前是 `happy` → 开心还在。

## 批次 4：开始求知产生好奇

算法事件表有「开始求知」：心情 0，候选 `curious`，求知结束或 45 分钟。`pact.answer.submitted` 现在只有去向消费者。

先确认代码里「进入 study / 开始一场共学」是哪一个稳定事件，只给那一个事件加 `emotion` 消费者。每次答题不要刷新 TTL。

映射：`curious`，心情 +0，TTL 45 分钟，原因码 `EVENT_PACT_STARTED`。`pact.completed` 仍是 `happy`。`happy` 的覆盖名次高于 `curious`，完成时会换掉好奇，不必再写一条清除。

同一 `event_id` 重放不得把 45 分钟重新起算。

## 批次 5：话题结束

`topic.ended` 的映射已经能清 `happy`、`irritated`、`missing`、`curious`、`close`。没有调用方。

只在用户原文命中封闭词表时发布，不从「下一句不像同一话题」推断：

```text
不想继续、别说了、换个话题、说到这
```

事件 id 用对话 `event_id` 派生的稳定 UUID（例如 UUID5），重放对话时话题结束也只生效一次。发布点放在 `publish_dialogue_completed` 里、情绪结算之前，作为第二条事实交给同一次 `settle`。不要在聊天生成过程中单独开事务写情绪。

`missing` 已由批次 3 的有效对话清除。话题结束词表主要提前结束开心、好奇、亲近、烦躁。

## 批次 6：已确认边界才产生烦躁

`boundary.touched` 映射已在：`irritated`，心情 −2，30 分钟。业务要求必须是已确认边界。

当前没有独立边界表，只有会话 overlay 的 `blocked_topics`，以及记忆封存话题。规则：

- 用户原文命中本轮已有的 `blocked_topics`，或命中已封存且未要求重新记住的话题时，事实类型用 `boundary.touched`。
- 没有这种记录就不发。不用「语气不好」「生气」猜测烦躁。
- 生气投喂仍走批次 0 的 `missing`，不改。

`irritated` 覆盖名次低于 `hurt` 和身体严重档，高于开心。严重饥饿时主标签仍是「饿了」。

## 批次 7：情绪记忆触发和封存

算法测试第 10 条和业务验收都要求：封存后相关话题不再产生 `missing` / `low`。领域函数有 `memory.emotion.triggered` 和 `seals_memory`，没有任何调用方会构造这些事实。

做法：

1. 记忆模块提供一个只读查询：给定用户原文，是否命中一条仍有效的情绪记忆，以及它是负向还是仅相关。情绪模块不扫描记忆表。
2. 对话发布前调用该查询。命中且记忆未封存：事实带 `emotion_memory_relevant=True`，事件类型走现有 `memory.emotion.triggered` 的映射（`missing`，心情 −2，2 小时，并在本次 `settle` 把 `low_reason_active` 设为真）。未命中不要设这个标记。
3. 心情仍大于 19 时，主标签是惦记，不是低落。心情 ≤19 且标记为真、又没有更高标签时，主标签才是低落。
4. `memory.sealed`、`memory.deleted`、`memory.cleared` 在 `memory_control` 的同一事务里调用 `resolve_now`，事实使用现有 `seals_memory` 映射。效果是本次以及之后都不再用这份记忆产生候选；若当前原因码是 `EVENT_MEMORY_TRIGGER`、`EVENT_DIALOGUE_MEMORY` 或 `PLAN_LOW`，清掉对应瞬时情绪，并把 `low_reason_active` 写回假。
5. 不在记忆服务里直接 `UPDATE spirit_emotion`。

封存查询失败就不要发触发事实。漏触发可以，误触发不行。

## 批次 8：测试与文档

每批合并前跑对应单测。碰库的批次用现有 db 测试库 `kelin_pytest`，不要对 `kelin_test` 跑会删精灵的用例。

一批落地后，把行为补进 `docs/emotion.md` 的对应小节。不要改 `1_plan/STATUS.md`，不要写验收通过。

建议回归：

- lost 时投喂知识，主标签仍是 `uneasy`。
- 刚找回且饥饿、精力都 ≤19，主标签是 `hurt`。
- 知识事件心情只 +4 一次；同一 `client_id` 重试不延长好奇。
- 低落原因写回后，8 小时内心情仍 ≤19 时，下一次无事件结算仍是 `low`；满 4 小时瞬时情绪过期后不再是 `low`，除非原因标记仍在且心情仍 ≤19。过期规则以 `emotion_expires_at` 为准；标记还在但瞬时情绪已过期时，按伪代码仍可因心情 ≤19 显示低落。这里以施工伪代码为准：标记为真且心情 ≤19 就显示，4 小时限制的是 `low` 这条瞬时情绪，不是把主标签硬截止。实现时不要自行加一条「满 4 小时强制改成平静」除非瞬时情绪过期且标记已被正向事件清掉。

## 先不排期

下面只有产品规格写了，算法事件表没有，或然数没有锁定。不要在上面各批里顺手做。

| 项 | 原因 |
| :--- | :--- |
| 较久未互动就惦记 | 没有小时数。去向已是满 18 小时出门、72 小时走失。要先定「仍未走丢时从第几小时起惦记」 |
| 约定即将到期就惦记 | 现在只影响字条 `promise_due`。算法事件表没有这条 |
| 进化产生开心 | 算法事件表没有 |
| 节日、天气、时辰单独出标签 | 业务一期明确排除 |
| 精力离线回升、求知或串门后回精力 | 速度在产品 §18 待确认。现有共学完成是精力 −3。这是去向模块的数，不是情绪标签 |
| 固执让委屈、烦躁的 TTL 加长 | 没有系数 |
| 夜行高只改「有点困」的说法、不改精力 | 去向已对夜行低于 40 做深夜扣精力。表现层不要在情绪里再减精力 |
| 投喂 `note` 写成情绪记忆 | 记忆模块的入口。情绪结算继续忽略 `note` 文本 |
| 把「平静」投喂改成有时平静、有时亲近 | 候选表是斜杠，没有分叉条件 |
