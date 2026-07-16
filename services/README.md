# `services` 模块

本目录保存与飞书传输无关的销售汇总业务逻辑。它不调用飞书 API，也不负责长连接或用户回复。

| 文件 | 职责 | 主要公开接口 |
| --- | --- | --- |
| `sales_workbook_aggregator.py` | 选择并校验源 XLSX 中的签约表，按 SOP 重建模板签约汇总表，并验证控制总额和公式结构。 | `validate_source_workbook()`、`aggregate_sales_workbooks()`、`SourceValidationResult`、`AggregationResult` |
| `aggregation_batch_store.py` | 持久化按聊天和发送人隔离的临时批次、固定云表、latest 缓存与输出路径，并兼容迁移 v1 状态。 | `AggregationBatchStore`、`RegisteredCloudSource` |
| `download_cache.py` | 在限定目录内尽力删除非活动缓存，并保护活动批次、模板和 `.gitkeep`。 | `DownloadCacheCleaner`、`CacheCleanupResult` |

汇总引擎的调用者必须为每个源文件提供稳定且唯一的 `source_file_id`。该 ID 只用于防止同一飞书来源重复加入批次，不用于业务内容去重。

固定云表使用链接类型和原始 Token 生成稳定 `cloud-...` 来源 ID。登记顺序跨汇总保留；成功汇总只清除临时批次，固定来源由监听器在每次汇总前刷新并校验。

引擎当前只修改模板中的签约目标表。回款表和其他非目标工作表的工作表 XML、关系和引用部件会在保存后恢复，以避免 `openpyxl` 丢失模板图片等不支持对象。回款解析代码不进入当前运行路径。正式规则以 [`../excel_file_example/2026年销售数据汇总SOP.md`](../excel_file_example/2026年销售数据汇总SOP.md) 为准。
