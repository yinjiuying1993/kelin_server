# 刻灵 P18 联调说明（iOS 接入）

面向 iOS ↔ 本机 FastAPI，覆盖 **七日鉴定快照、生成中状态、签名句补全**。  
锁与进程见 [`p16-p20-integration.md`](p16-p20-integration.md)。用 Debug 把孵化日/对话轮次调合格见 [`p20-integration.md`](p20-integration.md)。  
**不是阶段验收**。本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af` |
| Alembic head | `20260908_0019` |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/` |

合格条件（服务端常量）：孵化满 **7 日**（`REPORT_UNLOCK_AFTER`）且普通对话轮次 **≥ 10**（`REQUIRED_DIALOGUE_ROUNDS`）。时区用用户 IANA timezone。

### 服务端已交付（对照本文）

| 文档条目 | 本机实现 | 入口 |
| :--- | :--- | :--- |
| 快照 | **已接线**。`GET /bootstrap.report` 与 `GET /report` 同一 `ReportSnapshot` | `app/schemas/report.py` |
| 未合格 | **HTTP 200 + `status=locked`**。不是业务错误，没有 `REPORT_NOT_ELIGIBLE` 当 HTTP 错 | `GET /api/v1/report` |
| 首次合格 GET | 插入 `generating` 并入队 `report.generate`（一次） | 需要 **worker** |
| 签名句 | 仅 `status=ready` 有 `signature_line`；`partial` 必须 null | `POST /api/v1/report/line` |
| 补句 | 只补 line；其它卡面字段冻结；`client_id` + `expected_version` | 最多 3 次 |

`status`：`locked | generating | partial | ready | failed`。`locked` 时 `report_id` 与 `card` 都是 null。

---

## iOS 需要改什么

### 不要做

- 不要把 `locked` 当成 HTTP 失败或 `error.code`  
- 不要本地算 title / top_traits / signature_line / 是否合格  
- 不要对 `generating` 狂轮询短于产品约定；worker 没起会一直 generating  
- 不要在 `partial` 把旧 line 显示成已生成  
- 不要 POST line 去改 title / 记忆 / 天气  
- 不要发 `REPORT_NOT_ELIGIBLE` 的客户端错误映射给 locked  
- 不要把 fixture 的 `eligible_at` / `signature_line` 钉死  

### 必须改：DTO 对齐 SHA

锁定 SHA：`98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af`。

新增解码：

- `GET_api_v1_report/{success,business_error,missing_required_field,generating,partial,ready,failed}.json`  
  `success.json` 样例是 **locked**（`is_eligible=false`）。  
- `POST_api_v1_report_line/{success,business_error,missing_required_field,idempotent_replay,version_conflict,max_attempts,provider_fail}.json`

`GET /report` 无 body。line：

```json
{
  "client_id": "<uuid>",
  "report_id": "<uuid>",
  "expected_version": 1
}
```

`expected_version` 用卡上的 `card.version`。冲突走 `CONFLICT`。

### 必须改：SessionStore

- `GET /report` 与 bootstrap 的 `report`：**整份替换**快照，不要字段级猜  
- `POST /report/line` 合入 `data.patch.report`（以及 `data.resource` 与卡一致）  
- 不可用记忆：`unavailable=true` 时 **没有** `type`/`summary`，不要用占位编造正文  

标题由特质规则生成（如「夜行毒舌」）；有学者纹则后缀 ` · 学者`。客户端只展示。

---

## 建议联调顺序

1. 新孵化账号 `GET /report` → 200，`status=locked`，`card=null`，`days_remaining` / `dialogue_rounds_remaining` > 0  
2. 确认 bootstrap 的 `report` 同形  
3. 用 Debug（仅 dev）或真实过 7 日 + 10 轮对话后，再 GET → `generating` 或已有 `report_id`  
4. worker 跑完 → `partial`（line null）或 `ready`（有 line）  
5. 若 `partial`：`POST /report/line` 同一 `client_id` 重放幂等  
6. 错 `expected_version` → `CONFLICT`  
7. 未合格账号不要发 line  

---

## 错误码（用 `error.code` 映射 UI）

| code | 典型 UI |
| :--- | :--- |
| （无；locked 是 200） | 鉴定尚未解锁，展示 eligibility |
| `REPORT_LINE_UNAVAILABLE` | 签名句暂时写不了，可按产品重试 |
| `CONFLICT` | 用最新 `card.version` |
| `NOT_FOUND` | report_id 不属于自己或不存在 |
| `INVALID_INPUT` | 缺字段；对 locked/generating 调 line |
| `DEPENDENCY_UNAVAILABLE` | 稍后重试 GET，不要当永久失败 |

英文 `message` 不要直接展示。

---

## 人工联调清单

- [ ] SHA `fed40c13…`  
- [ ] locked 是成功包，UI 走「未解锁」不是 toast 错误  
- [ ] ready 才展示 signature_line；partial 不展示旧句  
- [ ] line 只带 `client_id/report_id/expected_version`  
- [ ] worker 在跑才能离开 generating  
- [ ] 不宣称 7 日真机鉴定已完成（除非实际做到）  

---

## 本范围明确不做

- 在本机 `3_ios/` 改工程  
- 改鉴定规则或 OpenAPI  
- 不跑 worker 却把 generating 当 bug  
- 宣布阶段验收通过或已部署  

常见坑：把 locked 当 4xx；本地写文案冒充 signature_line；GET 合格后不启 worker；继续用 P15 SHA。
