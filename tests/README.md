# `tests` 目录

测试使用 `pytest`，所有飞书 HTTP 响应、导出文件和 Channel 消息都必须由 fake/mock 构造。测试不得访问真实飞书、真实凭据或真实业务文件。

| 文件 | 覆盖内容 |
| --- | --- |
| `test_settings.py` | 环境变量、路径解析、收件与归档目录初始化、大小上限。 |
| `test_feishu_client.py` | token 缓存、重试、脱敏、消息下载、Wiki 节点查询、导出任务及权限错误。 |
| `test_feishu_attachment.py` | 文件消息解析、Excel 白名单、文件名安全和幂等行为。 |
| `test_feishu_table_export.py` | Sheets/Wiki Token 提取、Wiki `sheet`/`bitable` 分流、标题、归档命名和权限映射。 |
| `test_feishu_bot_listener.py` | 监听器的 Wiki 权限提示、群聊提及策略、日志脱敏/去重和单实例锁。 |
| `conftest.py` | 在 `tests_runtime/` 创建并清理隔离临时目录。 |

运行：

```powershell
.venv\Scripts\python.exe -m pytest
```

新增行为时，优先在同一模块层补充离线单元测试，再进行已授权飞书环境的手工验证。

当前基线（2026-07-15）：**50 项测试通过**。
