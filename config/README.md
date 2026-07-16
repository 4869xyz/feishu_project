# `config` 模块

本目录只管理运行配置与本地目录初始化，不包含飞书 API 请求或消息业务逻辑。

`settings.py` 的 `load_settings()` 从本地 `.env` 和环境变量读取配置，显式环境变量优先，并返回不可变的 `Settings`。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `FEISHU_APP_ID` | 无 | 必填的企业自建应用 App ID。 |
| `FEISHU_APP_SECRET` | 无 | 必填的应用密钥；仅保存在本地环境。 |
| `FEISHU_INBOX_DIR` | `./data/inbox` | 直接上传 Excel 附件目录。 |
| `FEISHU_ARCHIVE_DIR` | `./data/archive` | Sheets/Wiki 链接导出的归档根目录。 |
| `FEISHU_AGGREGATION_DIR` | `./data/aggregation` | 汇总批次状态和结果的根目录。 |
| `FEISHU_SALES_TEMPLATE_PATH` | 无 | 必填的销售汇总模板 `.xlsx` 路径。 |
| `FEISHU_MAX_DOWNLOAD_BYTES` | `104857600` | 单文件本地上限，范围为 1 到 100 MB。 |
| `LOG_LEVEL` | `INFO` | Python 日志级别。 |

加载配置时会创建 `logs/`、收件箱、归档和汇总目录。模板存在性和工作表结构在真正汇总时校验。不要提交 `.env`，也不要让 `config` 依赖 `clients`、`services` 或监听器。
