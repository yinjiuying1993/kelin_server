# iOS 联调字段说明

按当前已挂载的公开契约写给 iOS。`docs/ios-integration.md` 保持原样，本文不改它。  
字段以本文为准。未知键按 `extra=forbid` 拒绝，请求多一个字段就是 `422 INVALID_INPUT`。联调不校验 OpenAPI SHA-256。

| 项 | 值 |
| :--- | :--- |
| Alembic head | `20261012_0102` |
| schema_version / api_version | `2` / `v1` |
| API | `http://192.168.100.212:8000` |
| 真机 | `192.168.100.205` |
| Supabase | `https://rnvfnvowjavtojvzgvtr.supabase.co` |

业务路径都在 `{API}/api/v1`。探活是 `{API}/health/live` 和 `{API}/health/ready`，无 JWT，也不是 envelope。live 成功体是 `{"status":"live"}`。

---

## 会话

匿名登录，`Authorization: Bearer` 的 `alg` 必须是 RS256。业务 body 里不要放 `user_id`。建议每个请求带 `X-Request-ID`。

成功包：

```text
ok: true
data: ...
error: null
request_id: string
server_time: ISO-8601 UTC
```

失败包 `ok: false`，`data: null`，`error` 含 `code`、`message`、`retryable`、`details`。界面只映射 `code`。

写接口的 `data` 一般是 `resource` + `patch` + `quotas` + `events`。  
`patch.snapshot_version` 小于本地时丢掉整包并 `GET /bootstrap`。大于或等于本地时合入。某一块为 null 表示这包不改那一块。`quotas` 整表替换，不要本地加减 `used`。

`client_id`（聊天是 `client_message_id`）在动作第一次发出时生成，失败重试用同一个。同一 id 换 body 得到 `409 IDEMPOTENCY_CONFLICT`。

分页 `cursor` 不透明。`limit` 默认 30，最大 50。换了过滤条件就丢掉旧 cursor。

---

## 进房

`POST /spirits` 成功后即可 `GET /bootstrap`。没有五句引导。

`POST /spirits`

| 字段 | 约束 |
| :--- | :--- |
| client_id | UUID |
| egg | `warm` / `cold` / `wild` |
| name | 1–20 |
| consents.ai_disclosure.document_version | 1–32 |
| consents.ai_disclosure.explicitly_accepted | 只能是 `true`，界面不能预勾 |
| consents.data_notice.displayed | 只能是 `true`，并带 document_version |
| consents.user_terms.displayed | 同上 |

返回的 `patch.spirit.hatched_at` 有值。`stage` 为 `whelp`，`status` 为 `home`。邀请码 8 位，不含 `0` `O` `1` `I`。

`SpiritPublic`：`id` `name` `egg` `invite_code` `closeness` `curiosity` `sharpness` `nocturnal` `stubborn` `hunger` `energy` `mood` `bond` `stage` `status` `version` `hatched_at` `created_at`。数值特质和饥饿、精力、心情、羁绊都是 0–100。`stage` 是 `whelp` / `formed` / `awake`。`status` 是 `home` / `away` / `study` / `lost`。

`GET /bootstrap` 整包替换本地。`schema_version` 固定 `2`，`api_version` 固定 `v1`。有精灵时 `room` 必有；没有精灵时 `spirit` 和 `room` 都是 null。

房间只有五件字段：

| 字段 | 值 |
| :--- | :--- |
| stage_image_key | `whelp_image` / `formed_image` / `awake_image` |
| primary_emotion_label | 1–32 |
| letter_key | 可 null |
| text_status | 1–64 |
| status | `home` / `away` / `study` / `lost` |

同包还有 `report`、`quotas`、`feature_flags.remote_search`。`latest_memories`、`unread_postcards` 现在是空列表，`active_pact` 是 null。记忆走 `GET /memories`，明信片走 `GET /postcards`，共学走 Pact 接口。阶段和主情绪读房间字段，客户端不重算。

联调要求 App 冷启动首页与本机调试工具查询结果一致时，按 `docs/ios-homepage-debug-parity.md` 实现刷新与字段绑定。

---

## 聊天与抽取

`POST /chat`

| 字段 | 约束 |
| :--- | :--- |
| client_message_id | UUID |
| content | 1–4000 |
| source | `text` / `voice` |
| context.timezone | 1–64 |
| context.local_hour | 0–23。只给表达用，不参与配额日界和走失 |
| context.weather | 1–32。聊天入参，不是房间字段 |
| context.city | 可 null，最长 40 |

不要传 `onboarding`。

`data.resource` 类型是 `chat_turn`：`user_message`、`spirit_message`、`conversation_window_id`、`should_extract`、`speech_audio`、`usage`。窗口 id 由服务端给，客户端不传消息起止 id。`should_extract=true` 时再调抽取。`generation_source=stub` 不要显示成已接通的模型。

`source=voice` 且回复已落库时，`speech_audio` 可能有值：`audio_url`、`mime=audio/mp4`、`duration_ms`、`expires_at`、`cache_hit`。文字轮次、回复超过 200 字、TTS 配额用尽或合成失败时它为 null，聊天仍是 200，界面回退文字。

`POST /extract`：`client_id` + `conversation_window_id`。成功时 `resource.status` 为 `processing` 或 `extracted`，`memories` 最多 2 条。抽取失败不回滚已落库的聊天。

`GET /messages` 拉历史，cursor 规则同上。

---

## 记忆

类型只有 `preference` `knowledge` `emotion` `relationship`。状态 `active` `sealed` `deleted`。列表项不含 `deleted`，删除只出现在 `tombstones`（只有 `id` 和 `deleted_at`）。

| 方法 | 路径 | body |
| :--- | :--- | :--- |
| GET | `/memories` | query：`filter`（`all` 或四种类型）、`cursor`、`limit` |
| PATCH | `/memories/{memory_id}` | `client_id`、`expected_version`、`action` |
| DELETE | `/memories/{memory_id}` | `client_id`、`expected_version` |
| DELETE | `/memories` | `client_id`、`confirm` 必须是 `CLEAR_ALL_MEMORIES` |

`action=correct` 时 `summary` 必填（1–500）。`action=seal` 时不能带 `summary`。封存或删除后，下一轮召回不含该条。迟到抽取不会把已封存或已删除的记忆写回来。非 `active` 时是 `MEMORY_NOT_ACTIVE`。

---

## 投喂与找回

`POST /feed` 用 `kind` 区分，没有 `sight`。

| kind | payload |
| :--- | :--- |
| food | `{}` |
| knowledge | `text` 1–10000 |
| emotion | `emotion`：`calm` `happy` `tired` `anxious` `angry`；`note` 可 null，最长 200 |
| promise | `text` 1–500，`remind_at` 带时区 |

约定后续：

| 方法 | 路径 | body |
| :--- | :--- | :--- |
| PATCH | `/feeds/{feed_id}` | `client_id`、`expected_version`、`text` 1–200、`remind_at` |
| POST | `/feeds/{feed_id}/complete` | `client_id`、`expected_version` |
| POST | `/feeds/{feed_id}/cancel` | 同上 |

约定不是进行中时是 `PROMISE_NOT_ACTIVE`。

`POST /recall`：`client_id`，`method` 只能是 `food`。精灵不是 `lost` 时 `SPIRIT_NOT_LOST`。

---

## 语音

`POST /transcribe` 是 multipart，字段只有 `client_id` 和 `audio`。容器为 m4a/aac，MIME 为 `audio/mp4`、`audio/m4a`、`audio/x-m4a` 或 `audio/aac`。最大 5 MiB、30 秒。空结果是 `ASR_EMPTY_RESULT`，不会变成一条聊天。日配额 60。

`POST /synthesize` JSON：`client_id`、`message_id`、`voice_profile` 只能是 `default`。不要传要朗读的文本，服务端读自己的那条精灵消息，超过 200 字拒绝。

播放地址是 `GET /object/{bucket}/{object_path}`，不在 OpenAPI 里。

---

## 共学

没有公开的完成接口。末题在 `POST /pact-answer` 里收口。客户端不传分数、完成度或印记。

| 方法 | 路径 | 要点 |
| :--- | :--- | :--- |
| POST | `/pacts` | `theme` 为 `interview` 或 `notes`。`title` 1–80。`notes` 必须带 `notes_memory_id` |
| POST | `/pact-session` | `client_id`、`pact_id` |
| POST | `/pact-answer` | `session_id`、`expected_session_version`、`question_id`、`text` 1–4000 |
| POST | `/pact-skip` | `pact_id`、`session_date`（日期） |

已有进行中的共学是 `PACT_ALREADY_ACTIVE`。当日已关是 `PACT_DAY_CLOSED`。

---

## 好友、明信片、鉴定、设置、注销

`POST /friends`：`client_id` + `invite_code`。不能加自己（`SELF_FRIEND_NOT_ALLOWED`）。公开资料只有 `id` `title` `stage` `public_marks` `status`。没有 `POST /visits`，串门由 worker 结算。好友公开标记现为空数组。

`GET /postcards`，`PATCH /postcards/{postcard_id}/read`。明信片落地需要 worker。

`GET /report` 未合格也是 200：`status=locked`，`report_id` 和 `card` 为 null。资格在响应的 `eligibility` 里：孵化满 7 天，且普通对话满 10 轮。失败、安全、出门、走失回复不计入。`status` 还有 `generating` `partial` `ready` `failed`。生成离开 `generating` 需要 worker。

卡片若存在，含 `spirit.public_layers`、`room_weather`、`scholar_marks`、`top_traits`（恰好 2 条）、`top_memories`（最多 3 条）、`signature_line`、`invite_code`。房间仍只用四件套，不从卡片读图层、天气或印记。`ready` 时签名句非空。打开卡片读的是生成时的快照。`POST /report/line` 只重试签名句；不可用时 `REPORT_LINE_UNAVAILABLE`。

`PATCH /spirit`：`client_id`、`expected_version`，以及 `name` 或 `preferences` 至少一项。偏好字段均可省略：`tts_on` `visit_on` `timezone`（IANA）`default_city` `location_weather_on` `remote_search_on`。

`DELETE /account` 成功是 202。body：`client_id`，`confirm` 必须是 `DELETE_MY_ACCOUNT`。收到 202 后立刻清本地。进行中是 `ACCOUNT_DELETE_PENDING`。清库需要 worker。

`/api/v1/debug/*` 只在 `APP_ENV=dev` 注册。

---

## 已卸下的路径

这些路径现在是 envelope `404`，`code=NOT_FOUND`，不会结算：

- `POST /onboarding/complete`
- `POST /devices`
- `POST /moderate-sight`
- `POST /storage/sight-upload-url`

公开面也没有设备登记和 APNs。约定提醒若要响，用本机通知。

---

## 错误码

`retryable=true` 才用同一个 `client_id` 自动重试。

| code | 何时 |
| :--- | :--- |
| UNAUTHENTICATED | 无 token、过期、算法不是 RS256 |
| FORBIDDEN | Debug 身份或来源 |
| INVALID_INPUT | 缺字段、多余字段、非法枚举、确认串不对 |
| INVALID_CURSOR | 丢掉 cursor 重拉 |
| NOT_FOUND | 不属于自己，或路由已卸下 |
| CONFLICT | 版本不符，拉 bootstrap |
| IDEMPOTENCY_IN_PROGRESS | 同一 id 稍后再试 |
| IDEMPOTENCY_CONFLICT | 同一 id 换了 body |
| QUOTA_EXCEEDED / RATE_LIMITED | 看 `reset_at`，不要换 id |
| DEPENDENCY_UNAVAILABLE / MODEL_UNAVAILABLE / PROVIDER_TIMEOUT | 可重试。聊天失败不落假回复 |
| SPIRIT_NOT_LOST | 现在不能口粮找回 |
| MEMORY_NOT_ACTIVE | 记忆已封存或删除 |
| ASR_EMPTY_RESULT | 语音识别为空 |
| ACCOUNT_DELETE_PENDING | 注销已在进行 |

本机联调要同时跑 api、scheduler、worker。`postgres-test` 只给会清空库的测试用，不要拿联调账号跑。
