# 刻灵 P16–P20 联调说明（iOS 接入）

V0 全量入口：[`ios-integration.md`](ios-integration.md)。

面向 iOS ↔ 本机 FastAPI，覆盖 **社交串门、设备通知、七日鉴定、设置/注销、找回与 Debug**。  
前置：已按 [`p01-p07-integration.md`](p01-p07-integration.md) 孵化（`hatched_at` 有值）。房间/Chat [`p08-p10-integration.md`](p08-p10-integration.md)；记忆 [`p11-integration.md`](p11-integration.md)；投喂 [`p12-integration.md`](p12-integration.md)；成长 [`p13-integration.md`](p13-integration.md)；语音 [`p14-integration.md`](p14-integration.md)；共学 [`p15-integration.md`](p15-integration.md)。  
**不是阶段验收**；`1_plan/STATUS.md` 仍由人工更新。本机工作区 `3_ios/` 仍无 App 源码，本文给 **192.168.100.205** 上的 iOS 工程改。

| 项 | 值 |
| :--- | :--- |
| OpenAPI SHA-256 | `fed40c13c30d2203f20ed83c7ad208042c13f35e330d3491e407f22be0593230` |
| Alembic head | `20260908_0019`（P20 **未改库**） |
| `schema_version` / `api_version` | `2` / `v1` |
| Fixture 根 | `4_server/fixtures/`（与 SHA 同锁） |

P15 文档里的 SHA `743b462b…` 在 **P16 起加社交 / 设备 / 鉴定 / 设置 / 注销 / 找回** 后作废。解码请用上表。P15 文末「不要打社交、鉴定卡、注销、找回」已被本系列取代：本机 **可以** 打下列阶段文档里的端点。

分阶段说明：

| 阶段 | 文档 | 公开写/读 |
| :--- | :--- | :--- |
| P16 | [`p16-integration.md`](p16-integration.md) | 好友、明信片；串门只由 worker 规划/结算 |
| P17 | [`p17-integration.md`](p17-integration.md) | `POST /devices`；服务端 **不发 APNs** |
| P18 | [`p18-integration.md`](p18-integration.md) | `GET /report`、`POST /report/line` |
| P19 | [`p19-integration.md`](p19-integration.md) | `PATCH /spirit`、`DELETE /account` |
| P20 | [`p20-integration.md`](p20-integration.md) | `POST /recall`；Debug **仅 dev** |

### 进程（相对 P15 已变）

本机 `compose.yaml` 现为 **api + scheduler + worker**（另有 `postgres-test` 仅 tools profile）：

| 进程 | 做什么 |
| :--- | :--- |
| `api` | HTTP。`0.0.0.0:8000` |
| `scheduler` | `python -m app.scheduler`。入队 state / visit_plan / visit_settle / usage_rollup / notification_plan |
| `worker` | `python -m app.worker`。执行 visit 规划与结算、通知规划、鉴定生成、账号注销。**不发 APNs** |

P16 明信片、P17 通知规划、P18 `generating → partial/ready`、P19 注销落地都依赖 **worker 在跑**。只重启 api 不够。改 Python / `.env` 后重启对应容器。

环境、JWT、ATS、IP 与 P01–P07 第 2 节相同：`API_BASE_URL=http://192.168.100.212:8000`。

**不要**在联调期间对同一库跑会 `DROP SCHEMA` 的 db pytest（会清精灵/好友/明信片/鉴定，需重新孵化）。

### SessionStore（五阶段共用）

合入规则与 P13 相同：

```
patch.snapshot_version < 本地 → 丢弃，立刻 GET /bootstrap
patch.snapshot_version >= 本地 → 合入（同版本 = 幂等回放）
```

不要把 `patch.spirit` / `patch.room` / `patch.social` / `patch.report` 为 `null` 当成清房间、清好友或清鉴定卡。null 表示本包不改那一块。

### 公开 API 没有这些东西

- `POST /visits` 或任何客户端选 host / NPC 的串门创建  
- identity linking / magic-link / `/bind` / `/link`  
- prod / 默认 test OpenAPI 里的 `/api/v1/debug/*`  
- 服务端代发 APNs（规划写入 outbox，发送未接线）

`GET /bootstrap` 本机 `unread_postcards` **仍恒为 `[]`**（与 P15 的 `active_pact` 一样：聚合有字段、公开快照未填）。未读明信片以 `GET /postcards?unread_only=true` 及 mutation 的 `patch.postcards_upsert` 为准。`report` 已是完整 `ReportSnapshot`（含 `locked`）。
