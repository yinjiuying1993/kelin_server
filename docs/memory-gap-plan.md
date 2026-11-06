# 记忆模块完善方案

对照三份材料：

- 产品规则：`0_kelin/v1/记忆/kelin_v1_memory_rules.md`
- 业务施工：`0_kelin/v1/记忆/记忆业务模块施工说明书.md`
- 算法施工：`0_kelin/v1/记忆/记忆算法模块施工说明书.md`

只排「施工说明书已经写了、现行代码没接上」的改动。一次只做下面的一个批次。每批先补纯函数测试；碰库的批次再补一条 db 测试。不顺手改聊天新闻、取名、配额或 iOS。

记忆仍只写 `public.memories`。成长只读 active 证据，不扫正文。聊天不得直接 ingest；普通聊天仍由中央在 `dialogue.completed` 后封口。不新增见闻、口头禅、天气或通知记忆。

## 已经接上、不要重写

- 公开类型只有 `preference`、`knowledge`、`emotion`、`relationship`。`speech`、`sight`、`style` 不能再作为记忆类型写入。
- `20260918_0100` 已有 `topic_key`、`source_event_id`、`rules_version`、`confirm_count`、`last_confirmed_at`、`decision_audit`，以及来源事件唯一索引、active 主题索引。本方案不另起一套表。
- 普通聊天满 3 个完整轮次后，窗口 `open → ready`，同一窗口最多一个 `extract.requested` 任务。失败回复不计入窗口。
- 抽取输出里的 `style_samples`、`personality_delta` 会被丢掉，不写成长。
- 纠正、封存、删除、清空已有 owner 锁、`client_id` 幂等、`CLEAR_ALL_MEMORIES` 和快照版本。删除 tombstone 不含摘要。
- `app/domain/memory/admit.py`、`salience.py`、`recall.py` 里的闸门、显著度和召回公式可以继续用。缺的是调用方和落库，不是再写一套公式。

## 批次 1：候选只描述事实，不提交最终显著度

模型现在提交 `type/summary/tags/salience/confidence`。施工要求候选必须带 `memory_type`、`summary`、`normalized_topic_key`、`source_message_ids`、`source_quote`、`confidence`、`signal`。最终 `salience`、状态、性格增量和阶段不得由模型提交。

改：

- `app/providers/types.py` 的 `ExtractMemoryDraft`：去掉作为最终值的 `salience`；补上主题键、证据消息、原文引用和信号。消息 ID 仍由服务端在窗口转录里核对，模型只报它引用的用户句。
- `app/integrations/bailian/prompts/extract/v2.txt`：按上面的字段要 JSON。置信度低于 `0.75` 的候选仍省略。
- `app/domain/extract.py` 的 `admit_extract_drafts`：把模型字段原样送进 `MemoryCandidate`，不再用整窗关键词替模型猜 `explicit/teachable/commitment`。信号是否合法留给 `admit()`。

完成后：缺任一必填字段、证据不是本窗用户消息、类型不在四类、信号不在五种合法值，候选被拒绝，不入库。`salience` 只来自 `compute_salience()`。

不改窗口封口，不改投喂。测试放在现有抽取准入单测。

## 批次 2：抽取结算走决策，不再直接插入

`app/services/extract.py` 和 `app/services/memory/extract_worker.py` 现在都调用 `extract_repo.insert_memory()`。决策里的 `merge`、`supersede` 没有写回数据库，`topic_key` 等列保持空值或默认值。

新增 `app/services/memory/ingest_service.py`。输入是已经算出的 `MemoryDecision` 和 owner 事务。它只做：

- `accept`：插入一条 active，写入主题键、来源事件、规则版本、确认次数 1、确认时间和 decision audit。
- `merge`：不插新行；目标行 `confirm_count += 1`，`last_confirmed_at` 更新，`salience` 取较大值。
- `supersede`：旧行改为 `superseded`，新行 active，并记下被替代的 id。
- `reject`：只记审计，不进记忆页。
- 同一 `spirit_id + source_event_id` 再次到达：返回原记忆，不增加确认次数，不改状态。

两条抽取入口都改为调用它。`insert_memory()` 不再被抽取路径直接使用。

同步 `POST /extract` 仍先保持现有接口。本批次不把模型调用移出事务，那是批次 4。

完成后：同一知识事件重试只有一条记忆；同主题同摘要只合并；明确新称呼替代旧称呼后，旧行不再被 active 查询读到。

## 批次 3：主题、别记遮罩和长期信号

`admit_extract_drafts()` 现在用摘要哈希当主题键，一次「别记」会挡住整窗，也没有跨窗口的重复和持续情绪。

改 `app/domain/memory/topic.py`：保留四段式 `<type>:<subject>:<attribute>:<scope>`。不能识别主体或属性时拒绝，不发明哈希键。称呼和禁止称呼必须是不同键，例如 `preference:user:address_name:preferred` 与 `preference:user:address_name:forbidden`。

别记按主题写入窗口遮罩，不拒绝无关候选。遮罩随窗口持久化，后续同主题默认拒绝；用户明确「重新记住」才允许新建。

信号按施工条件复核，不相信模型单独报上来的标签：

- `repeated`：同一主题最近 7 天至少 3 条不同用户消息。
- `stable_emotion`：同一情绪主题跨 2 个已抽取窗口，且至少一次是用户明确表达。
- `teachable`：定义、步骤或因果，并且至少 40 个汉字或 20 个词。
- 不确定词、强调词和可解析日期只影响显著度加减，不单独构成信号。

完成后：只说一次「我很焦虑」不写长期情绪；跨两个窗口且主题明确才可以。普通闲聊里的偏好、知识和持续情绪仍可由系统自动识别，不要求用户先说「记住」。

## 批次 4：抽取锁不再盖住模型调用

`services/extract.py` 在已持有精灵锁和窗口锁时调用模型。施工要求 claim 提交后释放锁，模型在事务外调用，结算前重新锁定并核对 claim 与快照版本。

异步 worker 已接近这个形状，补上结算前的快照检查：捕获版本落后于当前 `snapshot_version` 时丢弃写入，不得复活已封存或已删除的记忆。现有 `late_extract.py` 继续负责「控制操作胜出」。

同步 `POST /extract` 改成同一顺序：短事务 claim，事务外取模型结果，新事务结算。模型失败把窗口放回 `failed`，不写 active，不改成长。同一 `client_id` 同 body 回放第一次结果；不同 body 返回 `IDEMPOTENCY_CONFLICT`。

本轮新写入的聊天记忆仍不可用于当前轮回复。

## 批次 5：成长事件走同一 ingest

中央已经把这些事件标成 memory 消费者，但没有写入记忆：

- `feed.knowledge.accepted`
- `feed.emotion.accepted`
- `promise.created`
- `promise.completed`
- `pact.completed`
- `recall.completed`

在对应用户动作成功之后调用批次 2 的 ingest，不在 `feed.py`、`pact.py`、`recall.py` 里再写一条插入。

规则：

- 知识投喂成功写一条知识记忆。摘要只留主题和主旨。同主题合并。失败、空文本和重试不新增。
- 情绪投喂只有枚举合法且用户写了可归一原因才写。只有 `happy/anxious/tired/angry/calm` 时不写长期记忆。
- 创建约定写一条 active 关系记忆。完成只更新原来那条，不另开「已完成」记忆。取消改为 `cancelled`，不增加显著度。
- 共学完成最多一条主记忆。
- 找回成功写「你离开过」。同一找回事件重试不新增；另一次找回事件才可以再写一条。主动召回只取最近一条。
- 串门和明信片不写私人记忆。

完成后：现有「知识投喂后 `source_feed_id` 记忆数为 0」的测试要改成「同一次投喂恰好一条，重试仍是一条」。

## 批次 6：聊天召回改用评分

`app/domain/memory/recall.py` 的 `build_recall_context()` 没有被聊天调用。现在 `fetch_prompt_memories()` 按 `created_at DESC` 取最近 20 条，`select_chat_memories()` 再按文字包含关系挑选。

召回查询同时约束 `spirit_id`、owner 和 `status = 'active'`。`sealed`、`deleted`、`superseded`、`stale` 不进入主动召回。

普通项使用：

```text
score = round(0.55 × topic + 0.30 × salience + 0.15 × recency)
```

排序为 score、salience、`last_confirmed_at`、`memory_id`。普通闲聊低于 55 分不召回；知识问题还要求 topic 不低于 60。每轮最多 6 条，同主题最多 1 条。幼灵、成形、觉醒上限仍是 3、5、6。

强制槽只留给当前称呼、禁止称呼和安全边界、当前问题直接命中的知识、正在处理的约定、走失找回所需关系。这些仍受总条数限制，不参加普通淘汰。

`app/repositories/memory_recall.py` 改为取出评分所需列，由 `build_recall_context()` 决定结果。聊天继续只收到摘要，不收到 sealed 或 deleted 正文。

不在本批次做「一轮回复最多显式提到 1 条普通记忆」的模型验收。那是回复约束，等召回名单先正确。

## 批次 7：控制动作补全主题和历史

纠正现在只改 `summary`。施工还要求重新检查敏感信息、重新生成主题键，并在主题变化时重查冲突。

改 `correct_active_memory`：

- 摘要仍须 1 到 500 字，且过隐私检查。
- 重新计算主题键。若与另一条 active 冲突，按批次 2 的合并或替代处理，不留下两条同主题同语义。
- 称呼类纠正沿用「最新明确指令胜出」，不与旧称呼投票共存。
- 不回滚五维、羁绊和阶段。

封存和删除继续立即移出召回与成长证据。重复删除返回原来的 `deleted_at`。清空仍只接受 `CLEAR_ALL_MEMORIES`，并保留阶段和五维。

本批次不新增「封存恢复」接口。规则允许用户明确操作后恢复，但现有 API 只有 correct 和 seal；恢复留到记忆页筛选一起做。

## 批次 8：过期记忆和记忆页筛选

没有任务把「90 天未确认且显著度低」的 active 标成 `stale`。记忆页也只返回 active 正文和 deleted tombstone，不返回 sealed、stale，也不返回来源类别和最近确认时间。

先加一个只读结算函数和 worker 入口：active、`last_confirmed_at` 早于 90 天、显著度低于该类型门槛的行改为 `stale`。不物理删除。active 达到 200 时也不自动删旧行。

列表先保持现有 active 分页，避免改 iOS 契约。sealed 和 stale 的筛选、来源类别、恢复接口作为下一批，本批只保证 stale 不再进入批次 6 的主动召回。用户明确查询历史时的「仅本次被动返回」也放到那一批，本批不把 stale 塞进普通聊天。

## 验收顺序

按批次合并，不跳批。每批至少跑对应单测；批次 2、4、5、7 再跑一条 db 测试。命令沿用：

```text
docker exec -e APP_ENV=test -e PYTHONDONTWRITEBYTECODE=1 kelin-server-api uv run --frozen pytest <测试路径> -q --tb=short
```

碰库测试不要并发。全部批次完成前，不把现行实现称为已符合三份施工说明书。
