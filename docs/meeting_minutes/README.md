# 周例会纪要机器人

该机器人是仓库中的第二个独立飞书长连接进程。它处理私聊文字、图片、文字型 PDF、DOCX 和 Markdown 纪要，不读取销售机器人的 `.env`、数据目录、日志或进程锁。

## 首次配置

1. 在飞书开放平台创建新的企业自建应用，启用机器人和长连接消息事件，并授予接收消息、发送消息、获取用户 ID 及获取与上传图片或文件所需权限。
2. 复制 `.env.meeting-minutes.example` 为 `.env.meeting-minutes`，填写新应用的 App ID 和 App Secret。
3. 复制 `meeting_minutes_bot/config/people.example.yaml` 为 `meeting_minutes_bot/config/people.yaml`。
4. 根据新应用收到的事件补齐所有员工和管理员的 `open_id`。不要复用旧应用中记录的 `open_id`。
5. 将正式模板放到配置的 `MEETING_BOT_TEMPLATE_PATH`。模板必须包含每个启用人员对应的 Jinja 占位符，例如 `{{ yang_yilin }}`。
6. 安装依赖后执行：

```powershell
python -m meeting_minutes_bot
```

原销售机器人仍通过 `python feishu_bot_listener.py` 启动。两个命令使用不同飞书凭据、日志、锁文件和数据目录，可以同时运行。

## 命令

- 直接发送非空文字：追加本周纪要。
- 直接发送图片：使用本地 RapidOCR 识别中英文并追加。
- 直接发送 `.pdf`：提取每页文字层并追加；不支持扫描版或含图片型页面的 PDF。
- 直接发送 `.docx`：提取正文段落和表格并追加；不识别内嵌图片。
- 直接发送 `.md` 或 `.markdown`：转换为可读纯文本并追加。
- `替换：新的完整内容`：替换本人本周有效纪要，历史记录保留。
- `查看我的纪要`：查看本人本周有效内容。
- `撤回本周提交`：撤回本人本周全部有效内容。
- `生成本周纪要`：管理员生成带版本号和时间戳的 DOCX。
- `查看本周提交状态`：管理员查看已提交与未提交名单。

同一 `message_id` 只会执行一次。周期使用 `Asia/Shanghai` 时区的 ISO 周，周一 00:00 切换。

## 附件限制与缓存

- 支持的独立图片文件：`.png`、`.jpg`、`.jpeg`、`.webp`、`.bmp`、`.tif`、`.tiff`。
- 单个附件默认最大 20 MB，图片默认最大 4000 万像素，PDF 默认最多 50 页。
- 任一 PDF 页面缺少文字层时，整份 PDF 都不会入库，机器人会提示改发文字型 PDF、DOCX、Markdown 或单独图片。
- 识别文本超过 `MEETING_BOT_MAX_TEXT_LENGTH` 时整条拒绝，不会静默截断。
- 原始附件缓存在 `data/meeting_minutes/attachments/`，默认保留 14 天，缓存总量默认不超过 512 MB。
- 附件始终按“追加”处理；需要替换时请继续使用文字指令 `替换：...`。

可选环境变量及默认值：

```dotenv
MEETING_BOT_MAX_ATTACHMENT_BYTES=20971520
MEETING_BOT_MAX_PDF_PAGES=50
MEETING_BOT_RETENTION_DAYS=14
MEETING_BOT_ATTACHMENT_CACHE_MAX_BYTES=536870912
```

## 数据保留与自动清理

- 原始附件、数据库中的提交正文与处理记录、生成的 Word 纪要统一保留 14 天。
- 机器人每次启动时立即清理一次，持续运行时每 24 小时再清理一次。
- 超过保留期的所有 Word 版本都会删除，不永久保留每周最新版。
- 日志按天轮转并保留最近 14 份；单项清理失败不会导致机器人停止，下一周期会继续重试。
- `MEETING_BOT_RETENTION_DAYS` 是统一保留期配置。旧的 `MEETING_BOT_ATTACHMENT_CACHE_TTL_SECONDS` 仍可在未设置统一保留期时单独控制附件缓存，建议新配置统一使用天数。

## 当前范围

当前仍不处理群聊、旧版 `.doc`、扫描版 PDF、DOCX 内嵌图片、自动提醒、大模型整理、在线文档或 Windows 便携包。正式模板缺失、缺少启用人员占位符或本地 OCR 无法初始化时，机器人会拒绝启动并给出明确错误。
