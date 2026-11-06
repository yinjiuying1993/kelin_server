# V3-S0-T01 复用点

对照 `0_kelin/v3/plan/kelin_v3_server_technical_spec.md` 与当前 `4_server` 工作区。只盘点，不改房间、记忆、成长、串门结算。

## 评分结算能否挂现有 worker

能。进程入口是 `app/worker.py` → `app.services.cron.run_worker_tick`。抽取已经用同一套 outbox：`app/services/memory/extract_worker.py` 调 `app/repositories/outbox.py` 的 `claim_due_outbox(..., event_types=...)`。

传艺评分入账应新增一个 `process_due_*`，在 `run_worker_tick` 里多调一次，用新的 `event_type` 领取。结算只写账本和技能积分，不调模型。

不要挂到 `app/domain/central/consumers.py` 的 `EVENT_CONSUMERS`。那是进程内记忆/意识白名单，不是数据库 outbox，也不做跨模块加分。

## 复用

| 能力 | 真实路径 | V3 用法 |
| :--- | :--- | :--- |
| Worker 循环 | `app/worker.py`，`python -m app.worker` | 同一进程加结算 tick |
| Outbox 领取 | `repositories/outbox.py`：`claim_due_outbox` / `reclaim_expired_outbox`；SQL `private.claim_due_outbox` | 评分事务里插入 `outbox_events`，worker 按新 event_type 领 |
| 幂等行 | `public.idempotency_records`，键 `(user_id, operation, client_id)` | 新 operation 名，不复用 `central.event` |
| 幂等判定 | `services/central/idempotency.py` `decide_claim`；`domain/central` `classify_idempotency` | 创建技能、提问、轮次、评分可复用这套 replay / conflict / in_progress |
| 幂等写入范例 | `repositories/central/idempotency.py` `claim_event`；`repositories/spirit.py` `claim_create_idempotency` | 照抄 INSERT … ON CONFLICT，operation 换成传艺/问艺 |
| 一只用户一只精灵 | `repositories/spirit.py` `fetch_spirit_for_owner` | API 身份下取主人的精灵 |
| Worker 反查主人 | `private.spirit_owner_id(uuid)`，仅 `kelin_worker` 可执行（migration `20260920_0101`） | 结算 tick 从精灵主键找回主人 |
| 百炼唯一入口 | `providers/factory.py` `build_provider` → `integrations/bailian.py` `BailianHttpAdapter` | 匹配、作答、自测仍走这个 Adapter |
| 违法拒绝 | `BailianProvider.moderate` | 创建、提问、一轮、自测可先走安全检查 |
| RLS 矩阵 | `app/db/rls_matrix.py` | 新表登记 owner 策略，不另写一套角色 |
| 事务身份 | `claimed_transaction` + `kelin_api` / `kelin_worker` | API 写用 api 角色，结算用 worker 角色 |

## 新建

| 缺口 | 原因 |
| :--- | :--- |
| 传艺 / 匹配索引 / 问艺 / 账本 / 结算表 | 现库没有 skill、快照、问艺对话、传艺积分 |
| 三个独立模型调用 | `BailianProvider` 只有 `chat` / `extract` / `moderate` 等。`chat` 绑房间聊天 prompt，不能当问艺或匹配 |
| 匹配用的 skill → 精灵查找 | `spirit_owner_id` 只给 worker，且入参是精灵主键，不是 skill ID |
| 评分 outbox 的 event_type 与 consumer | 现有 tick 不认识传艺结算；`EVENT_CONSUMERS` 不能拿来加分 |
| 自测凭证与过期清理 | 无对应表或 cron |
| 运营兑付权限 | 普通用户接口没有单独运营角色写入 |

## 明确不复用

- 房间聊天落库、`dialogue.completed`、记忆抽取窗。
- 成长账本、投喂配额、羁绊。
- 串门 `visit.plan` / `visit.settle` outbox。
- 通知 planner（worker 当前 `notification_plan=0`，V3 也不发推送）。
