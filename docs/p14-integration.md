# 刻灵 P14 联调说明（iOS 接入）

面向 iOS ↔ 本机 FastAPI，覆盖 **S05 语音：ASR 转写、TTS 合成、配额、24h 缓存、短播放 URL**。  
前置：已按 [`p01-p07-integration.md`](p01-p07-integration.md) 孵化（`hatched_at` 有值）。Chat FIFO 见 [`p08-p10-integration.md`](p08-p10-integration.md)。成长 / 配额日界见 [`p13-integration.md`](p13-integration.md)。P15 共学 Pact 见 [`p15-integration.md`](p15-integration.md)（**SHA 已换**）。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机工作区 `3_ios/` 仍无 App 源码，本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `cdb52009a4ec253303a483f7a706d0439bc3fae8c728f146bbb7fa89fec71ae2` |
| Alembic head | `20260908_0011`（P14 **未改库**） |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/`（与 SHA 同锁） |

P13 文档里的 SHA `4297869e…` 在 **P14-T01 加语音端点后作废**。P08/P11 旧 SHA 也不要再用。当前全量锁见 [`p16-p20-integration.md`](p16-p20-integration.md)（`98d1c742…`）。  
**语音回合 Chat 带回 TTS**：按 [`p14-chat-voice-tts.md`](p14-chat-voice-tts.md) 改 DTO / 超时 / 播放，不要沿用下文「Chat 后再打 `/synthesize`」当主路径。

P13 文末「不要打 ASR/TTS」已被本文取代：本机 **可以**打 `/transcribe` 与 `/synthesize`。T04–T08 仍是 iOS 工程；T09 真机真实百炼未做。Pact 不要按本文「不要打」——改看 P15。

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| P14-T01 契约 | **OpenAPI / fixture / m4a 格式已锁** | `POST /api/v1/transcribe`、`POST /api/v1/synthesize` |
| P14-T02 ASR | **已接线**。MIME/容器/5MiB/30s → 短事务预留配额 → 唯一 Provider → 提交/释放。Router 不直调百炼。临时 m4a `finally` 删除 | `POST /api/v1/transcribe` |
| P14-T03 TTS | **已接线**。owner 校验 → 正文 ≤200 字 → 24h 进程内缓存 → Provider 合成 → 10min 签名 GET。`source=voice` 的 `POST /chat` 在文字落库后合成，把同一套 `speech_audio` 带回 Chat 响应；失败为 null，Chat 仍 200 | `POST /api/v1/synthesize`、`POST /api/v1/chat` |
| 播放 GET | **已接线，不进 OpenAPI** | `GET /object/{bucket}/{object_path}`（`include_in_schema=False`） |
| P14-T04…T08 | **本机不做** | iOS Audio / 手势 / FIFO / 播放 / 权限 |
| P14-T09 | **未做** | 真机 + 真实百炼复查 |

进程仍是 `kelin-postgres-test` + `kelin-server-api`（`0.0.0.0:8000`）。无独立 worker。改 Python / `.env` 后 `docker restart kelin-server-api`。TTS 转封装依赖容器内 `ffmpeg`；`docker compose up --force-recreate` 会丢（当前不是 runtime 镜像），需再装或按 Dockerfile runtime 重建。

本机 API 已配 `BAILIAN_ASR_MODEL` / `BAILIAN_TTS_MODEL` / `BAILIAN_TTS_VOICE`（`.env`）。客户端 **`voice_profile` 仍只能 `default`**；服务端把 `default` 映射成上游音色，presence 只记 set/unset。上游 TTS 不是 m4a，服务端转封装后再按契约 `audio/mp4` 播放。  
**龙安灵心只挂在 plus 上**；flash 会 `411`。本机 adapter live：静音 ASR 空文本；TTS 出 m4a；把合成音频再转写非空。这不是真机 T09。alias 被清空时两端点仍是 `503 MODEL_UNAVAILABLE`。

24h TTS 缓存是进程内 `PrivateTtsStorage`，**不是**数据库表。`cache_hit=true` 不重复计 TTS 配额。跨用户 `message_id` 是 `404 NOT_FOUND`。

**不要**在联调期间对该库跑会 `DROP SCHEMA` 的 db pytest（会清精灵/消息，需重新孵化）。

环境、JWT、ATS、IP 与 P01–P07 第 2 节相同：`API_BASE_URL=http://192.168.100.212:8000`。

---

## iOS 需要改什么

### 不要做

- 不要用 Apple Speech；不要存百炼 Key / 模型 / 工作区 ID；不要直接向百炼上传  
- `POST /synthesize` **不要传 `text`**；`voice_profile` 只能是 `default`  
- 空识别 / 取消 / 上滑取消 / **&lt;0.8s** 丢弃：**不要插消息**，不要进编辑框  
- 非空 ASR 文本 **不要进草稿**；直接复用 P09 Chat FIFO（`POST /chat`，`source=voice`）  
- 不要本地计 ASR/TTS 次数；只用响应里的 `quotas`  
- 不要本地拼 TTS 对象路径或伪造签名；path / `exp` / `sig` 只能用服务端返回的 URL  
- 不要把 fixture 里的 `audio_url` 当 live 播放地址（缺 `exp`/`sig`）  
- 不要把 `patch.spirit` / `patch.room` 为 null 当成清房间  
- 不要为同一次录音 / 同一次合成换新 `client_id`  
- 不要打社交、鉴定卡、注销、找回（P16 / P20）。Pact 见 [`p15-integration.md`](p15-integration.md)
- 不要在 S04 孵化五句申请麦克风（首次按住才申请是 T08）

### 必须改：DTO 对齐 SHA

锁定 SHA：`cdb52009a4ec253303a483f7a706d0439bc3fae8c728f146bbb7fa89fec71ae2`。

新增解码：

- `POST_api_v1_transcribe/{success,business_error,missing_required_field,empty_result,invalid_mime,too_large,duration_exceeded}.json`
- `POST_api_v1_synthesize/{success,business_error,missing_required_field,cache_hit,quota_exceeded}.json`

续用：

- `POST_api_v1_chat/`（语音直发：`source=voice`，`content` = 转写文本）
- `GET_api_v1_bootstrap/`、`GET_api_v1_messages/`（取 spirit `message_id` 做 TTS）

未知字段 / 缺字段要失败可观测。fixture 的 UUID、时间、`snapshot_version`、`audio_url` 是样例，**live 以响应为准**。

`GET /object/...` **不在 OpenAPI**，不要当公开契约生成 DTO。

### 必须改：SessionStore

语音 mutation **只合入**：

1. `data.quotas`（`asr` 或 `tts`）  
2. `data.patch.snapshot_version`（迟到规则与 P13 相同）

`patch.spirit` / `patch.room` 在本机语音成功包里是 **null**。不要据此把房间或精灵清掉。  
ViewModel **禁止**对 `asr.used` / `tts.used` 做乐观加减。

合入规则仍是：

```text
patch.snapshot_version  <  本地 snapshot_version  →  丢弃，立刻 GET /bootstrap
patch.snapshot_version  >= 本地 snapshot_version  →  合入（同版本是幂等回放）
```

### 必须改：AppPhase（S05）

```text
ready（hatched_at 有值）
  → S05 Chat
       按住录音（T04/T05 状态机；本机 iOS 尚未实现）
       松开发送 → POST /transcribe
         非空 text → POST /chat source=voice（P09 FIFO）
         ASR_EMPTY_RESULT → 文案「没听清，再说一次」，零消息
       source=voice 成功包带 speech_audio（可空）
         有 audio_url → 改写 host 后 GET 播放
         null → 只显示文字（超 200 字 / 配额 / TTS 失败）
       文字模式点播或短链过期 → POST /synthesize
```

录音与 TTS **必须互斥**（T04）：按下停播；播放前取消录音。本文只保证服务端两端点可联。

### 建议联调顺序

1. 重新孵化 → 普通 Chat 至少一轮，记下 `spirit_message.id`  
2. `POST /transcribe` 无 JWT → `401 UNAUTHENTICATED`  
3. 非法 MIME / 超 5MiB / 超 30s → `422 INVALID_INPUT`，`quotas` 不出现 asr 增量  
4. **alias 被清空**：合法 m4a 仍 `503 MODEL_UNAVAILABLE`  
5. 本机已配 ASR alias：合法 m4a → 200，`resource.text`，`quotas.asr.used` +1，limit=60  
6. 空结果 → `422 ASR_EMPTY_RESULT`，`retryable=false`，**仍计** ASR 配额，不插消息  
7. 非空 text → `POST /chat`，`source=voice`，**新的** `client_message_id`（不是 transcribe 的 `client_id`）；成功包读 `speech_audio`  
8. 文字点播或 `speech_audio` 短链过期：`POST /synthesize` 只带 `client_id` + spirit `message_id` + `voice_profile=default`（不要传上游音色名）  
9. **alias 被清空**：仍 `503`。有 TTS alias 时 200，`cache_hit=false`，`tts.used=1`，limit=20  
10. 同一 `message_id` 再请求（可换 synthesize `client_id`）→ `cache_hit=true`，`used` 仍为 1  
11. 把 `audio_url` 的 `https://kelin.invalid` 换成 `http://192.168.100.212:8000`，保留 path 与 query，GET → `audio/mp4`  
12. 过期 URL（10 分钟后或改 `exp`）→ 404；再 `POST /synthesize` 换新短链  
13. 他人 / 用户消息 / 不存在的 `message_id` → `404 NOT_FOUND`，不计 TTS 配额  

---

## 1. P0 能力：现在能联什么

| P0 能力 | 本机状态 | 联调注意 |
| :--- | :--- | :--- |
| `POST /transcribe` 契约 | **可联** | multipart；无 JWT 401 |
| `POST /synthesize` 契约 | **可联** | JSON；禁止 `text` |
| 真实百炼 ASR | **本机 adapter live 已通（非真机）** | 静音 → 空文本 / `ASR_EMPTY_RESULT` |
| 真实百炼 TTS | **本机 adapter live 已通（非真机）** | 客户端只传 `default`；播放仍是 m4a |
| TTS 24h 缓存 / `cache_hit` | **进程内可联** | API 重启丢失；跨用户 404 |
| 播放短 URL | **可联（改 host）** | 10min；坏签/过期 404；不进 OpenAPI |
| 录音手势 / 互斥 / 权限 | **iOS 未交** | T04–T08 |
| 真机麦克风 + 真实百炼 | **未做** | T09 |

---

## 2. `POST /api/v1/transcribe`

multipart，字段 **只有** `client_id` + `audio`。多一个表单字段 → `422 INVALID_INPUT`。不要发 JSON。

| 项 | 值 |
| :--- | :--- |
| MIME | `audio/mp4` \| `audio/m4a` \| `audio/x-m4a` \| `audio/aac`（可带 `codecs=`） |
| 容器 | ISO-BMFF m4a（`ftyp` 品牌 `M4A ` / `mp42` / `isom` 等） |
| 上限 | 5 MiB、**30 秒**（服务端读 `mvhd`） |
| 幂等键 | `client_id`（同一录音重试用同一 ID） |

成功 `data.resource`：

| 字段 | 说明 |
| :--- | :--- |
| `type` | `transcript` |
| `text` | 非空；trim 后至少 1 字 |
| `duration_ms` | 服务端解析的时长 |
| `language` | 如 `zh` |
| `provider_request_id` | 可空 |

同包 `quotas` 含 `capability=asr`，`limit=60`。成功路径 **计 1 次**。  
`patch.snapshot_version` 有值；`patch.spirit` 为 null。

转写成功后客户端：

```text
POST /api/v1/chat
  client_message_id = 新 UUID（Chat 幂等，不是 transcribe 的 client_id）
  content            = resource.text
  source             = "voice"
  onboarding         = false
  context            = 与文本 Chat 相同
```

**禁止**把 `text` 写入输入框。重试 Chat 用同一个 `client_message_id`。  
`source=voice` 成功时 `data.resource.speech_audio` 为合成结果或 `null`。`quotas` 可能含 `tts`。客户端超时按 **48s**（Chat 18s + TTS 18s + 余量）。文字回合 `speech_audio` 恒为 null。

---

## 3. 空结果与格式失败

| 情况 | HTTP | code | 配额 | 消息 |
| :--- | ---: | :--- | :--- | :--- |
| 空转写 / 全空白 | 422 | `ASR_EMPTY_RESULT` | **仍计 1 次 ASR** | 零消息 |
| WAV/JPEG/非法容器 | 422 | `INVALID_INPUT` | 不计 | 无 |
| 超 5 MiB | 422 | `INVALID_INPUT` | 不计 | 无 |
| 超 30 s | 422 | `INVALID_INPUT` | 不计 | 无 |
| 上游超时 | 504 | `PROVIDER_TIMEOUT` | 释放预留 | 无；可换新录音重试 |
| Provider 不可用 | 503 | `MODEL_UNAVAILABLE` | 释放预留 | 无 |

`ASR_EMPTY_RESULT`：`retryable=false`。UI 固定文案 **「没听清，再说一次」**。用 `error.code` 映射，不要用英文 `message`（fixture 是 `empty transcription`）。

取消录音、上滑 80pt 取消、按下不足 0.8s：**不要调用** `/transcribe`。

---

## 4. `POST /api/v1/synthesize`

JSON，`extra=forbid`。字段只有：

```json
{
  "client_id": "<uuid>",
  "message_id": "<owned spirit message uuid>",
  "voice_profile": "default"
}
```

带 `text`、非 `default` 的 `voice_profile`、或多字段 → 422。  
服务端读 **本人** spirit 消息正文，≤ **200** 字。`message_id` 必须是 `role=spirit` 且 `status=generated`。

| 失败 | HTTP | code | 配额 |
| :--- | ---: | :--- | :--- |
| 不存在 / 非本人 / 用户消息 / 非 generated | 404 | `NOT_FOUND` | 不计 |
| 正文 &gt;200 字 | 422 | `INVALID_INPUT` | 不计 |
| 当日 20 次已满（且非 cache hit） | 429 | `QUOTA_EXCEEDED` | `details.quota=tts`，`details.reset_at` |
| alias unset / 上游不可用 | 503 | `MODEL_UNAVAILABLE` | 释放预留 |
| 上游超时 | 504 | `PROVIDER_TIMEOUT` | 释放预留 |

成功 `data.resource`：

| 字段 | 说明 |
| :--- | :--- |
| `type` | `speech_audio` |
| `audio_url` | `https://kelin.invalid/object/kelin-tts/tts/{owner_id}/{message_id}/default.m4a?exp=…&sig=…` |
| `mime` | 恒为 `audio/mp4` |
| `duration_ms` | ≥1 |
| `expires_at` | UTC `Z`，签发后 **10 分钟** |
| `cache_hit` | 命中 24h 缓存为 `true` |

`quotas`：`capability=tts`，`limit=20`。  
缓存 key：`(message_id, voice_profile, model_alias)`，TTL **24h**。命中 **不** `used+1`。  
fixture 的 `success` 是 miss（`cache_hit=false`，`used=1`）；`cache_hit` 样本 `used` 与 miss 相同。

同 `client_id` 重试同一 `message_id`：幂等回放，新签 10min URL。  
换 `client_id`、同一 `message_id`：可 `cache_hit=true`，仍不计第二次配额。

---

## 5. 播放 URL（改 host，不要重签）

live 成功包里的 host 是 `kelin.invalid`（与见闻 PUT 同源约定）。本机播放：

```text
https://kelin.invalid/object/kelin-tts/tts/<owner>/<message>/default.m4a?exp=<unix>&sig=<hex>
        ↓ 只换 scheme+host
http://192.168.100.212:8000/object/kelin-tts/tts/<owner>/<message>/default.m4a?exp=<unix>&sig=<hex>
```

- **保留** path、`exp`、`sig`。不要客户端再 HMAC。  
- GET **不必**带 JWT；签名即授权。  
- 成功：`Content-Type: audio/mp4`，body 为 m4a。  
- 过期、坏签、对象不在（重启丢缓存）→ **404**。再 `POST /synthesize` 取新 URL。  
- 该 GET 不在 OpenAPI；不要写进公开客户端 SDK。  
- 日志不要打完整签名 URL、不要打音频 bytes。

ATS：与 P01 相同，真机访问 `192.168.100.212` 需允许明文 HTTP。

API 重启后进程内对象没了：旧 URL 404；重新 synthesize 会再计配额（缓存已空）。

---

## 6. 配额

`data.quotas[]`：`capability` / `used` / `limit` / `reset_at`（UTC `Z`）。  
日界规则与 P13 相同：用户 timezone 次日 00:00 的 UTC；改系统时区不能刷额度。

| capability | limit | 何时 +1 |
| :--- | ---: | :--- |
| `asr` | 60 | 成功转写 **或** `ASR_EMPTY_RESULT` |
| `tts` | 20 | `cache_hit=false` 的成功合成 |
| `chat` | 100 | 语音直发走 Chat 时另计（与 ASR 独立） |

超限：

```text
HTTP 429
error.code = QUOTA_EXCEEDED
error.retryable = false
error.details.quota = asr | tts
error.details.reset_at = <ISO 8601 UTC>
```

不要本地 `used+1`。格式失败不计 ASR；TTS 404/过长不计。

---

## 7. `snapshot_version` 与幂等

语音成功包带 `patch.snapshot_version`，本机 **不** bump `spirits.version` 去写属性。迟到包仍按第「必须改：SessionStore」处理。

| 键 | 作用 |
| :--- | :--- |
| transcribe `client_id` | 同音频重试回放；换 ID 会再计 ASR |
| synthesize `client_id` | 同请求回放；缓存命中可换 ID 且不计配额 |
| chat `client_message_id` | P09 FIFO；与上面两个 ID **不是**同一个 |

同 `client_id` 改了音频 / 换了 `message_id` → `409 IDEMPOTENCY_CONFLICT`。  
进行中 → `409 IDEMPOTENCY_IN_PROGRESS`，`retryable=true`，稍后原 ID 重试。

---

## 8. 统一错误码（P14）

| HTTP | code | retryable | 处理 |
| ---: | :--- | :--- | :--- |
| 401 | `UNAUTHENTICATED` | false | 重新匿名登录 |
| 404 | `NOT_FOUND` | false | TTS：换正确的 spirit `message_id`；播放 URL 失效则重新 synthesize |
| 409 | `IDEMPOTENCY_CONFLICT` | false | 换新 `client_id` 或恢复原 body |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | true | 稍后原 ID |
| 422 | `INVALID_INPUT` | false | 格式 / 超限 / 多字段 / 正文过长 |
| 422 | `ASR_EMPTY_RESULT` | false | 「没听清，再说一次」；零消息；已计 ASR |
| 429 | `QUOTA_EXCEEDED` | false | 展示 `details.reset_at` |
| 503 | `MODEL_UNAVAILABLE` | true | 上游不可用或 alias 被清空；不要假写消息。本机 **当前已配** alias，正常路径不应再是这条 |
| 504 | `PROVIDER_TIMEOUT` | true | ASR 换新录音；TTS 可原 `message_id` 再试 |

---

## 9. 全链路时序

本机 **已配** ASR/TTS alias。下面「alias unset」只是负例（清空 env 才出现），不是当前默认。

```text
GET  /bootstrap                              已孵化
POST /chat  source=text                      拿到 spirit_message.id
POST /transcribe  multipart                  无 JWT → 401
POST /transcribe  wav                       422 INVALID_INPUT，asr 不计
POST /transcribe  m4a（alias unset）          503 MODEL_UNAVAILABLE（负例）
POST /transcribe  m4a（有 alias，空结果）     422 ASR_EMPTY_RESULT；asr.used+1；不 Chat
POST /transcribe  m4a（有文本）              200 text；asr.used+1
POST /chat  source=voice  content=text        P09 FIFO；另计 chat 配额
POST /synthesize  用户消息 id                404；tts 不计
POST /synthesize  spirit id（alias unset）  503（负例）
POST /synthesize  spirit id（有 alias）      200 cache_hit=false；tts.used=1
GET  改写后的 /object/...                     audio/mp4
POST /synthesize  同一 message_id            200 cache_hit=true；used 仍为 1
10min 后 GET 旧 URL                         404 → 再 POST /synthesize
```

---

## 10. 人工联调清单

- [ ] 解码 SHA 是 `cdb52009…`，不是 P13 的 `4297869e…`  
- [ ] DTO 能解 multipart `/transcribe` 与 JSON `/synthesize`；请求里没有 `text`  
- [ ] `voice_profile` 只有 `default`  
- [ ] 空结果码映射 `ASR_EMPTY_RESULT`，不进编辑框、不插消息  
- [ ] 非法 MIME/过大/过长 422，ASR `used` 不变  
- [ ] `BAILIAN_ASR_MODEL` 清空时 transcribe 仍 503；有 alias 才期待 200 转写  
- [ ] `BAILIAN_TTS_MODEL` 清空时 synthesize 仍 503；有 alias 才期待 200 合成；`voice_profile` 只有 `default`  
- [ ] 非空 ASR → Chat `source=voice`；`client_message_id` ≠ transcribe `client_id`  
- [ ] 跨用户 / 非 spirit `message_id` 是 404；`cache_hit=true` 时 TTS `used` 不增加  
- [ ] 播放把 `kelin.invalid` 换成本机 API host；过期 URL 404 后重新 synthesize  
- [ ] 配额只信 `quotas[]`；429 有 `details.reset_at`  
- [ ] `patch.spirit=null` 不丢房间  
- [ ] 日志无 JWT、无音频 bytes、无签名 URL、无 TTS 正文  
- [ ] 不宣称录音手势 / 互斥 / 权限 / 真实百炼 e2e 已在真机完成（除非实际做到）  

---

## 11. 本范围明确不做

- 在本机 `3_ios/` 改工程（到 192.168.100.205）  
- 改 OpenAPI 字段（SHA 已锁）或新增 public 表  
- 把进程内 TTS 缓存写成跨重启 / 跨实例持久  
- 把 `BAILIAN_*_MODEL` unset 的 503 写成「语音已通」  
- 把 fixture 无 query 的 `audio_url` 写成可直接播放  
- 启用 iOS 持续音频后台模式（T07 停止条件）  
- 用 Apple Speech 或客户端本地计次  

常见坑：继续用旧 SHA；synthesize 传 `text`；空结果进草稿；把 transcribe `client_id` 拿去当 Chat 幂等键；不改 host 去请求 `kelin.invalid`；联调时跑 db pytest 清空库；API 重启后还拿旧播放 URL。

标准 JSON 以仓库 fixture 为准（与本文 SHA 同锁）。live 的 id、`used`、`expires_at`、`audio_url` 的 `exp`/`sig`、`server_time` 以服务端为准。
