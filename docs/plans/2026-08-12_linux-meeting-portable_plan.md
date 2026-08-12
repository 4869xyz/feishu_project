# Ubuntu 24.04 纪要机器人便携发布包

**状态：** 已完成  
**创建日期：** 2026-08-12  
**负责人：** Agent

## 1. 背景与目标

- 业务背景：云服务器为 Ubuntu 24.04，需要与 Windows 便携包同级的免系统 Python 交付物。
- 目标：在 Linux 上用 PyInstaller 构建 onedir 包，附启停脚本与 systemd 示例。
- 成功标准：解压后可启动；文档写明必须在 Linux 构建、单实例与密钥安全。

## 2. 范围

**包含：** `packaging/linux/meeting/` 全套脚本与文档更新。  
**不包含：** Windows 交叉编译、Docker、销售机器人 Linux 包。

## 3. 技术决策

- 构建必须在 Linux x64（Ubuntu 24.04）执行。
- 产物：`release/周例会纪要机器人-Linux-x64.tar.gz`。
- 启停：nohup + `meeting_minutes_bot.pid`；生产用 systemd `Restart=always`。

## 4. 预计修改文件

| 路径 | 操作 |
| --- | --- |
| `packaging/linux/meeting/*` | 新增 |
| `docs/meeting_minutes/Linux便携发布包构建与交付.md` | 新增 |
| 管理员说明 / ARCHITECTURE / VersionLog | 修改 |

## 5. 原子 Checklist

- [x] Linux spec 与 `build_meeting_portable.sh`
- [x] 启停/日志 shell 与 systemd 示例
- [x] 交付文档与架构/版本日志/计划

## 6. 测试与验收

- [ ] 在 Ubuntu 24.04 上执行构建并启动（需在目标机完成）
- [x] 仓库内脚本与文档已落地；Windows 开发机无法生成 ELF

## 7. 执行记录

| 日期 | 状态 | 记录 |
| --- | --- | --- |
| 2026-08-12 | 已完成 | 落地 packaging/linux/meeting 与文档 |
