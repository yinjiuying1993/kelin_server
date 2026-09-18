# 刻灵 P17 联调说明（iOS 接入）

面向 iOS ↔ 本机 FastAPI，覆盖 **APNs 设备登记、免打扰与日限额规划、进程分离**。  
锁与进程见 [`p16-p20-integration.md`](p16-p20-integration.md)。`push_on` / DND / timezone 的 PATCH 见 [`p19-integration.md`](p19-integration.md)。  
**不是阶段验收**。本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af` |
| Alembic head | `20260908_0019` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/` |

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| 设备登记 | **已接线**。按 `installation_id` + `environment` upsert；token 哈希+加密存储 | `POST /api/v1/devices` |
| 响应脱敏 | **已锁**。只有 `device_id, enabled, updated_at` | 响应 **无** 明文 token |
| sandbox / production | **不可互换**。同一 installation 换环境是另一行 | `environment` |
| `enabled=false` | **保留行**，不再规划推送 | 列名 `notifications_enabled` |
| 通知规划 | **worker**。DND、每日最多 **2** 条 active 远程类型 | `app/services/notification_planner.py` |
| APNs 发送 | **故意未接线**。`cron.py` 写明 T04 send absent | 真机收不到远程推是预期 |

Promise 类型在服务端是 `SERVER_LOCAL_ONLY`：规划时抑制远程，约定提醒仍由 **iOS 本地通知** 负责。

公开 payload 只有 `type` + `resource_id`（可为 null）。文案固定：

| type | alert body |
| :--- | :--- |
| `promise` | 你有一个约定 |
| `pact` | 共学有新进展 |
| `postcard` | 你收到一张明信片 |
| `lost` | 刻灵走失了 |
| `care` | 刻灵想你了 |

---

## iOS 需要改什么

### 不要做

- 不要把 `apns_token` / JWT 打进日志、崩溃报告、Analytics  
- 不要用 sandbox token 登记 `environment=production`（或反过来）  
- 不要期望本机 worker 真的打到 APNs；不要据此宣称「真机推送通过」  
- 不要把系统通知权限、定位权限 POST 到 `/devices` 或 `/spirit`（`extra=forbid`）  
- 不要服务端成功前用服务端类型替换 Promise 本地通知  
- 不要把 `enabled=false` 理解成删行；再 `enabled=true` 是同一 installation 打开  
- 不要把 fixture 的 `device_id` 钉死  

### 必须改：DTO 对齐 SHA

锁定 SHA：`98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af`。

新增解码：

- `POST_api_v1_devices/{success,business_error,missing_required_field,idempotent_replay,token_rotate,enabled_false,environment_mismatch}.json`

请求：

```json
{
  "client_id": "<uuid>",
  "installation_id": "<uuid>",
  "apns_token": "<至少16字符>",
  "environment": "sandbox",
  "enabled": true,
  "app_version": "1.0.0",
  "locale": "zh-Hans"
}
```

`app_version` / `locale` 可选。响应只有三个字段。token 轮换用同一 `installation_id` + `environment`、新 `apns_token`。

### 必须改：本机推送策略

1. 登录/拿到 token 后 `POST /devices`；失败不要静默丢 token（可观测）  
2. 用户关推送：`enabled=false`，并关 `PATCH /spirit` 的 `preferences.push_on`（P19）  
3. Promise：继续用 iOS 本地通知；不要等 APNs  
4. 远程类型（postcard / lost / care / pact）：本机 **收不到真实 APNs**；UI 仍以 bootstrap / 列表 / patch 为准  
5. DND 由服务端按用户 timezone + `HH:MM` 窗口抑制规划，客户端不要再猜服务端会不会推  

Dockerfile / 运行时镜像 **不含** scheduler、worker 入口；compose 用独立 command。API 进程不得兼职 cron。

---

## 建议联调顺序

1. 孵化完成；JWT 有效  
2. `POST /devices` 无 JWT → `401`  
3. sandbox 登记 → 200，响应无 token  
4. 同一 `client_id` 重放 → 同一 `device_id`  
5. 换 token 同 installation → `updated_at` 变、`device_id` 不变  
6. `enabled=false` → 200，`enabled` 为 false  
7. 用 production 套同一 sandbox installation（或反过来）→ 按 fixture `environment_mismatch` 处理，不要混用  
8. 确认 compose 里 scheduler 与 worker 都在跑（规划才会入队；发送仍不会发生）  

---

## 错误码（用 `error.code` 映射 UI）

| code | 典型 UI |
| :--- | :--- |
| `INVALID_INPUT` | 缺字段、token 太短、未知 environment、多余字段 |
| `UNAUTHENTICATED` | 重新登录 |
| `DEPENDENCY_UNAVAILABLE` | 稍后重试；不要清本地 token |

英文 `message` 不要直接展示。

---

## 人工联调清单

- [ ] SHA `fed40c13…`  
- [ ] 响应解码只有 `device_id/enabled/updated_at`  
- [ ] sandbox / production 不混 token  
- [ ] 日志无 APNs token、无 JWT  
- [ ] Promise 仍走本地通知  
- [ ] 不宣称真机 APNs 通过  
- [ ] api / scheduler / worker 三进程分离  

---

## 本范围明确不做

- 接线真实 APNs 发送  
- 在本机 `3_ios/` 改工程  
- 改 OpenAPI 或新增 migration  
- 宣布阶段验收通过或已部署  

常见坑：把登记成功当成推送已通；sandbox token 标 production；worker 没起却等规划；把 token 打进 os_log。
