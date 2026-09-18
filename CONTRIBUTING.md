# 贡献指南

本仓库是刻灵服务端仓库：`yinjiuying1993/kelin_server`。只接受 API、worker、数据库迁移、OpenAPI、服务端配置、测试和 fixture；iOS 实现与共享计划不属于本仓库。

本地刻灵工作区还应阅读权威规范：

`/Users/robosen/Desktop/work/1_plan/governance/GIT_COMMIT_AND_PR_STANDARD.md`

该本机路径仅用于本项目工作区，不作为 GitHub 可访问链接。若本文与统一规范冲突，以统一规范为准。

## 开始前

- 一次只执行一个 `Sxx-Txx` 或 `Pxx-Txx`；非计划缺陷使用 `BUG-<id>`。
- 检查当前是否在 `main`、`git status` 和 `git diff`，保护用户已有修改。
- **V1 上线前**：直接在 `main` 上开发，不新建功能分支，不开 PR。上线后恢复分支与 PR，见统一 Git 规范第 0 节。
- 涉及 API 时先评审 OpenAPI，固定 SHA，再实现兼容服务端、fixture 和消费者。
- 数据库结构与数据迁移只允许通过 Alembic revision 管理。
- 不得修改工作区的 `1_plan/` 或 `0_kelin/`。

### 空仓库首次提交

当前远端尚无 `main`。允许一次性将仅包含仓库治理文件的 bootstrap 提交推送为 `main`：

```text
chore(repo): 建立仓库协作基线

Refs: GOV-001
```

首次提交不得包含业务代码。AI 未获用户明确授权时仍不得自行 commit 或 push。V1 上线前后续功能继续在 `main` 上开发；上线后再恢复分支与 PR。

## 分支（V1 上线后恢复）

格式：

```text
<type>/<Pxx-Txx>-<short-description>
```

`type` 可用 `feat`、`fix`、`refactor`、`test`、`docs`、`chore`、`security`、`hotfix`。描述使用小写英文和连字符。

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c feat/P06-T02-bootstrap-endpoint
```

## Commit

使用 Conventional Commits：

```text
type(scope): 中文摘要

Refs: P06-T02
```

常用 scope：

`api`、`auth`、`db`、`migration`、`rls`、`openapi`、`worker`、`chat`、`memory`、`feed`、`voice`、`social`、`notification`、`account`、`security`、`test`、`infra`

- 标题不超过 72 个字符。
- 一个 commit 表达一个可审查、可回滚的意图。
- 测试必须与对应行为在同一 PR。
- migration、模型适配、回填与测试应按依赖形成可理解的提交序列。
- 禁止 `WIP`、`final`、`fix stuff`、`misc changes` 等无意义提交。
- Breaking change 使用 `type(scope)!:` 并添加 `BREAKING CHANGE:` footer。

示例：

```text
fix(rls): 阻止跨账号读取记忆

Refs: BUG-142
```

## Pull Request（V1 上线后恢复）

V1 上线前不新建 PR。上线后标题：

```text
[Pxx-Txx][Server] 目标
```

契约专属 PR 可使用 `[Pxx-Txx][Contract] 目标`，缺陷可使用 `[BUG-<id>][Server] 目标`。

- 一个任务一个分支、一个 PR，不捆绑无关重构或下一任务。
- 未完成实现、测试、migration 或证据时保持 Draft。
- Ready 前填写模板所有适用项，粘贴完整命令和真实退出码。
- 建议不超过 15 个业务文件或 500 行人工代码；超出需说明或拆分。初始化、migration、锁文件和生成物可合理例外。
- 跨仓变更必须链接 iOS PR，并注明 `Server 兼容契约 → iOS → Server 清理` 的合并顺序。

## Server 检查项

仓库初始为空，具体命令必须在工程建立后根据真实 `pyproject.toml`、锁文件、Alembic、测试和 CI 配置发现，不得虚构。适用检查基线：

- Ruff lint/format（若实际采用 Ruff）。
- 静态类型检查（按实际采用的 mypy、Pyright 或其他工具）。
- Pytest 单元与集成测试。
- Alembic 从空数据库升级至 head 并验证 schema。
- RLS 双账号测试：账号 A 不可读取或修改账号 B 数据。
- OpenAPI SHA、schema 与 fixture 一致性检查。
- Secret scan（按实际 CI 工具）。
- worker 重试、幂等、超时与失败恢复测试（涉及时）。

所有结果必须记录实际完整命令、真实退出码与关键证据。没有工具或脚本时，应在对应初始化任务中先建立可复现入口。

## 契约与 Migration

OpenAPI 变更顺序：

1. 评审契约并固定 SHA。
2. Server 先提供向后兼容行为。
3. iOS 使用该 SHA 和 fixture 迁移。
4. 联调并度过兼容窗口。
5. 独立 PR 清理旧行为。

优先使用新增可选字段或版本化能力。Breaking change 必须列出调用方、迁移窗口和回退方式，消费者迁移完成前不得删除旧契约。

Migration 使用 `expand → backfill → switch → cleanup`，每一步说明 revision、upgrade/downgrade、锁与耗时、数据量、不可逆步骤、备份恢复和应用回滚后的 schema 兼容性。可能丢数据时不得把盲目 downgrade 当作回滚，应采用安全的前向修复。

## Secret、隐私与数据安全

禁止提交：

- `.env`、令牌、密码、私钥、数据库/云凭据和生产配置。
- 真实姓名、账号、手机号、邮箱、设备标识、聊天、记忆、语音或其他隐私数据。
- 生产数据库 dump、未脱敏日志、真实用户 fixture。
- 虚拟环境、缓存、覆盖率临时文件、运行时数据库和构建产物。

fixture 必须使用合成、脱敏数据。涉及用户数据的 PR 必须检查 RLS、认证授权、幂等、日志脱敏、保留期限和账号删除行为。

## Review 与人工验收

- 至少一次独立审查；所有 review conversation 必须解决。
- 契约、migration、RLS、认证授权和账号删除必须专项审查。
- 作者不能用 AI 报告替代真实数据库、双账号或人工验收证据。
- 个人项目可记录“人工自审 + 独立 AI 只读复查”，但发布门禁仍由人工签署。

## AI 边界

AI 必须先检查 `git status`、分支和 diff，只改当前任务范围，保护用户修改，并交付 PR 草案。除非用户明确要求，AI 不得 commit、push、创建 PR；任何情况下不得自行批准、签署验收或合并，也不得声称未实际运行的测试、数据库、CI 或安全检查已通过。

## 合并与故障处理

- 只允许 Squash merge；PR 标题即最终 commit 标题。
- 保持 `main` 线性历史，合并后删除分支。
- 禁止 merge commit、rebase merge、直接或 force push 到 `main`。
- checks 失败、conversation 未解决、缺少人工验收、存在 P0 或安全问题时禁止合并。
- 冲突应理解双方意图后逐项解决并重跑检查；不得用 `git reset --hard` 丢弃共享改动。
- 已合并问题通过新 revert/hotfix PR 修复，不改写 `main` 历史；hotfix 仍需最小范围、检查、审查与人工验收。

仓库管理员后续应在 CI 建立后，按真实 check 名称保护 `main`，要求 conversation resolution 和 linear history，阻止 force push/deletion，开启自动删分支并只保留 Squash merge。本文不表示这些设置已经配置。
