# 周例会纪要机器人

该机器人是仓库中的第二个独立飞书长连接进程。它处理私聊文字、图片、文字型 PDF、DOCX 和 Markdown 纪要，不读取销售机器人的 `.env`、数据目录、日志或进程锁。

## 首次配置

1. 在飞书开放平台创建新的企业自建应用，启用机器人和长连接消息事件，并授予接收消息、发送消息、获取用户 ID 及获取与上传图片或文件所需权限。
2. 复制 `.env.meeting-minutes.example` 为 `.env.meeting-minutes`，填写新应用的 App ID 和 App Secret。
3. 复制 `meeting_minutes_bot/config/people.example.yaml` 为 `meeting_minutes_bot/config/people.yaml`。
4. 根据新应用收到的事件补齐所有员工和管理员的 `open_id`。不要复用旧应用中记录的 `open_id`；同时让每个启用人员的 `template_key` 与正式 Word 模板占位符一致。
5. 将正式模板放到配置的 `MEETING_BOT_TEMPLATE_PATH`。模板必须包含每个启用人员对应的 Jinja 占位符，例如人员的 `template_key` 为 `employee_1`，模板中就要包含 `{{ employee_1 }}`。
6. 安装依赖后执行：

```powershell
python -m pip install -r requirements.txt
.venv\Scripts\python.exe -m meeting_minutes_bot
```

原销售机器人仍通过 `python feishu_bot_listener.py` 启动。两个命令使用不同飞书凭据、日志、锁文件和数据目录，可以同时运行。

需要把机器人交给没有 Python 环境的同事时，见[便携发布包构建与交付](便携发布包构建与交付.md)（Windows）或 [Linux 便携发布包构建与交付](Linux便携发布包构建与交付.md)。

固定 Ubuntu 服务器长期运行（源码 + venv + systemd，含升级与 60 秒/5 次启动限流）见 [Ubuntu 源码 systemd 部署与升级](Ubuntu源码systemd部署与升级.md)。管理员运维摘要见[管理员使用说明](管理员使用说明.md)。

`.env.meeting-minutes` 和 `meeting_minutes_bot/config/people.yaml` 均含敏感信息，已由 Git 忽略；只提交不含真实凭据和 `open_id` 的示例文件。

## 配置

必填项：

| 配置项 | 说明 |
| --- | --- |
| `MEETING_BOT_FEISHU_APP_ID` | 纪要机器人独立飞书应用的 App ID。 |
| `MEETING_BOT_FEISHU_APP_SECRET` | 纪要机器人独立飞书应用的 App Secret。 |
| `MEETING_BOT_PEOPLE_CONFIG_PATH` | 人员 YAML 路径；相对路径以项目根目录为准。 |
| `MEETING_BOT_TEMPLATE_PATH` | 正式 DOCX 模板路径；相对路径以项目根目录为准。 |

其余配置均有默认值：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `MEETING_BOT_DATABASE_URL` | `sqlite+aiosqlite:///./data/meeting_minutes/meeting_minutes.db` | 当前只支持 `sqlite+aiosqlite:///` 地址。 |
| `MEETING_BOT_DATA_DIR` | `./data/meeting_minutes` | 数据根目录；附件缓存与 DOCX 输出位于其下。 |
| `MEETING_BOT_LOG_DIR` | `./logs/meeting_minutes` | 日志和单实例锁目录。 |
| `MEETING_BOT_TIMEZONE` | `Asia/Shanghai` | ISO 周计算时区。 |
| `MEETING_BOT_MAX_TEXT_LENGTH` | `20000` | 单次文字或附件识别结果的字符上限。 |
| `MEETING_BOT_MAX_ATTACHMENT_BYTES` | `20971520` | 单附件上限，最大允许配置为 100 MB。 |
| `MEETING_BOT_MAX_PDF_PAGES` | `50` | 单个 PDF 最大页数。 |
| `MEETING_BOT_RETENTION_DAYS` | `14` | 附件、数据库记录、DOCX 和轮转日志的统一保留天数。 |
| `MEETING_BOT_ATTACHMENT_CACHE_MAX_BYTES` | `536870912` | 附件缓存总容量，不能小于单附件上限。 |
| `MEETING_BOT_REMINDER_ENABLED` | `true` | 是否启用周日未提交提醒；设为 `false` 可关闭。 |
| `MEETING_BOT_LOG_LEVEL` | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR` 或 `CRITICAL`。 |

人员 YAML 的 `people` 键以飞书 `open_id` 为索引。`name`、`department`、`template_key`、正整数 `section_order` 和 `sort_order` 为必填字段；`enabled` 默认为 `true`，`role` 可为 `employee` 或 `admin`。真正控制管理命令权限的是顶层 `admins` 列表，因此管理员的 `open_id` 还必须列在 `admins` 中。

## 人员变更维护（离职 / 新人）

人员 YAML 是唯一配置源。管理员直接编辑 `people.yaml`，然后私聊机器人发送 `重载人员配置` 即可免重启生效：

| 场景 | 怎么改 | 是否改 Word 模板 |
| --- | --- | --- |
| 离职 | 将该人员的 `enabled` 改为 `false`（保留条目和 `template_key`，便于新人复用） | 否 |
| 新人顶替旧岗位 | 复用原槽位：改该条目的 `open_id`、`name`（如需再调部门/排序），确保 `enabled: true` | 否（推荐方式） |
| 全新岗位/新段落 | 新增 `people` 条目并使用新的 `template_key`，同时在正式模板中加入对应 `{{ template_key }}` 占位符 | 是 |
| 管理员变更 | 维护顶层 `admins` 列表（本项目管理员基本固定） | 否 |

注意事项：

- `open_id` 必须来自当前纪要应用收到的事件（可从日志或数据库获取），不能复用其他飞书应用的值。
- 重载前机器人会重新校验 YAML 结构和启用人员的模板占位符；任一校验失败都不会替换名单，机器人会回复具体错误，原配置继续生效。
- 重载即时影响提交权限、周日提醒名单和 DOCX 生成；已入库的历史提交不受影响。

## 命令

- 直接发送非空文字：追加本周纪要。
- 直接发送图片：使用本地 RapidOCR 识别中英文并追加。
- 直接发送 `.pdf`：提取每页文字层并追加；不支持扫描版或含图片型页面的 PDF。
- 直接发送 `.docx`：正文、表格与内嵌图片会持久保存；生成周报时原样嵌入对应人员段落。查看指令仍显示文字摘要。
- 直接发送 `.md` 或 `.markdown`：转换为可读纯文本并追加。
- `替换：新的完整内容`：替换本人本周有效纪要，历史记录保留。
- `查看我的纪要`：查看本人本周有效内容。
- `撤回本周提交`：撤回本人本周全部有效内容。
- `生成本周纪要`：管理员生成带版本号和时间戳的 DOCX。
- `查看本周提交状态`：管理员查看已提交与未提交名单。
- `重载人员配置`：管理员修改 `people.yaml` 后免重启热生效；校验失败时保留原名单并回复原因。

同一 `message_id` 只会执行一次。周期使用 `Asia/Shanghai` 时区的 ISO 周，周一 00:00 切换。

机器人仅接受私聊，群聊消息不会进入业务处理。发送者必须存在于人员 YAML 且处于启用状态；机器人不从消息正文猜测姓名。

## 周日未提交提醒

机器人在进程内按 `MEETING_BOT_TIMEZONE`（默认 `Asia/Shanghai`）自动催促本周尚未有效提交的启用人员：

- 每周日 17:00：向所有 `enabled=true` 且本周无有效提交的人员私聊首轮提醒（含未提交的管理员）。
- 每周日 20:00：再次查询未提交名单，仅向仍未提交者私聊二次提醒。
- 不向管理员额外发送未提交名单汇总。
- 每轮提醒以 SQLite `meeting_reminder_runs` 按「周期 + 槽位」幂等；完成后不重复发送。若发送中途崩溃，超过约 10 分钟的 `PROCESSING` 记录可重试，且只发给当时仍未提交的人。
- 进程在周日槽位之后启动时，会对当日已到期且尚未完成的轮次补发。
- 可用 `MEETING_BOT_REMINDER_ENABLED=false` 关闭提醒任务。

飞书应用需具备向用户发消息的能力；用户通常需先与机器人建立过私聊，否则个别发送可能失败。单人失败只记入日志和失败计数，不中断整轮。

可选环境变量及默认值：

```dotenv
MEETING_BOT_MAX_ATTACHMENT_BYTES=20971520
MEETING_BOT_MAX_PDF_PAGES=50
MEETING_BOT_RETENTION_DAYS=14
MEETING_BOT_ATTACHMENT_CACHE_MAX_BYTES=536870912
MEETING_BOT_REMINDER_ENABLED=true
```

## 附件限制与缓存

- 支持的独立图片文件：`.png`、`.jpg`、`.jpeg`、`.webp`、`.bmp`、`.tif`、`.tiff`。
- 单个附件默认最大 20 MB，图片默认最大 4000 万像素，PDF 默认最多 50 页。
- 任一 PDF 页面缺少文字层时，整份 PDF 都不会入库，机器人会提示改发文字型 PDF、DOCX、Markdown 或单独图片。
- 识别文本超过 `MEETING_BOT_MAX_TEXT_LENGTH` 时整条拒绝，不会静默截断。
- 原始附件缓存在 `data/meeting_minutes/attachments/`，默认保留 14 天，缓存总量默认不超过 512 MB。
- 附件始终按“追加”处理；需要替换时请继续使用文字指令 `替换：...`。

可选附件相关环境变量见上一节示例。

## 数据保留与自动清理

- 原始附件、提交源 Word（`submission_docs/`）、数据库中的提交正文与处理记录、提醒轮次记录、生成的 Word 纪要统一保留 14 天。
- 机器人每次启动时立即清理一次，持续运行时每 24 小时再清理一次。
- 超过保留期的所有 Word 版本都会删除，不永久保留每周最新版。
- 日志按天轮转并保留最近 14 份；单项清理失败不会导致机器人停止，下一周期会继续重试。
- `MEETING_BOT_RETENTION_DAYS` 是统一保留期配置。旧的 `MEETING_BOT_ATTACHMENT_CACHE_TTL_SECONDS` 仍可在未设置统一保留期时单独控制附件缓存，建议新配置统一使用天数。

运行产物位于：

```text
data/meeting_minutes/meeting_minutes.db   # 事件、提交、提醒轮次和生成版本记录
data/meeting_minutes/attachments/         # 下载的原始附件缓存
data/meeting_minutes/output/              # 管理员生成的版本化 DOCX
logs/meeting_minutes/meeting_minutes_bot.log
logs/meeting_minutes/meeting_minutes_bot.lock
```

锁文件存在不代表进程一定仍在运行；启动程序会实际尝试获取锁。若已有实例持有锁，新实例会直接报错退出，避免同一机器人重复消费消息。

## 验证

提交或部署前运行离线测试：

```powershell
.venv\Scripts\python.exe -m pytest tests\meeting_minutes
```

测试使用 fake/mock，不连接真实飞书，也不读取本地真实凭据。首次上线还应在已授权的测试会话中依次验证文字提交、附件识别、本人查看、管理员状态查询和 DOCX 回传。

## 当前范围

当前仍不处理群聊、旧版 `.doc`、扫描版 PDF、浮动版式的完整还原、大模型整理或在线文档。正式模板缺失、缺少启用人员占位符或本地 OCR 无法初始化时，机器人会拒绝启动并给出明确错误。
