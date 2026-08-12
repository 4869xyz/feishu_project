# 管理员私聊上传 YAML/模板热生效

**状态：** 已完成  
**创建日期：** 2026-08-12  
**负责人：** Agent

## 1. 背景与目标

- 业务背景：服务器部署后不便改代码；人员与模板需通过飞书私聊维护。
- 目标：管理员私聊上传合法 YAML / 正式模板后，校验通过即备份并热生效。
- 成功标准：失败不覆盖线上文件；成员附件流程不变；管理员异名 DOCX 仍可作本周提交。

## 2. 范围

**包含：**

- 管理员上传 `.yaml`/`.yml` 更新人员配置
- 管理员上传与正式模板同名的 `.docx` 更新模板
- 备份、原子替换、`校验配置` 指令、文档与测试

**不包含：**

- 飞书逐字段改人员、人员入库、周日自动生成、`.env` 热更新、Windows 服务安装脚本

## 3. 影响分析与技术决策

- 受影响模块：`config_update`、`service`、`listener`、`attachments`、`document.validate_template`
- 已确定的技术决策：
  - 校验通过后备份至 `data/meeting_minutes/config_backups/`（保留 20 份）再原子覆盖
  - 模板识别：文件名等于 `MEETING_BOT_TEMPLATE_PATH` basename
  - 配置应用使用 `_config_lock`；管理员配置附件跳过提交身份拒绝与 OCR

## 4. 预计修改文件

| 路径 | 操作 | 原因 |
| --- | --- | --- |
| `meeting_minutes_bot/config_update.py` | 新增 | 备份与应用编排 |
| `meeting_minutes_bot/service.py` | 修改 | 上传入口与校验指令 |
| `meeting_minutes_bot/listener.py` | 修改 | 配置附件分流 |
| `meeting_minutes_bot/attachments.py` | 修改 | 管理员 YAML 后缀 |
| `meeting_minutes_bot/document.py` | 修改 | 候选模板路径校验 |
| `tests/meeting_minutes/test_config_upload.py` | 新增 | 覆盖成功/回滚/权限/分流 |
| 文档与 `VersionLog.md` | 修改 | SOP 与架构 |

## 5. 原子 Checklist

- [x] 新增 `config_update`：备份、原子替换、YAML/模板校验应用
- [x] `service`：配置上传入口 + `校验配置`；失败不落盘
- [x] `listener`/`attachments`：管理员 YAML 与「模板同名 DOCX」分流
- [x] 测试覆盖成功生效、坏文件回滚、非管理员拒绝、管理员非模板名 DOCX 仍提交
- [x] 更新管理员说明（含服务器稳定运行）、架构图、`VersionLog.md` 与计划状态

## 6. 测试与验收

- [x] 自动化：`tests/meeting_minutes` 71 passed, 1 skipped
- [x] 异常与回滚：坏 YAML / 缺占位符模板不覆盖线上文件
- [ ] 手动验证：服务器不重启，私聊上传后立刻 `校验配置` / `生成本周纪要`

## 7. 执行记录

| 日期 | 状态 | 记录 |
| --- | --- | --- |
| 2026-08-12 | 草拟 | 确认上传即生效 + 模板同名识别 |
| 2026-08-12 | 已完成 | 实现热上传、备份、测试与文档 |
