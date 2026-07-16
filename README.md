# 飞书销售表格收件机器人

这是一个 Python 3.11 飞书长连接机器人。它会接收聊天中直接上传的 Excel 附件，或用户发送的飞书 Sheets/Wiki 表格链接，校验并暂存销售数据文件，再按既定 SOP 生成签约汇总 Excel 并发送回当前会话。

## 当前能力

- 下载聊天中直接上传的 `.xlsx`、`.xls`、`.xlsm` 附件到 `data/inbox/`；
- 识别 `https://*.feishu.cn/sheets/<spreadsheet_token>`，直接导出为 XLSX；
- 识别 `https://*.feishu.cn/wiki/<wiki_node_token>`，先解析其真实对象，再导出 `sheet` 或 `bitable`；
- 链接中的查询参数和 `#` 片段不会进入文档 Token；
- Wiki 链接优先采用知识库节点标题；所有链接导出按日期、发送人和消息 ID 归档；
- 使用 `.part` 临时文件原子落盘，并对下载大小、文件名和错误日志做基础保护；
- 私聊消息可直接处理；群聊只有直接 `@` 机器人时才处理，`@所有人` 不触发；
- 使用进程锁避免重复启动监听器，并统一脱敏项目、Lark SDK 与 HTTP 日志。
- 按“会话 + 发送人”维护独立的待汇总批次，上传时立即校验表结构和金额字段；
- 使用 `汇总状态`、`汇总`、`清空汇总` 管理批次，并输出保留模板格式的 `.xlsx` 汇总文件；
- 管理员可发送 `清空下载缓存`，删除本地非活动的收件、归档和历史汇总结果，活动批次源文件会自动保留；
- 汇总签约明细及个人/小组/部门统计；回款汇总当前暂停处理，不读取源回款表，也不修改模板回款表。

不包含数据库导入、Wiki 写入或 Web 管理界面。当前销售汇总只接受 SOP 规定的 `.xlsx` 工作簿结构。

## 目录结构

```text
.
├── feishu_bot_listener.py    # 长连接入口与消息编排
├── config/                   # 环境配置、路径解析和目录初始化
├── clients/                  # 飞书 HTTP API、附件和表格链接处理
├── services/                 # 销售工作簿校验、汇总和批次状态
├── data/inbox/               # 直接上传附件的本地收件箱（Git 忽略）
├── data/archive/             # 链接导出的归档目录（Git 忽略）
├── data/aggregation/         # 批次状态和生成结果（Git 忽略）
├── logs/                     # 本地日志（Git 忽略）
├── tests/                    # 不访问真实飞书的 pytest 测试
├── docs/plans/               # 功能计划和执行记录
├── ARCHITECTURE.md           # 架构地图
└── VersionLog.md             # 实质变更日志
```

## 安装与运行

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe feishu_bot_listener.py
```

## 配置

在本地 `.env` 中填写（不要提交该文件）：

```dotenv
FEISHU_APP_ID=
FEISHU_APP_SECRET=

FEISHU_INBOX_DIR=./data/inbox
FEISHU_ARCHIVE_DIR=./data/archive
FEISHU_AGGREGATION_DIR=./data/aggregation
FEISHU_SALES_TEMPLATE_PATH=./excel_file_example/汇总效果-合并版-2026年销售数据统计2.xlsx
FEISHU_CACHE_ADMIN_OPEN_IDS=ou_your_open_id
FEISHU_MAX_DOWNLOAD_BYTES=104857600
LOG_LEVEL=INFO
```

| 配置项 | 说明 |
| --- | --- |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 企业自建应用凭据。 |
| `FEISHU_INBOX_DIR` | 直接 Excel 附件的收件目录，默认 `data/inbox/`。 |
| `FEISHU_ARCHIVE_DIR` | Sheets/Wiki 链接导出目录，默认 `data/archive/`。 |
| `FEISHU_AGGREGATION_DIR` | 待汇总批次状态和汇总结果目录，默认 `data/aggregation/`。 |
| `FEISHU_SALES_TEMPLATE_PATH` | 必填的销售汇总模板 `.xlsx` 路径。 |
| `FEISHU_CACHE_ADMIN_OPEN_IDS` | 可执行全局缓存清理的飞书用户 `open_id`；多个值用英文逗号分隔。未配置时命令禁用。 |
| `FEISHU_MAX_DOWNLOAD_BYTES` | 单个附件或导出文件的本地上限，默认且最大为 100 MB。 |
| `LOG_LEVEL` | `DEBUG`、`INFO`、`WARNING`、`ERROR` 或 `CRITICAL`。 |

## 消息准入与运行约束

- 私聊和群聊均允许使用，但群聊消息必须直接 `@` 当前机器人；只 `@所有人` 不会触发处理。
- 一条消息如果包含可下载的 Excel 附件，会优先走附件分支；仅在没有附件时识别文本中的第一个受支持 Sheets/Wiki 链接。
- 当前进程通过异步锁串行执行附件下载和表格导出，避免多个文件任务同时写入本地。
- 批次按聊天 ID 和发送人 ID 隔离；同一个飞书来源文件 ID 不会被重复加入批次，文件内容相同但来源不同的记录不会去重。
- 启动后会持有 `logs/feishu_bot_listener.lock`。如果已有实例正在运行，新实例会立即报告启动失败；正常退出后锁会释放，锁文件本身可以保留。
- 运行日志写入 `logs/feishu_bot_listener.log`。Lark SDK 和 HTTP 库的 URL 型 INFO 日志被抑制，已知 WebSocket 临时凭证会被替换为 `***`。

## 使用方式与归档规则

直接发送 Excel 附件时，文件保存到：

```text
data/inbox/<message_id>__<安全文件名>
```

发送以下任一链接时，机器人会创建 XLSX 导出任务：

```text
https://xxx.feishu.cn/sheets/shtxxxxxxxx
https://xxx.feishu.cn/wiki/wikixxxxxxxxx
```

普通 Sheets 链接的 `<spreadsheet_token>` 直接作为导出 Token，类型为 `sheet`。Wiki 链接先调用 Wiki 节点查询接口，读取 `data.node.obj_type`、`data.node.obj_token` 和 `data.node.title`：

- `obj_type=sheet`：以 `sheet` 导出；
- `obj_type=bitable`：以 `bitable` 导出；
- 其他类型：不下载，并回复“当前链接不是可导出的销售表格”。

链接导出保存到：

```text
data/archive/YYYY-MM/sender_open_id/
SUB-YYYYMMDD-HHMMSS-messageid后8位_文档标题.xlsx
```

文件标题优先使用 Wiki 节点返回的 `title`；标题为空时，使用导出任务返回的文件名。归档目录和下载内容均被 Git 忽略，只有 `.gitkeep` 用于保留目录结构。

## 销售汇总流程

1. 依次发送或导出 `.xlsx` 销售文件。单工作表时直接作为签约表；多工作表时按 `签约情况`、`签约数据`、包含“签约+情况”、包含“签约+数据”的优先级选表。选中的表通过 A:T 结构校验后按上传顺序加入批次。
2. 发送 `汇总状态` 查看批次；发送 `清空汇总` 可清除当前批次，但不会删除已下载的源文件。
3. 发送 `汇总`。机器人以 `FEISHU_SALES_TEMPLATE_PATH` 为模板，只清空并重建签约目标表的明细、统计行、合并单元格和公式，然后把结果 Excel 发回会话。回款表和其他模板工作表原样保留。

生成成功且文件发送成功后，当前批次才会清空；发生校验、生成或发送错误时会保留批次，便于修正后重试。详细字段规则以 [`excel_file_example/2026年销售数据汇总SOP.md`](excel_file_example/2026年销售数据汇总SOP.md) 为准。

## 清理本地下载缓存

先在 `.env` 的 `FEISHU_CACHE_ADMIN_OPEN_IDS` 填入管理员飞书 `open_id`，重启机器人后，由管理员发送：

```text
清空下载缓存
```

机器人会串行清理 `data/inbox/`、`data/archive/` 和 `data/aggregation/output/` 中的非活动文件，并回复删除文件数、释放空间、保留的活动文件数和失败数。以下内容不会删除：

- 任意用户当前待汇总批次仍引用的源文件；
- 汇总模板；
- `data/aggregation/state/` 批次状态；
- 各缓存目录中的 `.gitkeep`。

`清空汇总` 只会移除当前用户的批次引用，不删除本地文件；之后再由管理员发送 `清空下载缓存`，相应源文件才会成为可清理文件。非管理员执行该命令不会触发扫描或删除。

## 飞书权限与节点授权

除机器人长连接和消息资源下载所需权限外，表格链接导出至少需要申请：

```text
wiki:node:retrieve
drive:export:readonly
```

如应用采用更完整的知识库只读权限，也可以申请：

```text
wiki:wiki:readonly
```

开放平台接口权限和具体知识库节点权限是两层权限。即使应用已经申请 Wiki API 权限，如果没有被授权访问具体知识库节点，仍然无法解析和导出。发生该情形时，机器人会提示将应用加入对应知识库或文档，并确认节点阅读与云文档导出权限后重试。

## 测试

```powershell
.venv\Scripts\python.exe -m pytest
```

测试全部使用 fake/mock，不访问真实飞书、不使用真实凭据，也不会改动真实下载文件。

当前完整基线：**74 项测试通过**。此外，汇总结果会通过 Microsoft Excel 兼容重开和可视检查验证签约公式与模板布局。

## 工程协作与文档入口

- [架构地图](ARCHITECTURE.md)：模块边界、依赖方向和两条数据流。
- [功能计划模板](docs/templates/feature-plan-template.md)：新增功能先写范围、决策、原子 Checklist 和验收。
- [功能计划目录说明](docs/plans/README.md)：计划的命名、状态和归档规则。
- [本次表格链接导出计划](docs/plans/2026-07-14_feishu-table-link-export_plan.md)：已完成的实现记录与验收证据。
- [监听器运行安全计划](docs/plans/2026-07-15_listener-runtime-safety_plan.md)：群聊准入、日志脱敏和单实例约束的验收记录。
- [销售工作簿自动汇总计划](docs/plans/2026-07-15_sales-workbook-aggregation_plan.md)：本次 Excel 汇总实现和验收证据。
- [下载缓存安全清理计划](docs/plans/2026-07-16_download-cache-cleanup_plan.md)：管理员权限、活动文件保护和清理范围的验收记录。
- [版本日志](VersionLog.md)：业务、配置和工程规则的实质变更。
- [Cursor 规则](.cursor/rules/project-workflow.mdc)：Plan/Agent 工作流；[`.cursorrules`](.cursorrules) 提供旧版兼容入口。

目录级职责见 [`clients/README.md`](clients/README.md)、[`services/README.md`](services/README.md)、[`config/README.md`](config/README.md)、[`data/README.md`](data/README.md)、[`tests/README.md`](tests/README.md) 和 [`logs/README.md`](logs/README.md)。
