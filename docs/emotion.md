# 情绪模块（现行实现）

本文按 `4_server` 里已经落地的代码整理。库表以 Alembic `20260918_0100` 的 `spirit_emotion` 为准。产品规格在 `0_kelin/v1/情绪/`，规格里有、下面写明「当前没有入口」的规则，运行时不会发生。

情绪只写心情、瞬时情绪和主标签。饥饿、精力、去向由去向模块先算完，情绪只读。

## 1. 代码落点

| 层 | 路径 | 做什么 |
| :--- | :--- | :--- |
| 纯计算 | `app/domain/emotion/` | 心情漂移、事件映射、瞬时覆盖、主标签。无 IO |
| 落库 | `app/repositories/emotion.py` | 锁并更新 `spirit_emotion` |
| 服务 | `app/services/emotion/settle_service.py` | `resolve_now`：锁行、调用 `settle`、有变化才写 |
| 调用顺序 | `app/services/central/lifecycle.py` | `settle_then_resolve`：先去向，再情绪 |
| 首页中文 | `app/domain/room/display.py` | 英文 key → 首页文案。情绪模块自己不翻译 |
| 聊天预算 | `app/domain/chat/emotion_context.py` | 把英文标签写进系统提示，要求模型不要回传标签 |

规则版本字符串是 `kelin-v1-rules-1`（`app/domain/emotion/types.py` 的 `RULES_VERSION`）。

## 2. 表 `spirit_emotion`

一行一只精灵，主键 `spirit_id`。

| 列 | 含义 |
| :--- | :--- |
| `mood` | 心情，0–100。检查约束 `ck_spirit_emotion_mood` |
| `realtime_emotion` | 瞬时情绪英文 key，可空 |
| `emotion_reason_code` | 原因码，如 `PLAN_CALM`、`EVENT_FEED_FOOD` |
| `emotion_started_at` / `emotion_expires_at` | 瞬时情绪起止。无 TTL 的标签 `expires_at` 为空 |
| `emotion_source_event_id` | 写下当前瞬时情绪的事件。同一事件再进来会跳过，避免把 TTL 再延一次 |
| `primary_label` | 主标签英文 key，1–32 字。空则尚未结算 |
| `emotion_snapshot_version` | 本表版本，从 1 起，每次真正 UPDATE 加 1。与 `spirits.snapshot_version` 不是同一个数 |
| `updated_at` | 上次情绪结算时间。锁行时把它当作 `last_emotion_settled_at` |

没有单独的「低落原因」列，也没有「情绪记忆是否还有效」列。这两项只活在一次 `settle` 的内存里，请求结束就丢。

行不存在时，`resolve_now` 用精灵宽表的 `mood` 插入一行，`primary_label` 仍为空。V1 重建迁移给已有精灵回填过主标签「平静」（中文）。下次结算若算出的英文 key 与库存字符串不同，会改写成英文 key。

首页心情读 `COALESCE(spirit_emotion.mood, spirits.mood)`。情绪结算不回写 `spirits.mood`。成长旧路径 `apply_growth_to_spirit` 仍可能改宽表心情，有情绪行之后首页不看那一列。

## 3. 谁调用

统一入口是 `settle_then_resolve`：去向 `settle_now` 完成之后，把结算后的 `status`、`hunger`、`energy` 交给 `resolve_now`。情绪不改这三个数。

| 场景 | 行为 |
| :--- | :--- |
| `GET /api/v1/bootstrap` | 不带事件事实，只按当前时间漂移心情、清过期瞬时情绪、重算主标签。组装房间后 `save_projection` |
| 聊天生成前 | 同上，先结算再把英文标签放进提示 |
| 接受投喂 / 完成约定 / 完成共学 / 召回 | 用户动作接受且不是重放时，再带一条情绪事实结算一次 |
| `dialogue.completed` | 对话落库后带事实结算。事实固定为有效互动，不带用户情绪种类 |
| `visit.completed` | worker 带事实结算。情绪有变化时顺手重写房间投影 |

`app/services/emotion/__init__.py` 的 `resolve()` 只返回空的 `EmotionView`，不读库。真正的结果来自 `resolve_now`。

记忆封存、删除、清空在消费者名单里写了 `emotion`，`memory_control` 当前只改记忆和熟悉度，不调用 `resolve_now`。

## 4. 一次结算的顺序

`settle`（`app/domain/emotion/resolver.py`）对一个 `EmotionSnapshot` 和一组事实：

1. 心情向 50 漂移。基准是 `updated_at`。满 8 个整小时动 2 点：高于 50 则减，低于 50 则加，到 50 为止。不足 8 小时不动。7 小时为 0 步，8–15 小时为 1 步，16 小时为 2 步。心情夹在 0–100。
2. `emotion_expires_at <= now` 时清掉瞬时情绪、起止、来源事件和原因码。
3. 去向是 `lost`：瞬时情绪改为 `uneasy`，不设过期。去向不是 `lost` 但库存瞬时情绪是 `uneasy`：清掉。
4. 事实按 `(occurred_at, event_id)` 排序后逐条映射。拒绝或未知事件没有候选，跳过。
5. 同一 `event_id` 在本批出现第二次，或等于库存 `emotion_source_event_id`，只跳过「再写瞬时情绪」。心情增量在这次也会跳过。
6. 候选若声明结束话题，且当前瞬时情绪是 `happy` / `irritated` / `missing` / `curious` / `close`，清掉瞬时情绪，本条结束。
7. 候选若声明结束委屈，且当前是 `hurt`，先清掉 `hurt`，再继续看本条要不要写新情绪。
8. 候选是 `calm` 且心情增量是 0：不写瞬时情绪。对话完成走的就是这条。
9. 原因码是 `EVENT_MEMORY_TRIGGER` 或 `EVENT_DIALOGUE_MEMORY` 时，本次内存里的低落原因记为真。当前没有入口造出这两种事实。
10. `lost` 时只接受会变成 `hurt` 的候选，其余跳过。结算末尾仍把瞬时情绪和主标签强制为 `uneasy`。
11. 先把心情加上增量，再判断能否覆盖瞬时情绪。情绪投喂（`feed.emotion.accepted`）直接换掉上一次瞬时情绪。其它事件仍按名次：覆盖失败时心情已经加上，瞬时情绪保持原来的。走失时这两种都写不上，最后仍是 `uneasy`。
12. 覆盖成功：瞬时情绪换成候选，起点是本次 `now`（不是事件发生时间），过期时间是 `now + TTL`。无 TTL 则过期为空。来源事件和原因码一并写上。
13. 用第 5 节的顺序算出主标签。主标签是 `uneasy` 时，瞬时情绪也写成 `uneasy`，过期为空。
14. 原因码空着时，用主标签对应的 `PLAN_*`。

`changed` 为真的条件：心情、瞬时情绪、过期时间、来源事件，或主标签与「用结算前快照再算一次」的结果不同。`resolve_now` 另外比较库存 `primary_label` 和这次的主标签，字符串不同也写库。没变化则不 UPDATE，`emotion_snapshot_version` 不动。

## 5. 主标签顺序

数字小的先赢。性格五维不参与这个顺序。`resolve_label` 按下面的顺序短路，不是把所有候选放进排序器再取最小。

| 顺序 | 标签 | 何时出现 |
| :--- | :--- | :--- |
| 1 | `uneasy` | 去向 `lost` |
| 2 | `hurt` | 未过期的瞬时情绪是 `hurt` |
| 3 | `hungry_and_tired` | 饥饿 ≤19 且精力 ≤19 |
| 4 | `hungry` | 饥饿 ≤19 |
| 5 | `tired` | 精力 ≤19 |
| 6 | `irritated` | 未过期的瞬时情绪是 `irritated` |
| 7 | `low` | 心情 ≤19，且本次内存里低落原因为真 |
| 8–12 | `missing` / `happy` / `curious` / `close` / `satisfied` | 对应瞬时情绪未过期 |
| 13 | `slightly_hungry` | 饥饿 ≤39 |
| 13 | `slightly_tired` | 精力 ≤39，且饥饿 >39 |
| 14 | `calm` | 以上都不是 |

未过期的 `low` 或 `calm` 瞬时情绪本身不会当成主标签。低落还依赖本次内存里的原因标记；锁行时这个标记固定是假，所以一次新的 bootstrap 不会把主标签留在 `low`。

身体档只读去向交来的饥饿和精力，情绪不把「饿了」写进 `realtime_emotion`。

## 6. 瞬时情绪覆盖与 TTL

覆盖用 `REALTIME_RANK`，1 最高。除情绪投喂外，新情绪的名次小于或等于当前名次时才能换上。空的当前瞬时情绪可以被任何候选换上。五种用户情绪投喂忽略名次，直接换上候选，并重算起止时间。

| key | 名次 | TTL |
| :--- | :--- | :--- |
| `uneasy` | 1 | 无 |
| `hurt` | 2 | 2 小时 |
| `irritated` | 6 | 30 分钟 |
| `low` | 7 | 4 小时 |
| `missing` | 8 | 2 小时 |
| `happy` | 9 | 1 小时 |
| `curious` | 10 | 45 分钟 |
| `close` | 11 | 2 小时 |
| `satisfied` | 12 | 2 小时 |
| `calm` | 14 | 无 |

例如未过期的 `irritated` 挡得住 `happy`，挡不住 `hurt`。

## 7. 事件实际写进情绪的结果

`emotion_fact_from_event` 只接受下列已接受事件。用户投喂情绪的 `payload.emotion` 必须是 `calm` / `happy` / `tired` / `anxious` / `angry`，非法则整条事实丢弃。

| 事件 | 瞬时情绪 | 心情 | TTL | 原因码 |
| :--- | :--- | :--- | :--- | :--- |
| `feed.food.accepted` | `satisfied` | 0 | 2 小时 | `EVENT_FEED_FOOD` |
| `feed.knowledge.accepted` | `curious` | +4 | 45 分钟 | `EVENT_FEED_KNOWLEDGE` |
| `feed.emotion.accepted` + `happy` | `happy` | +6 | 1 小时 | `EVENT_USER_HAPPY` |
| `feed.emotion.accepted` + `calm` | `close` | +2 | 2 小时 | `EVENT_USER_CALM` |
| `feed.emotion.accepted` + `tired` | `missing` | 0 | 2 小时 | `EVENT_USER_TIRED` |
| `feed.emotion.accepted` + `anxious` 或 `angry` | `missing` | −2 | 2 小时 | `EVENT_USER_ANXIOUS` / `EVENT_USER_ANGRY` |
| `promise.completed` / `pact.completed` | `happy` | +4 | 1 小时 | `EVENT_PROMISE` / `EVENT_PACT` |
| `visit.completed` | `satisfied` | 0 | 2 小时 | `EVENT_VISIT` |
| `recall.completed` | `hurt` | +8 | 2 小时 | `EVENT_RECALL` |
| `dialogue.completed` | 不写新的瞬时情绪 | 0 | — | 若当前是 `hurt`，先清掉 |

`map_event` 里还有 `boundary.touched`、`topic.ended`、`memory.sealed` / `deleted` / `cleared`、`memory.emotion.triggered`，以及对话上的焦虑、开心、记忆分支。当前没有任何调用方构造这些 `EmotionFact`，所以上表之外的映射不会在接口里发生。

用户动作重放不会再次进入 `_notify_emotion`。`visit.completed` 走同一条用户动作幂等。

## 8. 对外看到什么

库内和聊天提示用英文 key。首页 `data.room.primary_emotion_label` 在 `project` 里经 `display_label` 变成中文。未知 key 原样透传。空标签用「平静」。

| 库内 / 聊天 `primary_label` | 首页文案 |
| :--- | :--- |
| `uneasy` | 不安 |
| `hurt` | 委屈 |
| `hungry_and_tired` | 又饿又困 |
| `hungry` | 饿了 |
| `tired` | 困了 |
| `irritated` | 烦躁 |
| `low` | 低落 |
| `missing` | 惦记 |
| `happy` | 开心 |
| `curious` | 好奇 |
| `close` | 亲近 |
| `satisfied` | 满足 |
| `slightly_hungry` | 有点饿 |
| `slightly_tired` | 有点困 |
| `calm` | 平静 |

聊天 `render_emotion_context` 使用结算视图上的英文 `primary_label` 和 `realtime_emotion`，并附上句数、问句数、语气和原因可见性。`hurt` / `irritated` 的可见性是 `once`，`low` 是 `hidden`，其余是 `open`。`EmotionView.mood_band` 和 `reply_budget` 当前恒为 `None`。

房间 `text_status` 由去向和饥饿/精力档位另算（在家且 ≤19 时为 `hungry` / `tired` / `hungry_and_tired`，否则是去向英文 key）。它不是主情绪文案。

`data.spirit.mood` 是情绪行的心情。`data.spirit` 上没有主情绪字段；首页主情绪只看 `data.room.primary_emotion_label`。

## 9. 读代码时的边界

- 情绪不写饥饿、精力、去向、羁绊、阶段、记忆。
- 性格分在快照类型上有默认值 50，结算不读取，也不改变第 5 节的顺序。
- 低落原因和情绪记忆开关没有列。一次 bootstrap 不会维持「低落」。
- 手改 `primary_label` 而不同时改瞬时情绪、饥饿、精力、去向时，下一次 bootstrap 会按第 5 节重写主标签。
- 联调工具若要让保存结果在进房后保持不变，必须按同一套规则写瞬时情绪起止和去向结算时间。工具行为见 `5_testtool/spirit_console/`，不在本模块内。
