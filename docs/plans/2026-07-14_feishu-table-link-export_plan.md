# 飞书表格链接导出实施计划

**状态：** 已完成  
**创建日期：** 2026-07-14  
**执行日期：** 2026-07-14

## 1. 背景与目标

机器人原本只能下载聊天中直接上传的 Excel 附件。新增对普通 Sheets 链接和 Wiki 链接的识别、解析与 XLSX 导出，使销售表格能按发送人和日期归档到本地。

成功标准：

- `/sheets/<token>` 直接以 `sheet` 类型导出；
- `/wiki/<token>` 先解析 Wiki 节点，仅在真实对象为 `sheet` 或 `bitable` 时导出；
- 查询参数和片段不会混入 Token；
- Wiki 权限不足时给出明确的两层权限说明；
- 归档遵循 `data/archive/YYYY-MM/sender_open_id/SUB-YYYYMMDD-HHMMSS-messageid后8位_文档标题.xlsx`；
- 全部离线测试通过。

## 2. 范围

**包含：** 飞书链接解析、Wiki 节点查询、导出任务创建/轮询/下载、归档目录配置、监听器回复、权限说明和离线测试。

**不包含：** Excel 内容解析、数据清洗、自动导入、Wiki 写入、卡片消息链接解析或 CI 配置。

## 3. 技术决策

- 仅接受 `*.feishu.cn` 域名中的 `/sheets/` 与 `/wiki/` 路径；使用 URL 解析而非字符串切片，天然排除 `?` 与 `#` 之后内容。
- Wiki Token 只用于 `GET /wiki/v2/spaces/get_node`；以响应中的 `obj_type` 和 `obj_token` 创建导出任务，禁止把 Wiki Token 当作表格 Token。
- 统一使用 `POST /drive/v1/export_tasks`，请求体固定为 `file_extension=xlsx`、真实 Token 和 `sheet`/`bitable` 类型；随后轮询任务并原子下载结果。
- Wiki 节点标题优先；为空时回退到导出任务的文件名。所有用户输入都会清理为 Windows 安全文件名。
- `403`、已知权限错误码和导出任务的权限错误都映射为专用 Wiki 权限提示；其他 Wiki 类型不发起下载。

## 4. 影响文件

| 路径 | 操作 | 原因 |
| --- | --- | --- |
| `clients/feishu_client.py` | 修改 | 增加 Wiki 查询、导出任务和导出文件下载 API。 |
| `clients/feishu_table_export.py` | 新增 | 负责链接解析、Wiki 分流与归档命名。 |
| `feishu_bot_listener.py` | 修改 | 编排附件下载与表格链接导出，并回复用户。 |
| `config/settings.py`、`.env.example` | 修改 | 增加归档目录配置。 |
| `.gitignore`、`data/archive/.gitkeep` | 修改/新增 | 保留目录结构但忽略导出产物。 |
| `tests/` | 修改/新增 | 覆盖 API、链接解析、分流、命名和权限回复。 |
| `README.md`、`ARCHITECTURE.md`、目录 README、`VersionLog.md` | 修改 | 同步使用说明、权限与架构。 |

## 5. 原子 Checklist

- [x] 增加归档目录配置与 Git 忽略规则。
- [x] 实现 Sheets/Wiki 链接提取，排除查询参数与片段。
- [x] 实现 Wiki 节点解析和 `sheet`/`bitable` 类型分流。
- [x] 实现 XLSX 导出任务创建、轮询和 `.part` 原子下载。
- [x] 实现按发送人、消息 ID 和标题归档的命名规则。
- [x] 将链接能力接入监听器，并实现非表格与 Wiki 权限回复。
- [x] 补充离线测试并运行完整 pytest。
- [x] 更新架构、README、目录说明和版本日志。

## 6. 测试与验收

- 自动化：`.venv\Scripts\python.exe -m pytest`
- 结果：2026-07-14 通过 **46** 项测试。
- 覆盖：普通 Sheets、Wiki `sheet`、Wiki `bitable`、其他 Wiki 类型、Query/Fragment Token 截断、标题优先级、归档命名、Wiki 节点/导出权限异常和监听器指定回复。
- 上线前手工验证：在已授权的飞书测试群分别发送一条 Sheets 链接和一条 Wiki 表格链接，并确认应用同时具备开放平台权限与具体节点访问权。

## 7. 执行记录

| 日期 | 状态 | 记录 |
| --- | --- | --- |
| 2026-07-14 | 已完成 | 实现表格链接导出、归档、权限提示和文档同步；离线测试 46 项通过。 |
