# 项目架构地图

## 项目定位

这是一个 Python 3.11 双飞书机器人仓库：原销售机器人接收 Excel 附件或 Sheets/Wiki 链接并生成销售汇总；独立的周例会纪要机器人接收员工私聊文字，以 SQLite 保存可追溯提交，并由管理员生成 DOCX。两者使用不同飞书应用、配置、入口、锁、日志和数据目录。

## 模块边界与依赖方向

| 模块 | 职责 | 允许依赖 |
| --- | --- | --- |
| `config` | 读取 `.env`、校验配置、创建日志/收件/归档/汇总目录。 | 标准库、`python-dotenv` |
| `clients.feishu_client` | tenant token 缓存、飞书 HTTP 请求、Wiki 查询、导出任务和二进制下载。 | `config`、`requests`、标准库 |
| `clients.feishu_attachment` | 解析文件消息、校验 Excel 后缀、生成收件箱安全文件名。 | 标准库、消息资源下载协议 |
| `clients.feishu_table_export` | 识别表格链接、解析 Wiki 真实对象，并导出到消息归档或调用方指定的 latest 路径。 | `feishu_client` 的导出协议、标准库 |
| `services.sales_workbook_aggregator` | 按名称优先级选择并校验签约工作表，重建签约目标表、保留明细源字体颜色、按 C:G 暖色标记隐藏明细、校验控制总额并原子保存。 | `openpyxl`、标准库 |
| `services.aggregation_batch_store` | 按聊天和发送人持久化临时批次、固定云表、latest 缓存和输出路径，并迁移 v1 状态。 | 标准库 |
| `services.download_cache` | 只在配置的缓存根目录内删除非活动文件，并保护活动批次和显式保护路径。 | 标准库 |
| `feishu_bot_listener.py` | 配置消息准入、日志和单实例锁，创建长连接、串行编排并把结果或文件转换为飞书回复。 | `config`、`clients`、`services`、`lark-channel-sdk` |
| `meeting_minutes_bot` | 独立读取 `.env.meeting-minutes` 和人员 YAML，处理私聊文字、幂等入库、权限命令与版本化 DOCX 生成。 | `lark-channel-sdk`、SQLAlchemy、aiosqlite、PyYAML、docxtpl、标准库 |
| `packaging/windows` | 使用 PyInstaller 生成免 Python 的 Windows x64 便携程序，并提供中文启动、停止和日志入口。 | 项目入口、当前 `.env`、汇总模板、PowerShell |
| `tests` | 使用 fake/mock 验证配置、API 参数、解析、归档和回复。 | 被测模块、`pytest` |

依赖必须单向：`config` 不依赖 `clients`/`services`；`clients` 和 `services` 不依赖监听器；监听器只编排，不放入 HTTP、链接解析、汇总算法或文件命名细节。

`meeting_minutes_bot` 是完整命名空间，不导入销售监听器或销售业务服务；只有测试会同时获取两个锁文件名以验证隔离。

## 运行数据流

Windows 便携交付先在开发机执行 `packaging/windows/build_portable.ps1`，通过测试后把入口及运行依赖冻结到 `release/`。发布包外置复制当前 `.env` 和汇总模板；目标电脑双击启动脚本后，以 `FeishuSalesBot.exe` 所在目录作为项目根目录进入下述相同数据流。

```text
.env
  -> config.settings.load_settings()
  -> 配置凭据安全日志
  -> 持有 logs/feishu_bot_listener.lock（单实例）
  -> FeishuChannel 长连接
       -> 私聊开放
       -> 群聊开放，但必须直接 @机器人；@所有人不触发
  -> feishu_bot_listener.handle_message()（文件任务串行执行）
       ├─ 文件附件分支
       │   -> ExcelAttachmentDownloader
       │   -> FeishuClient.download_message_resource()
       │   -> data/inbox/<message_id>__<safe_filename>
       └─ 表格链接分支
           -> FeishuTableLinkExporter 识别 /sheets/ 或 /wiki/
           -> Sheets：真实 Token + type=sheet
           -> Wiki：GET /wiki/v2/spaces/get_node?token=<wiki_token>
                    -> obj_type=sheet|bitable + obj_token
           -> POST /drive/v1/export_tasks (file_extension=xlsx)
           -> 轮询导出任务并下载 file_token
           -> data/archive/YYYY-MM/sender_open_id/SUB-..._<title>.xlsx
       -> services.sales_workbook_aggregator.validate_source_workbook()
       -> services.aggregation_batch_store（chat_id + sender_open_id 隔离）
       -> 固定来源命令：添加云表 <链接1> <链接2> ... | 云表列表 | 移除云表 <数字编号>
                           | 云表排序 <完整编号顺序> | 清空云表
             -> 多链接按消息顺序解析、去重、逐条导出校验；单项失败不回滚其他成功项
            -> data/aggregation/registered/<owner>/<cloud-id>/latest.xlsx
       -> 用户命令：汇总状态 | 清空汇总 | 汇总
       -> 汇总时按登记顺序重新解析/导出/校验固定云表
            -> 任一来源失败：中止，不使用旧 latest 缓存
            -> 固定云表优先，随后追加当前临时批次
       -> aggregate_sales_workbooks()
            -> 只清空模板中的签约目标表
            -> 按上传顺序重建签约明细、统计、合并单元格与公式
            -> 控制总额和公式结构校验
            -> data/aggregation/output/YYYY-MM/<owner-hash>/...xlsx
       -> 飞书文件回复；成功后只清空临时批次，固定来源保留
       └─ 管理员命令：清空下载缓存
           -> 校验 FEISHU_CACHE_ADMIN_OPEN_IDS
           -> 汇总所有批次的活动源文件路径
           -> 清理 data/inbox、data/archive、data/aggregation/output
           -> 保留活动源文件、模板和 .gitkeep
  -> 飞书回复 + logs/feishu_bot_listener.log
```

### 周例会纪要机器人数据流

```text
.env.meeting-minutes + meeting_minutes_bot/config/people.yaml + 正式 DOCX 模板
  -> meeting_minutes_bot.settings.load_settings()
  -> 持有 logs/meeting_minutes/meeting_minutes_bot.lock（与销售锁独立）
  -> 初始化 data/meeting_minutes/meeting_minutes.db
  -> FeishuChannel 长连接（私聊开放、群聊禁用）
       -> open_id 查询人员 YAML，不读取正文姓名
       -> message_id 写入 meeting_events，重复事件不再执行
       -> 普通文字 / 替换：内容
            -> meeting_submissions 保留人员快照、原文、模式、状态和有效标记
       -> 查看我的纪要 / 撤回本周提交
       -> 管理员：查看本周提交状态 / 生成本周纪要
            -> 按 Asia/Shanghai ISO 周查询有效提交
            -> 按 template_key 渲染正式模板
            -> data/meeting_minutes/output/<周期>_v<版本>_<时间戳>.docx
            -> meeting_documents 保存版本、状态和路径
       -> 回复文字或 DOCX
  -> logs/meeting_minutes/meeting_minutes_bot.log
```

## Excel 汇总约束

- 只接收 `.xlsx`；单工作表直接作为签约表，多工作表按 `签约情况`、`签约数据`、模糊名称优先级选表，选中表必须符合 A:T 签约结构。
- 来源 ID 用于防止同一文件重复加入批次；业务明细不按姓名或内容去重，相同内容的不同来源记录会全部保留。
- 固定云表来源 ID 由原始链接类型和 Token 稳定生成并仅供内部去重、刷新和缓存定位；用户看到的编号始终按当前顺序映射为连续的 `1…N`。
- 固定来源按“聊天 + 发送人”隔离；一次添加命令可混合提交多个 Sheets/Wiki 链接，按首次出现顺序去重并独立提交，可提交全部数字编号重排；汇总严格使用持久化顺序，并始终先于临时来源。
- 模板中只重建签约汇总表；回款表及其他工作表的 XML、关系、图片等部件原样保留，回款错误不得阻断签约。
- 签约按来源顺序和首次出现顺序输出明细、个人、小组、部门统计，并使用独立的 `Decimal` 控制总额核对。
- 业务明细 A:T 先应用模板完整样式，再仅覆盖为源单元格字体颜色；组别继承时同步继承组别颜色，汇总行始终使用模板颜色。
- 静态 RGB、主题色和索引字体色统一解析为显示色；C:G 任一非空关键字段为黄色/橙色时仅设置行隐藏，空白字段残留样式及其他列颜色不触发隐藏，数据继续参与个人、小组和部门公式。
- 生成的签约明细及个人/小组/部门汇总金额统一覆盖为无货币符号、1 位小数的会计专用格式；空白明细月份预设相同格式，间隔空行保持模板样式。
- I:T 按每月全部明细金额绝对值上界估算会计格式显示长度，保留模板宽度下限并独立向外扩展，同时持久化 `bestFit` 标记。
- 最后一个个人汇总与小组汇总、最后一个小组汇总与部门汇总之间各使用模板空行样式分隔；人员间和组别间已有空行规则保持不变。
- 输出先写临时文件，重开校验公式、预期隐藏明细、金额格式、I:T 列宽/`bestFit`、其他行可见性和目标表结构后再原子替换正式文件。

## 表格链接导出约束

- URL 解析只从路径段读取 Token，因此 `?query` 和 `#fragment` 不会传给飞书 API。
- Wiki Token 不是电子表格 Token，绝不直接用于导出；必须先取得 `obj_type`、`obj_token` 和 `title`。
- 仅 `sheet` 和 `bitable` 可以请求 XLSX 导出。其他 Wiki 对象在监听器层回复为非可导出销售表格。
- Wiki 节点读取、创建导出任务或下载导出文件发生权限错误时，统一转化为 Wiki 节点授权提示。
- 每条消息只处理第一个受支持的表格链接；导出任务每秒轮询一次，最长等待 90 秒。
- 所有下载先写同目录 `.part` 文件，完成后用原子替换落盘；文件名会清理 Windows 非法字符，并受统一大小上限保护。
- 固定来源先导出到同目录 staging 文件，通过销售工作簿校验后才原子替换 latest；Wiki 原始节点每次刷新重新解析。

## 监听器运行约束

- `PolicyConfig` 显式固定为私聊开放、群聊开放、群聊必须直接提及机器人，并忽略仅 `@所有人` 的触发。
- 监听器进程生命周期内持有 `logs/feishu_bot_listener.lock` 的非阻塞跨平台文件锁；同一项目目录不能同时启动第二个实例。
- 进程内附件下载和链接导出共用一个 `asyncio.Lock`，避免多个阻塞式文件任务并发执行。
- 汇总和全局缓存清理共用同一文件锁；缓存清理只允许 `FEISHU_CACHE_ADMIN_OPEN_IDS` 白名单中的发送人执行，未配置时命令禁用。
- 缓存清理会先读取所有批次状态并保护仍被引用的源文件，只扫描 `data/inbox/`、`data/archive/` 和汇总 `output/`；不扫描或删除批次 `state/`。
- 固定来源登记与 latest 缓存位于汇总目录的 `registered/`，不属于全局缓存清理范围；`清空云表` 清固定登记和对应 latest，`清空汇总` 只清临时批次。
- Lark SDK 日志统一传播到根日志管线；Lark 与 `httpx` 的最低级别固定为 `WARNING`，格式化器继续兜底清理 `access_key`、`ticket`、`access_token` 和 `app_secret` 查询参数。
- 纪要机器人使用 `group_policy="disabled"`，只接收私聊；其锁文件、日志目录、SQLite、DOCX 输出和 `MEETING_BOT_` 环境变量不得与销售机器人复用。

## 运行产物与安全

- `.env`、日志、`logs/*.lock`、`data/inbox/` 实际附件、`data/archive/` 实际导出文件和 `data/aggregation/` 状态/结果都不得提交到 Git。
- `.env.meeting-minutes`、真实人员 YAML、`data/meeting_minutes/` SQLite/DOCX 和 `logs/meeting_minutes/` 同样不得提交；只提交无真实 open_id 的示例配置和测试模板。
- `data/inbox/.gitkeep`、`data/archive/.gitkeep` 和 `data/aggregation/.gitkeep` 仅保存目录结构。
- 日志和异常必须脱敏，不输出 App Secret、完整 tenant token、完整文件内容或业务数据。
- 飞书开放平台 API Scope 与具体 Wiki 节点/文档共享权限是独立条件；两者都满足才能解析和导出 Wiki 表格。

## 文档维护规则

- 新功能从 `docs/templates/feature-plan-template.md` 创建计划，按 `docs/plans/README.md` 维护状态。
- 模块、依赖方向、入口或数据流改变时更新本文档。
- 目录职责变化时更新相应目录 README；业务、配置语义或工程规则的实质变更追加到 `VersionLog.md`。
