# 监听器运行安全实施计划

**状态：** 已完成  
**创建日期：** 2026-07-15  
**执行日期：** 2026-07-15

## 1. 背景与目标

长连接监听器需要明确消息准入规则，并避免重复启动、第三方 SDK 重复打印日志或连接 URL 暴露临时凭证。此次变更把这些隐式运行假设固定为代码和测试约束。

成功标准：

- 私聊可直接使用，群聊仅在直接 `@` 机器人时处理，`@所有人` 不触发；
- 项目与 Lark SDK 日志只经过一条根日志管线；
- HTTP URL 型 INFO 日志受抑制，已知 WebSocket 临时凭证被脱敏；
- 同一项目同时只能运行一个监听器实例；
- 对应离线测试和项目管理文档同步完成。

## 2. 范围

**包含：** Channel 消息准入策略、日志管线与凭据脱敏、跨平台单实例文件锁、离线测试和相关文档。

**不包含：** 飞书后台权限变更、进程守护/自动重启、分布式锁、日志轮转、告警平台或多应用实例管理。

## 3. 技术决策

- 使用 `PolicyConfig(dm_policy="open", group_policy="open", require_mention=True, respond_to_mention_all=False)` 显式固定准入行为。
- 移除 Lark SDK 自带处理器并让记录传播到根日志；Lark 与 `httpx` 最低日志级别固定为 `WARNING`，避免重复控制台记录和带用户标识的 URL 型 INFO 日志。
- 自定义日志格式化器清理 `access_key`、`ticket`、`access_token` 和 `app_secret` 查询参数，作为第三方异常消息的兜底保护。
- 使用 `logs/feishu_bot_listener.lock` 持有非阻塞的一字节文件锁：Windows 使用 `msvcrt`，POSIX 使用 `fcntl`；锁随进程生命周期释放，文件可保留。
- 保留进程内 `asyncio.Lock`，使附件下载与表格导出串行进入阻塞式文件处理区。

## 4. 影响文件

| 路径 | 操作 | 原因 |
| --- | --- | --- |
| `feishu_bot_listener.py` | 修改 | 固定准入策略、统一日志、增加单实例锁。 |
| `.gitignore` | 修改 | 忽略 `logs/*.lock` 运行产物。 |
| `tests/test_feishu_bot_listener.py` | 修改 | 覆盖准入、日志脱敏/去重和锁冲突。 |
| `README.md`、`ARCHITECTURE.md`、`docs/plans/README.md`、`logs/README.md`、`tests/README.md`、`VersionLog.md` | 修改 | 同步使用、架构、计划索引、运行产物、测试基线和版本记录。 |

## 5. 原子 Checklist

- [x] 显式配置私聊与群聊提及策略。
- [x] 统一根日志管线并抑制 URL 型 INFO 日志。
- [x] 对 WebSocket 临时凭证查询参数做格式化兜底脱敏。
- [x] 增加跨平台单实例文件锁与启动冲突提示。
- [x] 增加监听器离线测试并运行完整 pytest。
- [x] 更新相关文档与 `VersionLog.md`。

## 6. 测试与验收

- 自动化：`.venv\Scripts\python.exe -m pytest`
- 结果：2026-07-15 通过 **50** 项测试。
- 新增覆盖：群聊必须直接提及机器人、忽略 `@所有人`、WebSocket 查询参数脱敏、Lark 日志单管线、`httpx` INFO 抑制和第二实例锁冲突。
- 运行环境复核：部署前可启动一个监听器后再次执行入口，确认第二个进程报告启动失败；在测试群分别验证直接 `@` 与仅 `@所有人` 的行为。

## 7. 执行记录

| 日期 | 状态 | 记录 |
| --- | --- | --- |
| 2026-07-15 | 已完成 | 运行准入、日志安全、单实例锁、离线测试和项目文档同步完成；50 项测试通过。 |
