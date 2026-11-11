# V4 精灵聊天服务端开发说明

iOS 对接看 [`v4-ios-integration.md`](v4-ios-integration.md)。本文只说明服务端当时打算落在哪。

依据 `0_kelin/v4/kelin_v4_spirit_chat_server.md`（`v4-chat-server-0.5`）。本文告诉实现落在哪、复用什么、先做哪一步。

本文不是实现状态，也不是已评审的 OpenAPI。表以之后的 Alembic migration 为准。字段以评审后的 OpenAPI SHA 为准。当前库头是 `20261109_0130`，新修订接在它后面，不改已经落地的修订。

房间对话、问艺、传艺、记忆、积分和帮助次数不改。不调用百炼生成任何一方的回复。

---

## 1. 现状

| 能力 | 现在 |
| :--- | :--- |
| 房间对话 | `POST /api/v1/chat`，`app/api/v1/chat.py` → `app/services/chat.py`。精灵聊天不走这条 |
| WebSocket | 工程里没有。要新加一条用户级连接 |
| 公开详情 | `GET /api/v1/spirits/{spirit_id}/public`。字段只有 `id`、`name`、`help_count`、`skills`。没有昵称，没有头像地址 |
| 文字安全 | `moderate_text`。图片安全是另一条 `moderate()`，没配安全模型时不能当成通过 |
| 幂等 | 全局表 `idempotency_records`，键含 operation。问艺用自己的 `client_id`。精灵聊天另建表，不共用那张，也不复用问艺的 `client_id` |
| Outbox | `public.outbox_events` 由 `app/services/cron.py` 的 worker tick 领走，里面有问艺评分结算。聊天投递不插入这张表，也不由结算函数消费 |
| 错误码 | 已有 `NOT_FOUND`、`INVALID_INPUT`、`MODERATION_REJECTED`、`IDEMPOTENCY_CONFLICT`、`IDEMPOTENCY_IN_PROGRESS`、`DEPENDENCY_UNAVAILABLE`。没有 `SPIRIT_UNAVAILABLE`，要加进 `app/core/errors.py` |
| 一只用户一只精灵 | `repositories/spirit.py` 按主人取精灵。会话仍按两只精灵区分，不按两个用户合并 |

---

## 2. 代码放哪

留在现有进程里，不新开部署。网关、命令、查询、投递不共享仓储对象。

| 部件 | 建议位置 | 只做 |
| :--- | :--- | :--- |
| 只读 HTTP | `app/api/v1/spirit_chats.py` | 列表、按精灵打开、按序号补洞。Envelope。不清未读 |
| WebSocket | `app/api/v1/spirit_chat_stream.py` | 握手、心跳、拆包、按用户限流、把命令交给命令服务、把应答写回这条连接。不开数据库事务 |
| 命令 | `app/services/spirit_chat/command.py` | 发送、清未读、删除。短事务。不持有连接，不调百炼生成 |
| 查询 | `app/services/spirit_chat/query.py` | 列表、补洞、投递前的可见性。不写文字 |
| 仓储 | `app/repositories/spirit_chat.py` | 只碰本模块的表。查询带当前用户 |
| 连接登记 | `app/services/spirit_chat/connections.py` | `user_id` 到当前连接。不读库 |
| 投递 | `app/services/spirit_chat/deliver.py` | 提交后消费本模块 outbox。先在 API 进程内做。不改文字 |
| 精灵目录 | `app/services/spirit_chat/directory.py` 调用精灵模块的新端口 | 只返回名片五项 |
| 精灵资料 | 精灵模块自己的服务和表 | 昵称、自定义头像、默认头像。不写会话 |

跨模块只传名片和安全判定。不把精灵 ORM、问艺 ORM 传入命令服务。投递要确认「对方现在还能不能看」时调用查询服务，不自己写 SQL。

---

## 3. 建议顺序

1. 精灵资料：默认头像在创建时从固定集合分配并记在这只精灵上。昵称可空，去掉首尾空白后 1–12 字；空则清空，超过 12 字拒绝且不改上一次。头像只存对象存储地址。保存前走内容安全。拒绝或超时不替换已生效的昵称和头像。入口是主人自己的精灵页，不是设置接口。
2. 精灵目录端口：`spirit_id`、主人用户主键、显示名、头像地址、是否可用。主人主键只留在服务端。未设置昵称用原名。未上传头像用默认头像地址。查找只有未找到、可用、不可用。聊天模块不得再查精灵表。
3. 公开详情增加显示名和头像地址。这会改 OpenAPI。邀请码、阶段、相伴天数只出现在主人自己的精灵页，不进公开详情，也不进聊天事件。
4. 聊天表、RLS、本模块幂等和 outbox。修订接在 `20261109_0130` 后。
5. 命令服务的发送、清未读、删除。模型调用和目录调用都在事务外。
6. 三条只读 HTTP。
7. WebSocket 和进程内投递。
8. 精灵改昵称、换头像、改原名或停用时，精灵模块在自己的事务提交后发事件。载荷只有 `spirit_id`、显示名、头像地址、是否可用。聊天订阅者只更新参与行副本，再写 outbox。

---

## 4. 复用

| 需要 | 用现成的 |
| :--- | :--- |
| 登录用户 | `require_current_user`，RS256。握手用同一套校验。数据包里不再读用户身份 |
| 事务身份 | `claimed_transaction` / `claimed_read_transaction`，角色 `kelin_api` |
| 文字是否违法 | `moderate_text`。只传去掉首尾空白后的正文和 `request_id`，不传会话和名片 |
| 头像是否违法 | 现有图片 `moderate()`。没配安全模型就拒绝这次上传，不能当成通过 |
| 字数 | 与问艺一轮相同：去掉首尾空白后 1–500。统计可以共用函数，表和事务不共用 |
| 读接口外壳 | 既有 Envelope：`ok` / `data` / `error.code` / `request_id` / `server_time` |
| 游标 | 现有不透明 cursor。客户端原样回传。不能靠改写它看到小于或等于 `clear_seq` 的文字 |
| 对象存储 | 现有 OSS 配置。图片正文不进聊天表，也不进聊天 outbox |
| RLS | `app/db/rls_matrix.py` 登记新表。仓储查询仍然显式带当前用户 |

---

## 5. 数据

下面是建议表名。落地前可以改名，但只能属于精灵聊天模块。删除会话不物理删除文字。

| 表 | 存什么 |
| :--- | :--- |
| `spirit_chat_threads` | 一场。两只精灵主键按固定顺序排列，这一对唯一。两只必须属于两个不同用户。创建后不能改成第三只 |
| `spirit_chat_participants` | 每场两行。`clear_seq` 默认 0，`last_read_seq` 只增不减，`last_visible_seq`，对方显示名，对方头像地址，`peer_available` |
| `spirit_chat_messages` | 序号从 1 连续递增，只在发送事务里分配。正文是原文，不摘要。文字行不存头像 |
| `spirit_chat_idempotency` | 用户加 `client_id` 唯一。请求摘要、状态、第一次成功后的 `thread_id`、文字 id、序号 |
| `spirit_chat_outbox` | 与业务行同一事务插入。收件用户、`type`、序号、定稿事件包、是否已处理 |

未读不另存一列。它等于对方发来的、序号大于 `last_read_seq` 且大于 `clear_seq` 的条数。自己发的不计。

列表不出现这场，当没有可见文字，或 `last_visible_seq` 不大于 `clear_seq`。

精灵模块另记：昵称、自定义头像地址、创建时分配的默认头像。聊天模块不选择默认图，也不改这张默认图。

---

## 6. 通道

`GET /api/v1/spirit-chats/stream` 升级为 WebSocket。一个用户同时只留一条连接，新连接替换旧的。这条连接覆盖该用户的全部会话。

token 过期，或连续两个 30 秒周期没有应用层 ping，服务端关闭连接。关闭只表示鉴权失败、token 过期、心跳中断。业务拒绝写在 `message.ack` 或 `error` 里，不用关闭码。

命令频率超限直接回 `error`，不调用命令服务，不落库。阈值实现时再定。

事件包字段：`type`、`event_id`、`thread_id`、`data`。`data` 不含用户主键。推送的 `event_id` 是服务端 UUID。命令应答的 `event_id` 沿用引起它的 `client_id`。第一条文字还没落成会话时 `thread_id` 为空。

| 方向 | `type` | `data` |
| :--- | :--- | :--- |
| 上行 | `message.send` | `client_id`、`peer_spirit_id`、`text` |
| 上行 | `thread.read` | `thread_id` |
| 上行 | `thread.clear` | `client_id`、`thread_id` |
| 下行 | `message.ack` | 只给发送者。成功带文字、序号、对方精灵名片。拒绝带 `error.code` |
| 下行 | `message.created` | 给还能看见这条文字的另一名参与者。文字 id、序号、正文、服务端时间，`mine` 为假 |
| 下行 | `thread.updated` | 对方精灵、显示名、头像地址、是否可用、最后一条可见文字、时间、未读数。从自己列表消失时带隐藏标记 |
| 下行 | `error` | 没有对应 ack 的失败：不是参与者、频率超限、未知 `type` |

未知命令回 `error`，码为非法输入，不落库。客户端收到未知事件则忽略，不断开。

WebSocket 文本帧不套 Envelope。HTTP 读接口仍套 Envelope。

后续输入状态、已读回执、富媒体只加 `type`，不加第二条连接。

### 6.1 发送顺序

1. 网关用连接上的 `user_id` 调用命令服务，不等待投递完成。
2. 先占住幂等记录。已完成则直接把第一次的 ack 交回网关，不再调用目录、安全和仓储。
3. 未完成时调用目录。未找到：对客户端是未找到，不建会话。不可用且已有会话：`SPIRIT_UNAVAILABLE`，不写新文字。没有会话时，未找到和不可用对客户端都是未找到。
4. 目录返回可用后，事务外做内容安全。拒绝是 `MODERATION_REJECTED`。超时或不可用是可重试失败，同一 `client_id` 可以再试。超时不等于通过。这两种都不进写事务。
5. 短事务：按精灵对取得或插入会话，插入下一条文字，更新两侧 `last_visible_seq` 和名片副本，推进发送者的 `last_read_seq`，写双方 outbox，幂等标成完成。接收者的 `last_read_seq` 不动。
6. 提交后网关把 `message.ack` 写给发送者。投递另行消费 outbox。
7. 接收者在线且序号仍可见：投 `message.created` 和 `thread.updated`。发送者在线：再投一条 `thread.updated`。

两笔首条同时到达时，唯一约束只留一场。输掉插入的那次锁住已有会话再写文字，仍返回成功。两条都按序号留下。

应答如果没赶上旧连接，客户端重连后用 `client_id` 或序号认出那条文字，不把它显示成第二条。

### 6.2 清未读和删除

`thread.read` 把 `last_read_seq` 提到当前能看见的最大序号。不改 `clear_seq`，不删文字。同一事务只给自己写 `thread.updated`。没有 `client_id`，重复发送结果相同。拉取文字的 HTTP 不顺便清未读。

`thread.clear` 把 `clear_seq` 提到当时的最大序号。文字行保留。不给对方写删除事件。同一 `client_id` 重放返回第一次的水位，不再往后推。删除提交前对方已经写入的更大序号仍然可见。成功后只给操作者推列表消失。

不是参与者：未找到。

### 6.3 投递

outbox 里已经写明收件用户、`type` 和定稿事件包。投递只做三件事：取出未处理的包、调用查询服务确认序号仍大于收件人的 `clear_seq`、交给连接登记。不可见或用户不在线，都标成已处理，不留补推队列。投递失败不回滚文字。

多实例时，进程间只传已经定稿的事件包。当前单进程先在 API 进程内投递，事件包形状不变。

---

## 7. 只读 HTTP

| 入口 | 行为 |
| :--- | :--- |
| `GET /api/v1/spirit-chats?cursor=` | 只返回 `last_visible_seq > clear_seq` 的参与行。按自己能看见的最后一条文字的时间从新到旧。每条含 `thread_id`、对方 `spirit_id`、副本里的显示名和头像、`peer_available`、最后一条可见文字、时间和未读数。根上给 `unread_total`。空列表是成功。不查目录 |
| `GET /api/v1/spirit-chats/by-spirit/{spirit_id}` | 没有会话时先问目录。未找到或不可用且本地没有会话：未找到，不插入。可用且没有会话：成功，`thread` 为空，文字为空，`peer_available=true`。已有会话不再为了名称问目录，按水位返回文字。同时给出自己精灵当前的显示名和头像；目录不可用时用自己参与行上的副本 |
| `GET /api/v1/spirit-chats/{thread_id}/messages?cursor=` | 参与者才可读。只返回序号大于自己 `clear_seq` 的文字。每条含文字 id、序号、`mine`、正文、服务端时间。不是参与者：未找到 |

分页从新到旧，客户端再按序号从小到大显示。重连、冷启动和下拉刷新走这三条，和推送读同一份水位。

---

## 8. 名片副本

列表和补洞不调用目录。显示名、头像地址和是否可用以参与行为准，所以精灵模块短暂不可用时，已有聊天仍能打开。

事件晚到时，下一次发送用当场的目录结果覆盖副本。两次发送之间列表可以暂时显示旧名字或旧头像。气泡不单独保存头像；客户端用这场会话当前的双方头像来画。副本更新后，旧气泡也换成新头像。

对方在线时收到 `thread.updated`，头像是他看见的对方头像。修改者自己的列表行仍是对方精灵。他正开着窗口时，同一条连接只告知自己的头像已更新，不把列表换成自己。

发送事务和资料事件并发时：发送使用它开始时拿到的目录结果；订阅者只更新副本。发送若后提交，用当场的显示名、头像和可用性再覆盖一次。两边都不写对方的表。

---

## 9. 失败

| 情况 | 结果 |
| :--- | :--- |
| 自己没有精灵，或给自己的精灵发送，或精灵不能看 | 未找到。不建会话 |
| 空字、纯空白、超过 500 字 | 拒绝。不落库 |
| 明显违法伤害 | `MODERATION_REJECTED`。不建会话，不写文字，不加未读 |
| 内容安全或目录超时 | 可重试。不建会话。已有会话的列表和补洞仍读本地副本 |
| 同一 `client_id` 换内容 | `IDEMPOTENCY_CONFLICT` |
| 同一 `client_id` 仍在处理 | `IDEMPOTENCY_IN_PROGRESS` |
| 会话已在，对方精灵已不在 | 历史按水位可读，`peer_available=false`。新发送 `SPIRIT_UNAVAILABLE` |
| 聊天库不可用 | 命令和读都失败。不伪造已发送 |
| 投递停着 | 已提交的文字和未读都在。对方用 HTTP 补洞 |
| token 过期或心跳中断 | 关闭连接。已提交的文字保留 |
| 推送时对方不在线 | outbox 标成已处理。未读留到下次列表或补洞 |

问艺、传艺、房间或结算故障与本模块无共享事务。那些故障不回滚这里的文字，这里的故障也不回滚它们。

日志不记聊天正文、token、昵称、头像地址和对方用户主键。可以记 `thread_id`、`spirit_id`、`type`、`error.code`、`request_id`。

---

## 10. 测试

先写不连库的单测，再写一条走一次性库的 HTTP 流程。不要在联调库上跑会 `DROP SCHEMA` 的 pytest。

至少锁住这些：

- 给自己的精灵打开或发送是未找到。没有精灵同样是未找到。
- 1–500 字落库。空字和超长不落库。
- 第一条发送成功后双方列表都出现，各自看到的是对方精灵。再次按这只精灵打开，`thread_id` 不变。
- 响应和事件里没有用户主键、技能正文、私人记忆。列表 SQL 不出现房间消息、问艺、技能、积分、帮助次数的表。
- 对方在线时收到 `message.created` 和未读变化。`thread.read` 使未读归零，文字还在。
- 对方离线不丢文字。按序号补洞后未读仍然正确。
- 发送事务失败时，对方看不见这条文字，outbox 里也没有对应的 `message.created`。
- 删除只改自己的 `clear_seq`。对方仍能读到删除前的文字，且收不到删除事件。
- 删除之后对方新写的一条，使自己的列表重新出现，且自己读不到删除前的文字。
- 同一 `client_id` 重放不产生第二条。
- 全程不调用 `complete_ask`、`complete_match`、`chat`。
- 内容安全超时不落库。目录超时不挡住已有会话的列表和补洞。
- 昵称超过 12 字或头像未通过时，已生效的昵称和头像不变。保存成功后，在线会话收到带新显示名和新头像地址的 `thread.updated`。
- 未知 `type` 不落库。文字、未读和删除都走同一条连接。
