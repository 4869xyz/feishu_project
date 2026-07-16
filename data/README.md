# `data` 目录

此目录只存放本地运行产物，不存放源代码、密钥或长期业务数据库。

| 路径 | 内容 | Git 规则 |
| --- | --- | --- |
| `data/inbox/` | 聊天中直接上传的 Excel 附件，命名为 `<message_id>__<安全文件名>`。 | 忽略实际文件，保留 `.gitkeep`。 |
| `data/archive/` | Sheets/Wiki 链接的 XLSX 导出，按 `YYYY-MM/sender_open_id/` 分目录。 | 忽略实际文件，保留 `.gitkeep`。 |
| `data/aggregation/state/` | 按聊天和发送人隔离的当前批次、上传顺序及已处理来源 ID。 | 忽略实际文件。 |
| `data/aggregation/output/` | 自动生成的销售汇总 XLSX，按月份和批次所有者哈希分目录。 | 忽略实际文件。 |

归档文件名固定为：

```text
SUB-YYYYMMDD-HHMMSS-messageid后8位_文档标题.xlsx
```

所有下载先写入同目录 `.part` 临时文件，成功后才原子改名。残留 `.part` 表示中断或失败，应在排查后手动清理；不要提交它或任何真实销售数据。
