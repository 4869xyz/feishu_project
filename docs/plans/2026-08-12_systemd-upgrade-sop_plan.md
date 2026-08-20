# Ubuntu 源码 systemd 部署与升级 SOP

**状态：** 已完成  
**创建日期：** 2026-08-12  
**负责人：** Agent

## 1. 背景与目标

- 固定 Ubuntu 服务器采用源码 + venv + systemd 运行纪要机器人。
- 文档化升级步骤，并为自动重启增加 60 秒内最多 5 次的安全上限。

## 2. 范围

**包含：** 源码版 systemd 示例、便携包 unit 同步 StartLimit、部署/升级/限流恢复文档。  
**不包含：** 业务代码改动。

## 3. 技术决策

- `Restart=always` + `RestartSec=5`
- `StartLimitIntervalSec=60` + `StartLimitBurst=5`；超限后 `reset-failed` + `start` 由管理员恢复

## 4. Checklist

- [x] `meeting-minutes-bot.source.service.example`
- [x] 便携包 `meeting-minutes-bot.service.example` 同步 StartLimit
- [x] `Ubuntu源码systemd部署与升级.md`
- [x] 管理员说明 / VersionLog / 计划

## 5. 执行记录

| 日期 | 状态 | 记录 |
| --- | --- | --- |
| 2026-08-12 | 已完成 | 落地 unit、SOP 与文档入口 |
