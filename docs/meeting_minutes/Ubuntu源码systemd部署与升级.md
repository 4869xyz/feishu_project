# Ubuntu 源码 + systemd 部署与升级

面向在 Ubuntu Server 上用 **Git 源码 + `.venv` + systemd** 长期运行周例会纪要机器人（推荐固定服务器场景）。若使用 Linux 便携包，见 [Linux便携发布包构建与交付](Linux便携发布包构建与交付.md)。

默认示例路径：`/opt/feishu_project`，服务名：`meeting-minutes-bot`。

## 1. 首次部署

```bash
cd /opt
git clone -b feature/meeting-minutes-bot https://github.com/4869xyz/feishu_project.git
cd feishu_project

sudo apt update
sudo apt install -y build-essential python3.11-venv python3.11-dev
python3.11 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt

cp .env.meeting-minutes.example .env.meeting-minutes
# 编辑密钥；准备 people.yaml 与正式 Word 模板（相对路径）
```

安装 systemd（源码版示例）：

```bash
# 按实际用户与路径编辑后再安装
sudo cp packaging/linux/meeting/meeting-minutes-bot.source.service.example \
  /etc/systemd/system/meeting-minutes-bot.service
sudo nano /etc/systemd/system/meeting-minutes-bot.service

sudo systemctl daemon-reload
sudo systemctl enable --now meeting-minutes-bot
sudo systemctl status meeting-minutes-bot --no-pager
```

启动前停掉开发机 / Windows 便携包上的同一飞书应用实例。

## 2. 自动重启与安全上限

unit 配置为：

- `Restart=always`、`RestartSec=5`：崩溃约 5 秒后自动拉起
- `StartLimitIntervalSec=60`、`StartLimitBurst=5`：**任意 60 秒内最多启动/重启 5 次**

超过上限后 systemd **停止继续尝试**，服务进入失败/限流状态，等待管理员处理，避免坏配置时无限重启打满资源。

### 超限后如何恢复

1. 查看原因：`journalctl -u meeting-minutes-bot -n 100 --no-pager`
2. 修好配置、依赖或代码
3. 清除限流并重新启动：

```bash
sudo systemctl reset-failed meeting-minutes-bot
sudo systemctl start meeting-minutes-bot
sudo systemctl status meeting-minutes-bot --no-pager
```

不要在未排查时连续盲目 `restart`（可能再次撞上限）。

## 3. 变更类型怎么处理

| 变更 | 是否重启服务 | 操作 |
| --- | --- | --- |
| 人员 YAML / 正式模板（飞书上传或改磁盘） | 否 | 上传即生效，或改完发 `重载人员配置`；可用 `校验配置` |
| 业务代码 | 是 | `git pull` →（依赖有变则 pip）→ `systemctl restart` |
| `requirements.txt` | 是 | `pip install -r requirements.txt` → `restart` |
| `.env.meeting-minutes` | 是 | 改文件 → `restart`（环境变量仅启动时加载） |
| systemd unit 本身 | 是 | 改 unit → `daemon-reload` → `restart` |

## 4. 标准代码升级

```bash
cd /opt/feishu_project
git fetch origin
git checkout feature/meeting-minutes-bot
git pull origin feature/meeting-minutes-bot

.venv/bin/pip install -r requirements.txt

sudo systemctl restart meeting-minutes-bot
sudo systemctl status meeting-minutes-bot --no-pager
journalctl -u meeting-minutes-bot -n 50 --no-pager
```

飞书管理员再发一次 `校验配置` 确认。

### 回滚

```bash
cd /opt/feishu_project
git checkout <旧commit或标签>
.venv/bin/pip install -r requirements.txt
sudo systemctl reset-failed meeting-minutes-bot   # 若已触发启动限流
sudo systemctl restart meeting-minutes-bot
```

## 5. 常用运维命令

```bash
sudo systemctl status meeting-minutes-bot
sudo systemctl stop meeting-minutes-bot
sudo systemctl start meeting-minutes-bot
sudo systemctl restart meeting-minutes-bot
journalctl -u meeting-minutes-bot -f
tail -f /opt/feishu_project/logs/meeting_minutes/meeting_minutes_bot.log
```

## 6. 硬约束

- 同一飞书应用全局只跑一个实例。
- 真实 `.env`、人员 YAML 勿提交公开仓库。
- 源码部署与便携包启动脚本不要对同一配置双开。
