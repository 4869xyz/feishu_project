# `clients` 模块

本目录承载飞书 HTTP 调用、消息附件解析和表格链接导出逻辑；不负责长连接生命周期或用户回复。

| 文件 | 职责 | 主要公开接口 |
| --- | --- | --- |
| `feishu_client.py` | token 缓存、重试、脱敏、消息资源下载、Wiki 查询、导出任务与导出文件下载。 | `FeishuClient`、`WikiNode`、`ExportTaskResult`、异常类型。 |
| `feishu_attachment.py` | 从 Channel 消息提取文件元数据，限制 Excel 后缀并保存至收件箱。 | `ExcelAttachmentDownloader.download_from_message()`。 |
| `feishu_table_export.py` | 从文本识别 Sheets/Wiki 链接，解析 Wiki 后导出并构建归档路径。 | `extract_feishu_table_link()`、`FeishuTableLinkExporter.export_from_message()`。 |
| `__init__.py` | 对外汇总稳定的客户端、模型和异常导入。 | 包级导出。 |

## 链接导出边界

- `/sheets/<token>` 的 Token 直接用于 `type=sheet` 的 XLSX 导出任务。
- `/wiki/<token>` 必须先通过 Wiki 节点接口解析；只有返回 `obj_type=sheet` 或 `bitable` 才能导出，且导出 Token 必须是返回的 `obj_token`。
- Wiki 节点标题优先于导出任务文件名；路径固定为 `data/archive/YYYY-MM/sender_open_id/SUB-..._<title>.xlsx`。
- HTTP 层只抛出结构化异常；`WikiTablePermissionError` 和面向用户的中文回复由监听器层处理。

不要在此目录导入 `feishu_bot_listener.py`，不要在日志、异常或测试固件中放入真实 App Secret、完整 Token、附件或导出内容。
