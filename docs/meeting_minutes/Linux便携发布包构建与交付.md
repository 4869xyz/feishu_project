# 周例会纪要机器人 Linux 便携包构建与交付

面向 Ubuntu Server 24.04 LTS x64：目标机无需再装业务 Python 依赖，解压后执行 `./启动机器人.sh` 或接入 systemd 即可长期运行。飞书聊天仍是唯一业务界面。

与 Windows 便携包、销售机器人便携包相互独立。

## 硬性前提

- **必须在 Linux x64 上构建**（建议与运行环境同为 Ubuntu 24.04）。Windows 上的 PyInstaller **不能**交叉编译出本包。
- 构建机需要：Python 3.11 虚拟环境（项目 `.venv`）、网络（首次装依赖）、以及常见构建工具，例如：

```bash
sudo apt update
sudo apt install -y build-essential python3.11-venv python3.11-dev
```

## 生成发布包

把本仓库放到 Ubuntu（git clone 或 scp），在项目根目录：

```bash
# 准备 .venv 与依赖（若尚未创建）
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 确保已有 .env.meeting-minutes，且人员 YAML / 模板路径为相对路径且文件存在

chmod +x packaging/linux/meeting/build_meeting_portable.sh
./packaging/linux/meeting/build_meeting_portable.sh
```

调试构建可加 `--skip-tests`；正式交付前应完整跑测试。

产物：

```text
release/周例会纪要机器人/
release/周例会纪要机器人-Linux-x64.tar.gz
```

脚本会打印文件数、字节数和 SHA-256。

## 包内结构

```text
周例会纪要机器人/
  启动机器人.sh / 停止机器人.sh / 查看运行日志.sh
  使用说明.txt / 用户使用说明.md / 管理员使用说明.md
  meeting-minutes-bot.service.example
  launcher/
  程序/
    MeetingMinutesBot
    .env.meeting-minutes
    meeting_minutes_bot/config/people.yaml
    meeting_minutes_bot/templates/...
    data/meeting_minutes/   logs/meeting_minutes/
    _internal/
```

发布包不携带历史提交、附件或已生成 Word。

## 两条硬约束

- **同一飞书应用同一时刻只能有一个实例。** 服务器启动后必须停止开发机 / Windows 便携包上的纪要机器人。
- **包内含 App Secret 与真实 open_id。** 仅点对点交付，禁止公开网盘；`release/` 已被 Git 忽略。

## 交付与启动

1. 停止所有其他纪要机器人实例。
2. 将 `周例会纪要机器人-Linux-x64.tar.gz` 传到 Ubuntu，校验 SHA-256。
3. `tar -xzf 周例会纪要机器人-Linux-x64.tar.gz -C /opt`（或自选目录）。
4. `cd /opt/周例会纪要机器人 && chmod +x 启动机器人.sh 停止机器人.sh 查看运行日志.sh && ./启动机器人.sh`
5. 飞书验证：成员提交；管理员 `校验配置` / `查看本周提交状态` / `生成本周纪要`。

### 推荐：systemd

编辑包内 `meeting-minutes-bot.service.example`，把 `WorkingDirectory` / `ExecStart` 改成实际「程序」绝对路径。单元已配置 `Restart=always`，以及 **60 秒内最多启动 5 次**（`StartLimitIntervalSec=60` / `StartLimitBurst=5`）；超限后需管理员 `systemctl reset-failed` 再 `start`。

```bash
sudo cp meeting-minutes-bot.service.example /etc/systemd/system/meeting-minutes-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now meeting-minutes-bot
sudo systemctl status meeting-minutes-bot
```

使用 systemd 后不要再跑 `./启动机器人.sh`。固定服务器若用源码 + venv，改看 [Ubuntu 源码 systemd 部署与升级](Ubuntu源码systemd部署与升级.md)。

## 更新

- **代码/依赖变更：** 在 Ubuntu 上重新执行构建脚本，停止服务后整体替换目录，再启动。
- **仅人员/模板：** 管理员飞书上传 YAML 或同名模板，或改磁盘文件后发 `重载人员配置`，无需重新打包。
