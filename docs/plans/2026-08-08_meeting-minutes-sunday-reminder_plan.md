# 周日未提交提醒实施计划

**状态：** 已完成  
**创建日期：** 2026-08-08  
**负责人：** Agent

## 1. 背景与目标

- 业务背景：管理员希望周日催促未交本周周例会纪要的人员。
- 目标：进程内自动在周日 17:00、20:00（`Asia/Shanghai`）私聊未提交人员。
- 成功标准：准时触发；只提醒 enabled 且本周无有效提交者；重启不重复发；单人失败不影响其他人；离线测试可验证调度与幂等。

## 2. 范围

**包含：**

- 周日 17:00 首轮、20:00 对仍未提交者再提醒
- SQLite `meeting_reminder_runs` 幂等与宕机补发
- `MEETING_BOT_REMINDER_ENABLED` 开关（默认 `true`）
- 测试与文档同步

**不包含：**

- 管理员汇总、自定义工作日/时刻 UI、群聊提醒、卡片消息、外部 cron

## 3. 影响分析与技术决策

- 受影响模块：`meeting_minutes_bot` 模型、仓库、新 `reminder.py`、settings、入口、保留期清理、文档
- 依赖/权限：飞书主动私聊；用户通常需先与机器人有过会话
- 已确定的技术决策：
  - 提醒对象为全部 `enabled=true` 人员
  - 仅私聊未提交者，不发管理员汇总
  - asyncio 后台任务，不引入 APScheduler
  - `COMPLETED` 幂等；`PROCESSING` 超过 10 分钟可重试

## 4. 预计修改文件

| 路径 | 操作 | 原因 |
| --- | --- | --- |
| `meeting_minutes_bot/models.py` | 修改 | `MeetingReminderRun` |
| `meeting_minutes_bot/repository.py` | 修改 | claim/finish 与清理 |
| `meeting_minutes_bot/reminder.py` | 新增 | 调度与发送 |
| `meeting_minutes_bot/settings.py` | 修改 | `reminder_enabled` |
| `meeting_minutes_bot/__main__.py` | 修改 | 挂载后台任务 |
| `tests/meeting_minutes/test_reminder.py` | 新增 | 离线覆盖 |
| 文档与 `VersionLog.md` | 修改 | 同步说明 |

## 5. 原子 Checklist

- [x] 新增提醒模型与 repository claim/finish API
- [x] 实现 `ReminderScheduler`
- [x] settings 开关 + `__main__` 挂载
- [x] 离线测试
- [x] 更新相关文档与 `VersionLog.md`

## 6. 测试与验收

- [x] 自动化测试：`tests/meeting_minutes` 50 passed, 1 skipped
- [x] 异常与回滚：`MEETING_BOT_REMINDER_ENABLED=false` 可关闭

## 7. 执行记录

| 日期 | 状态 | 记录 |
| --- | --- | --- |
| 2026-08-08 | 草拟 | 确认范围与送达方式 |
| 2026-08-08 | 已完成 | 实现周日双槽提醒、幂等补发与文档同步 |
