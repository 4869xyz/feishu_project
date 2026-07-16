# `tests` 目录

测试使用 `pytest`，所有飞书 HTTP 响应、导出文件和 Channel 消息都必须由 fake/mock 构造。测试不得访问真实飞书、真实凭据或真实业务文件。

| 文件 | 覆盖内容 |
| --- | --- |
| `test_settings.py` | 环境变量、路径解析、收件/归档/汇总目录初始化、模板路径、缓存管理员和大小上限。 |
| `test_feishu_client.py` | token 缓存、重试、脱敏、消息下载、Wiki 节点查询、导出任务及权限错误。 |
| `test_feishu_attachment.py` | 文件消息解析、Excel 白名单、文件名安全和幂等行为。 |
| `test_feishu_table_export.py` | Sheets/Wiki Token 提取、Wiki 分流、标题、归档命名、指定 latest 路径导出和权限映射。 |
| `test_feishu_bot_listener.py` | Wiki 权限提示、群聊提及、日志安全、固定云表命令与刷新、汇总、缓存清理和文件回复。 |
| `test_sales_workbook_aggregator.py` | 单/多工作表选表优先级、签约校验、跨文件非去重、源字体颜色、模板样式隔离、汇总区块空行、公式、控制总额、回款隔离、共享字符串和非目标表保留。 |
| `test_aggregation_batch_store.py` | 批次持久化、v1→v2 迁移、固定来源、发送人隔离、保护路径和输出路径。 |
| `test_download_cache.py` | 非活动缓存删除，以及活动源文件、模板和 `.gitkeep` 保护。 |
| `conftest.py` | 在 `tests_runtime/` 创建并清理隔离临时目录。 |

运行：

```powershell
.venv\Scripts\python.exe -m pytest
```

新增行为时，优先在同一模块层补充离线单元测试，再进行已授权飞书环境的手工验证。

当前基线（2026-07-16）：**88 项测试通过**。
