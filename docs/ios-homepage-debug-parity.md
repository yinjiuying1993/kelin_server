# iOS 首页与联调工具数据一致 — 实现方案

面向 `192.168.100.205` 上的 iOS 工程。字段契约仍以 `docs/ios-handoff.md` 为准；本文只规定：**App 启动后首页展示** 与 **联调工具查询结果** 如何对齐、如何验收。

| 项 | 值 |
| :--- | :--- |
| 文档版本 | 2026-10-14 |
| API | `http://192.168.100.212:8000` |
| 联调工具 | `http://127.0.0.1:8765`（本机；按邀请码读写同一 `kelin_test`） |
| 权威接口 | `GET /api/v1/bootstrap` |
| schema / api | `2` / `v1` |

---

## 1. 目标与验收定义

### 1.1 目标

联调时改完调试工具里的精灵数据后，**App 冷启动进首页**看到的值，与**联调工具再次查询**看到的首页相关值一致。不得出现「工具已是新值、App 仍是旧缓存」或「App 自己翻译/重算情绪与工具对不上」。

### 1.2 验收等式（必须同时成立）

同一邀请码、**App 已完成当次冷启动 bootstrap 之后**，下列三者相等：

```text
App 首页展示
  ==  SessionStore 里最近一次 GET /bootstrap 的 data.spirit + data.room
  ==  联调工具「首页对照」区（见 §3）展示值
```

对比时刻：先在工具保存 → 再冷启动（或杀进程重进）App 完成 bootstrap → 再在工具点一次「查询」。不要拿「保存后、App 尚未重进」的工具瞬间值和 App 旧会话比。

### 1.3 不在本文范围

- 不改邀请码生成、Auth、OpenAPI SHA。
- 不要求 iOS 直连 Postgres 或调用 `8765`。
- 不要求 iOS 实现去向/情绪结算算法。
- `text_status` 仍为英文 key（如 `home`、`hungry`）；主情绪文案只看 `primary_emotion_label`。

---

## 2. 权威与职责

### 2.1 唯一权威

首页业务态只认：

```http
GET /api/v1/bootstrap
Authorization: Bearer <RS256 access token>
```

成功包：

```text
ok: true
data.schema_version = 2
data.api_version = v1
data.snapshot_version
data.spirit   // SpiritPublic 或 null
data.room     // RoomPublic 或 null（有精灵时必有）
data.report / data.quotas / data.feature_flags
```

`GET /bootstrap` 会在服务端先按当前时间做去向与情绪结算，再组装 `spirit` + `room`。因此：

- App 展示的是**结算后**的快照，不是磁盘上未结算的旧行。
- 联调工具改库后，必须让 App **重新打 bootstrap**，两边才能对齐到同一版结算结果。

### 2.2 职责切分

| 侧 | 必须做 | 禁止 |
| :--- | :--- | :--- |
| iOS | 冷启动 / 回前台拉 bootstrap；整包替换本地；首页只绑 §3 字段 | 本地重算阶段/情绪/去向；用英文 key 当主情绪文案；用聊天旧缓存顶首页 |
| 联调工具 | 写 `spirit_awareness` / `spirit_emotion` / `spirit_growth` 与宽表镜像；房间情绪跟主情绪 | 当作「绕过结算的第二套首页 API」 |
| 服务端 | bootstrap 结算后返回公开契约；房间主情绪为中文展示串 | — |

iOS 工程在真机；本机 `3_ios/` 无 App 源码。实现落在真机仓库的 SessionStore / 房间页。

---

## 3. 首页对照字段（App ↔ 工具 ↔ bootstrap）

只验收下表。工具里的 `wide`、原因码、瞬时情绪、截止时间等是调试辅助，**不要求**画在首页，也不参与「一致」验收。

### 3.1 房间（四件套 + status）

| 首页 UI | bootstrap `data.room` | 联调工具 JSON 路径 | 说明 |
| :--- | :--- | :--- | :--- |
| 阶段形象 | `stage_image_key` | `room.stage_image_key` | `whelp_image` / `formed_image` / `awake_image` |
| 主情绪文案 | `primary_emotion_label` | `room.primary_emotion_label` | **中文**，如 `平静`、`开心`；长度 1–32 |
| 字条 | `letter_key` | `room.letter_key` | 可 null / 工具空串视为无字条 |
| 文字状态 | `text_status` | `room.text_status` | 英文 key；不要当主情绪展示 |
| 去向 | `status` | `room.status` | `home` / `away` / `study` / `lost` |

主情绪**只**绑 `room.primary_emotion_label`。不要绑 `spirit` 上不存在的情绪字段，不要读已废弃的 `room.layers` / `room.letter` / `room.weather`。

### 3.2 精灵数值与身份（首页需要展示的）

| 首页 UI | bootstrap `data.spirit` | 联调工具 JSON 路径 |
| :--- | :--- | :--- |
| 名称 | `name` | （查询结果顶层 `name`） |
| 邀请码 | `invite_code` | `invite_code` |
| 饥饿 | `hunger` | `awareness.hunger` |
| 精力 | `energy` | `awareness.energy` |
| 心情 | `mood` | `emotion.mood` |
| 羁绊 | `bond` | `growth.bond` |
| 阶段 | `stage` | `growth.stage` |
| 去向（若条上也有） | `status` | `awareness.status` |
| 五维 | `closeness` 等 | `growth.*` |

版本对齐用：

| 用途 | bootstrap | 联调工具 |
| :--- | :--- | :--- |
| 会话版本 | `data.snapshot_version` | `snapshot_version` |
| 精灵 version | `data.spirit.version` | `version` |

联调保存成功后这两个数应 +1；App 重进后本地 `snapshot_version` 必须变成服务端新值。

### 3.3 主情绪中文表（只读参考，客户端勿再映射）

服务端已把算法英文 key 投影为中文。App **直接展示**字符串，不要再做一层英→中。

| 算法 key（库内） | 首页展示 |
| :--- | :--- |
| uneasy | 不安 |
| hurt | 委屈 |
| hungry_and_tired | 又饿又困 |
| hungry | 饿了 |
| tired | 困了 |
| irritated | 烦躁 |
| low | 低落 |
| missing | 惦记 |
| happy | 开心 |
| curious | 好奇 |
| close | 亲近 |
| satisfied | 满足 |
| slightly_hungry | 有点饿 |
| slightly_tired | 有点困 |
| calm | 平静 |

未知 key：原样显示（透传）。投喂情绪入参仍是英文枚举（`calm`/`happy`/…），与首页展示字段无关。

---

## 4. iOS 实现要求

### 4.1 SessionStore：整包替换

收到成功的 `GET /bootstrap` 后，原子替换：

- `snapshot_version`
- `spirit`（整对象；无精灵则为 null）
- `room`（整对象；无精灵则为 null）
- `report`、`quotas`、`feature_flags`（按现有契约）

禁止字段级「有则改、无则留」拼旧房间。禁止用磁盘/SwiftData 里上一次会话的 `room.primary_emotion_label` 顶这次 bootstrap。

### 4.2 必须重新拉 bootstrap 的时机

| 时机 | 动作 |
| :--- | :--- |
| 冷启动（进程新建） | Auth 恢复后立刻 `GET /bootstrap`，成功后再进房间页 |
| App 从后台回前台（`scenePhase` → active） | 再打一次 `GET /bootstrap`，成功则整包替换 |
| 收到 `patch.snapshot_version` **小于**本地 | 丢弃该 mutation 包，立刻 `GET /bootstrap` |
| `409 CONFLICT` / 约定的需校正错误 | `GET /bootstrap` |
| 联调改完工具、要看首页 | 杀进程重进，或切到后台再回前台，触发上表 |

登录成功只打一次、之后几小时不拉，**不满足**本文验收。

### 4.3 写接口 patch 与首页

合入规则（与 `ios-handoff.md` 一致）：

```text
patch.snapshot_version <  本地  → 丢弃，立刻 GET /bootstrap
patch.snapshot_version >= 本地 → 合入（同版本 = 幂等回放）
某一块为 null → 本包不改那一块
```

首页相关补充：

| 包 | `patch.room` | `patch.spirit` | 首页怎么用 |
| :--- | :--- | :--- | :--- |
| Chat | 现为组装后的房间五字段（可能非 null） | 常有，但是宽表镜像 | `room` 非空可替换本地房间；**饥饿/精力/心情以最近一次 bootstrap 的 spirit 为准**，不要用 chat 的 spirit 覆盖首页三条生命值做联调验收 |
| Feed / Recall | 非空时可替换房间 | 非空时可合入 | 合入后仍建议回前台再 bootstrap 一次做联调对齐 |
| 其它 mutation `room=null` | 不改本地房间 | — | 不要当成清房间 |

联调「工具改数 ↔ 首页」只认 bootstrap 路径，不认「只聊一句话刷新首页」。

### 4.4 房间页绑定（伪代码）

```swift
// 唯一数据源：SessionStore 中 bootstrap 写入的 spirit / room
struct HomeRoomView: View {
  let spirit: SpiritPublic
  let room: RoomPublic
  let snapshotVersion: Int

  var body: some View {
    // 形象
    StageImage(key: room.stage_image_key)
    // 主情绪：直接展示中文，禁止本地字典
    Text(room.primary_emotion_label)
    // 去向
    StatusBadge(room.status)
    // 字条
    if let key = room.letter_key { LetterView(key: key) }
    // 生命值：来自 spirit，不要来自 chat patch 缓存
    Hunger(spirit.hunger); Energy(spirit.energy); Mood(spirit.mood)
    // 联调可选：Debug 浮层显示 snapshotVersion
  }
}
```

建议 Debug 构建在房间角上显示 `snapshot_version`，便于和工具顶栏对比。

### 4.5 失败与加载

- bootstrap 进行中：可保留上一帧 UI，但须有明确 loading；失败重试仍打 bootstrap，不要退回本地假结算。
- bootstrap 失败：不要用 Keychain/磁盘旧 `room` 冒充「已与工具对齐」。
- 无精灵：`spirit == null && room == null`，进孵化，不要捏造房间。

---

## 5. 联调操作顺序（给 QA / iOS）

1. 工具打开 `http://127.0.0.1:8765`，输入邀请码 → 查询。
2. 改需要验证的字段（饥饿、精力、心情、主情绪、去向、阶段图等）→ 保存。  
   保存时工具会把上次状态结算和瞬时情绪起止写成现在，自习/出门截止过期会自动延长，主情绪改成与饥饿、精力、去向一致的结果。以保存后表单里的值为准，不要用保存前下拉里的旧选项和 App 对比。在家且最近互动超过 18 小时，打开 App 仍会出门或走失。
3. **杀掉 App 进程**（或切后台再回前台），重新打开，等到 bootstrap 成功进入房间。
4. 工具再点一次「查询」。
5. 对照 §3 表格：App 首页与工具首页相关字段逐项相同；`snapshot_version` 与工具一致。

任一步跳过「App 重拉 bootstrap」或「工具再查询」，结果不一致不记为 iOS 缺陷。

---

## 6. 已知不一致来源（排障）

| 现象 | 原因 | 处理 |
| :--- | :--- | :--- |
| 工具新、App 旧，且 App 的 `snapshot_version` 也旧 | 未重新 bootstrap | 按 §4.2 补刷新 |
| App 的 `snapshot_version` 已新，主情绪仍是英文 | 仍走本地英→中或旧 `layers` | 改绑 `room.primary_emotion_label`，删本地映射 |
| 保存前选了「开心」，保存后表单变成「平静」或「饿了」 | 饥饿、精力或去向盖过了这个标签；工具已按结算规则改写 | 用保存后的表单和 App 比。要钉住「开心」，饥饿和精力都要高于 39，去向不要是 lost |
| 工具显示中文，App 仍英文，且版本已新 | API 进程未加载中文投影 | 服务端重启 `kelin-server-api`（iOS 无代码可修） |
| Chat 后首页饥饿与工具不一致 | 用了 `patch.spirit` 宽表盖首页 | 首页生命值改回 bootstrap 的 spirit |
| 工具查 404 | 邀请码不在当前 `kelin_test` | 用 bootstrap 里的 `invite_code`，不要用本地缓存里已删的码 |

---

## 7. iOS 验收清单

- [ ] 冷启动：Auth 后必打 `GET /bootstrap`，成功后再展示房间。
- [ ] 回前台：再次 `GET /bootstrap`，成功则整包替换。
- [ ] 房间主情绪只读 `data.room.primary_emotion_label`，值为中文，无本地翻译表。
- [ ] 房间不读 `layers` / `letter` / `weather`；形象只读 `stage_image_key`。
- [ ] 首页饥饿/精力/心情来自最近一次 bootstrap 的 `data.spirit`，不被 chat 的 `patch.spirit` 覆盖用于联调验收。
- [ ] `patch.room == null` 不清空本地房间；`patch.snapshot_version` 偏旧则丢弃并 bootstrap。
- [ ] 按 §5 走完：工具保存 → App 冷启动 → 工具再查，§3 字段一致，`snapshot_version` 一致。
- [ ] Debug 构建可看见 `snapshot_version`（推荐）。

---

## 8. 与现有文档关系

| 文档 | 关系 |
| :--- | :--- |
| `docs/ios-handoff.md` | 字段与错误码权威；本文不改其约束 |
| `docs/ios-integration.md` | 总联调说明；合入规则与本文 §4.3 一致 |
| `0_kelin/v1/房间投影/房间投影模块施工说明书.md` | 房间是投影不是独立业务态；客户端不结算 |
| `0_kelin/v1/情绪/kelin_v1_emotion_system_spec.md` §2 | 首页状态标签中文表；由服务端投影 |

实现时若与 `ios-handoff.md` 字段冲突，以 handoff 的类型约束为准，并以本文 §3 为首页对齐清单。
