# 刻灵服务端联调文档

当前全量入口（iOS ↔ 本机 API）：

[`ios-integration.md`](ios-integration.md)

部署到 Linux 服务器（Docker：api / scheduler / worker）：

[`deploy.md`](deploy.md)

分册是历史记录。分册里的 OpenAPI SHA，包括 V0 的 `98d1c742…` 以及 `fed40c13…`、`d688d428…`，都不是现行锁。现行字段以 [`ios-integration.md`](ios-integration.md) 为准。

V3 传艺/问艺复用点（`V3-S0-T01`）：[`v3-s0-reuse.md`](v3-s0-reuse.md)

V3 iOS 开发与联调（传艺 / 问艺）：[`v3-ios-integration.md`](v3-ios-integration.md)

模块实现（按现行代码整理）：

- 情绪：[`emotion.md`](emotion.md)
- 情绪缺口修改方案：[`emotion-gap-plan.md`](emotion-gap-plan.md)
- 记忆缺口完善方案：[`memory-gap-plan.md`](memory-gap-plan.md)
- 聊天双 Agent 完善方案：[`chat-agent-plan.md`](chat-agent-plan.md)
