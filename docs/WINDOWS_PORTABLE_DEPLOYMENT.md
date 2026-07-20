# Windows 便携包构建与交付

本方案面向单人使用：目标电脑无需安装 Python，解压后双击“启动机器人.cmd”即可在后台运行。飞书聊天继续作为业务界面，不增加 Web 或桌面前端。

## 生成发布包

在项目根目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\build_portable.ps1
```

脚本会先执行完整测试，再安装固定版本的 PyInstaller，最后生成：

```text
release/飞书销售汇总机器人/
release/飞书销售汇总机器人-Windows-x64.zip
```

发布包会原样复制当前项目的 `.env`，但不会复制 `.venv`、`.python`、历史日志或 `data/` 业务数据。每次代码更新后重新运行同一脚本即可覆盖旧发布包。

## 交付与启动

1. 先停止旧电脑上的机器人。
2. 通过 U 盘、局域网或加密压缩包传输 ZIP，避免公开网盘。
3. 在目标 Windows 10/11 x64 电脑上完整解压。
4. 双击“启动机器人.cmd”，成功后直接在飞书测试消息和 Excel。
5. 需要关闭时双击“停止机器人.cmd”；出错时双击“查看运行日志.cmd”。

发布目录包含飞书 App Secret，已由 `.gitignore` 排除。不要提交或公开分享 `release/`。

## 更新

保留项目源码中的 `.env`，完成代码修改和测试后重新执行构建脚本，再用新 ZIP 替换目标电脑上的整个旧目录。替换前先停止机器人；不要覆盖仍在运行的程序文件。
