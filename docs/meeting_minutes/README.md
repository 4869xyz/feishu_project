# 周例会纪要机器人

该机器人是仓库中的第二个独立飞书长连接进程。它只处理私聊文字纪要，不读取销售机器人的 `.env`、数据目录、日志或进程锁。

## 首次配置

1. 在飞书开放平台创建新的企业自建应用，启用机器人和长连接消息事件，并授予接收消息、发送消息及发送文件所需权限。
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
- `替换：新的完整内容`：替换本人本周有效纪要，历史记录保留。
- `查看我的纪要`：查看本人本周有效内容。
- `撤回本周提交`：撤回本人本周全部有效内容。
- `生成本周纪要`：管理员生成带版本号和时间戳的 DOCX。
- `查看本周提交状态`：管理员查看已提交与未提交名单。

同一 `message_id` 只会执行一次。周期使用 `Asia/Shanghai` 时区的 ISO 周，周一 00:00 切换。

## 当前范围

首版不处理群聊、DOCX/PDF 输入、OCR、自动提醒、大模型整理、在线文档或 Windows 便携包。正式模板缺失或缺少启用人员占位符时，机器人会拒绝启动并给出明确错误。
