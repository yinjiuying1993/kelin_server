# 刻灵 语音 Chat 带回 TTS（iOS 接入）

面向 **192.168.100.205** 上的 iOS 工程 ↔ 本机 FastAPI（`192.168.100.212:8000`）。  
覆盖：**用户发语音后，服务端把大模型回复合成 TTS，随 `POST /chat` 一并返回**。  
ASR 手势 / 互斥 / 权限仍见 [`p14-integration.md`](p14-integration.md)。Chat FIFO / 孵化见 [`p08-p10-integration.md`](p08-p10-integration.md)。全量锁见 [`ios-integration.md`](ios-integration.md)。  
**不是阶段验收**；本机 `3_ios/` 无 App 源码。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af` |
| Alembic head | `20260908_0019`（**未改库**） |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture | `fixtures/POST_api_v1_chat/success.json`（文字回合 `speech_audio=null`）；合成形状同 `fixtures/POST_api_v1_synthesize/success.json` 的 `resource` |
| Chat 客户端超时 | **48s**（上游 Chat 18s + TTS 18s + 余量） |

`cdb52009…` / `fed40c13…` 已作废。Chat DTO 必须按上表 SHA 重新解码；缺 `speech_audio` 键应失败可观测。

改 Python 后需 `docker restart kelin-server-api`。TTS 对象在 **进程内存**，重启后旧短链 404。

---

## 1. 客户端时序

```text
按住录音 → 松开
POST /api/v1/transcribe     multipart：client_id + audio
  空结果 ASR_EMPTY_RESULT   → 「没听清，再说一次」；零消息；不 Chat
  非空 text                 → 不进编辑框，进入 P09 FIFO
POST /api/v1/chat
  client_message_id = 新 UUID（≠ transcribe 的 client_id）
  content           = 转写文本
  source            = "voice"
  onboarding        = false
  context           = 与文字 Chat 相同
  HTTP 超时         = 48s
成功 200：
  先插入用户消息 + 精灵文字（spirit_message.content）
  看 data.resource.speech_audio
    对象 → 改 host 后 GET 播放
    null → 只显示文字（「今天先看字吧」若 quotas/tts 已满或合成失败）
短链 10min 过期或 API 重启 404 → 再 POST /synthesize 换新 URL
```

文字模式（`source=text`）：`speech_audio` **恒为 null**。右上角播报仍走 `POST /synthesize`。  
语音回合 **不要**在 Chat 成功后再自动打一次 `/synthesize`（服务端已合成；再打只会命中缓存或重复占超时）。

录音与播放必须互斥：按下录音立刻停播；有音频要播时先取消录音。

---

## 2. 必须改：Chat DTO

`data.resource` **必有** `speech_audio`，类型为对象或 JSON `null`。

文字 / 孵化 fixture 示例（`POST_api_v1_chat/success.json`）：

```json
"speech_audio": null
```

语音成功时形状与 `/synthesize` 的 `data.resource` 相同：

```json
{
  "type": "speech_audio",
  "audio_url": "https://kelin.invalid/object/kelin-tts/tts/<owner>/<spirit_message_id>/default.m4a?exp=<unix>&sig=<hex>",
  "mime": "audio/mp4",
  "duration_ms": 1820,
  "expires_at": "2026-09-14T12:10:00Z",
  "cache_hit": false
}
```

| 字段 | 规则 |
| :--- | :--- |
| `type` | 恒 `speech_audio` |
| `audio_url` | 签名 GET；见第 4 节改 host |
| `mime` | 恒 `audio/mp4` |
| `duration_ms` | ≥1 |
| `expires_at` | UTC `Z`，签发后 **10 分钟** |
| `cache_hit` | 同消息 24h 内再合成可为 `true`；Chat 原 ID 重放也可能仍是 `false`（幂等回放），不要用它做 UI |

同包 `data.quotas[]`：语音回合成功合成时含 `capability=tts`（`limit=20`）。`cache_hit=true` 时 `used` 不 +1。Chat 自己的 `chat` 配额仍单独计。  
`patch.spirit` 在 Chat 成功包里通常有值（回合结算）；TTS 失败不会把已落库的文字回合打掉。

未知字段 / 缺 `speech_audio` 键：解码失败可观测。不要把旧 Chat DTO 当兼容层默默丢掉该字段。

---

## 3. `speech_audio = null`（Chat 仍 200）

只显示精灵文字，不阻断输入、不插第二条消息、不改 `client_message_id`。

| 原因 | 客户端怎么判断 | 文案 |
| :--- | :--- | :--- |
| `source=text` / 孵化文字 | 请求就是 text；字段为 null | 不播 |
| 回复 &gt;200 字 | null；TTS 未计 | 只显示文字 |
| 当日 TTS 20 次已满 | null；同包或随后 `quotas` 里 tts `used==limit`，或稍后点播 429 | 「今天先看字吧」 |
| 上游 TTS 超时 / 不可用 | null；Chat 已是 200，**不会**变成 503/504 | 只显示文字 |
| 本机未配 `BAILIAN_TTS_MODEL` | 同上 | 只显示文字 |

Chat **失败**（503/504/429 chat 配额等）仍按 P09：不插假精灵回复，原 `client_message_id` 可重试。TTS 失败不会变成这种 Chat 失败。

---

## 4. 播放：只换 host

```text
https://kelin.invalid/object/kelin-tts/tts/<owner>/<message>/default.m4a?exp=…&sig=…
        ↓ 只换 scheme + host
http://192.168.100.212:8000/object/kelin-tts/tts/<owner>/<message>/default.m4a?exp=…&sig=…
```

- 保留 path、`exp`、`sig`。不要客户端 HMAC，不要拼对象路径。  
- GET **不必**带 JWT。  
- 成功：`Content-Type: audio/mp4`。  
- 过期 / 坏签 / API 重启丢对象 → **404**。用 `spirit_message.id` 再 `POST /synthesize`（`voice_profile=default`，**不要传 text**）。  
- `GET /object/...` 不在 OpenAPI，不要生成 SDK。  
- 日志不要打完整签名 URL、音频 bytes、转写/回复正文。  
- fixture 里的 `audio_url` 常无 query，不能当 live 播放地址。

---

## 5. 超时与幂等

| 请求 | 建议超时 | 重试键 |
| :--- | :--- | :--- |
| `POST /transcribe` | 与 P14 相同（上游 ASR 22s） | 该次录音的 `client_id` |
| `POST /chat` `source=voice` | **48s** | `client_message_id` |
| `POST /chat` `source=text` | 仍可 25–48s；本机统一 48s 也可以 | `client_message_id` |
| `POST /synthesize` | 23s（P14） | 该次合成的 `client_id`（≠ Chat ID） |

三个 UUID **禁止混用**：

```text
transcribe.client_id
chat.client_message_id
synthesize.client_id
```

同一语音句重试 Chat：必须同一 `client_message_id`。服务端会回放文字，并再挂同一套 TTS 幂等（不二次计 TTS，除非缓存已被 API 重启清掉）。

---

## 6. SessionStore

Chat 成功包仍按 P09/P13 合入 `patch` + `quotas`：

```text
patch.snapshot_version  <  本地  →  丢弃，立刻 GET /bootstrap
patch.snapshot_version  >= 本地  →  合入
```

- 合入 `quotas` 里的 `chat` / `tts`（若出现）。禁止本地 `used±1`。  
- `speech_audio` 是本轮播放材料，**不要**写进 SessionStore 当长期状态；过期即丢。  
- `patch.room=null` 不是清房间。

`tts_on`（设置里「自动语音播报」）只管 **文字模式**是否自动 `/synthesize`。语音回合按本文：有 `speech_audio` 就播，与开关无关。

---

## 7. 建议联调

前置：已孵化（`hatched_at` 有值）；API 已重启加载本契约；真机 ATS 放行 `192.168.100.212`。

1. DTO 锁 SHA `98d1c742…`；`ChatTurnResource.speech_audio` 可解 `null` 与对象。  
2. `POST /chat` `source=text` → 200，`speech_audio==null`，有精灵文字。  
3. `POST /transcribe` 合法 m4a → 200 `text`。  
4. `POST /chat` `source=voice`，`client_message_id` 新 UUID，超时 48s → 200。  
5. 本机已配 TTS：`speech_audio.audio_url` 非空，`mime=audio/mp4`，`quotas` 含 tts `used=1`。改 host 后 GET 能播。  
6. 同一 `client_message_id` 再 POST Chat → 回放同一 `spirit_message.id`，音频仍可播，tts `used` 不增加。  
7. 等 10 分钟或重启 api 后旧 URL 404 → `POST /synthesize` 只带 spirit `message_id`。  
8. 文字右上角点播：仍 `POST /synthesize`，不要指望 text Chat 带音频。

---

## 8. 不要做

- 不要在语音 Chat 成功后再自动 `/synthesize`  
- 不要把 ASR 文本放进输入框  
- 不要用 Apple Speech / 直连百炼  
- 不要 `synthesize` 传 `text` 或非 `default` 的 `voice_profile`  
- 不要本地计次、本地拼 path、伪造 `sig`  
- 不要把 Chat 的 200 + `speech_audio=null` 当成整轮失败  
- 不要把 `GET /object/...` 写进 OpenAPI 生成的 DTO  
- 不要在本机 `3_ios/` 改工程（到 205）  
- 不要宣称真机麦克风 / T09 已通过（除非实际做到）

---

## 9. 人工清单

- [ ] SHA `98d1c742…`，不是 `cdb52009…` / `fed40c13…`  
- [ ] Chat DTO 必解 `speech_audio`（含 null）  
- [ ] 语音 FIFO：transcribe → chat `source=voice`；两套 client id 不混  
- [ ] Chat 超时 48s  
- [ ] 有 `audio_url`：只换 host 播放；录音/TTS 互斥  
- [ ] `null`：只显示文字，不阻断  
- [ ] 过期 404 才 `/synthesize`  
- [ ] 配额只信 `quotas[]`；超限「今天先看字吧」  
- [ ] 日志无 JWT、无签名 URL、无音频、无正文  
