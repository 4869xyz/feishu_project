# 飞书机器人 Excel 附件下载

这是一个最小的 Python 3.11 项目：通过飞书长连接接收机器人消息，把直接发送的
Excel 附件保存到本地 `data/inbox/`。

当前版本只做以下事情：

- 使用 `app_id` / `app_secret` 建立飞书长连接；
- 识别会话中直接上传的 `.xlsx`、`.xls`、`.xlsm` 文件；
- 通过 IM 资源接口下载附件，写入本地收件箱；
- 向发送方回复下载结果（成功、重复、格式不支持或失败）。

项目没有实现多维表格读写、数据清洗、汇总或导入逻辑。

## 项目结构

```text
.
├── feishu_bot_listener.py   # 长连接入口：收消息并下载 Excel
├── config/                  # .env 加载、校验和本地目录初始化
├── clients/                 # 鉴权、附件下载与消息解析
├── data/inbox/              # Excel 收件箱（实际文件被 Git 忽略）
├── tests/                   # 不访问真实飞书的 pytest 测试
├── logs/                    # 本地运行日志
├── .env.example
├── requirements.txt
└── pytest.ini
```

## 环境要求

- Python 3.11 或更高版本
- Windows PowerShell、命令提示符或其他可运行 Python 的终端

## 安装

如果电脑已经安装 Python 3.11+，在项目根目录执行：

```bash
python -m venv .venv
```

Windows 激活虚拟环境：

```bash
.venv\Scripts\activate
```

安装依赖：

```bash
python -m pip install -r requirements.txt
```

本工作区已经将 Python 3.11、虚拟环境和依赖缓存安装在项目内的 `.python/`、`.venv/` 和 `.uv-cache/`。如果另一台电脑没有全局 Python、但已经安装 `uv`，可在 PowerShell 中用以下方式保持所有安装文件位于当前项目：

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.python"
uv python install 3.11 --install-dir .python --no-bin --no-registry --cache-dir .uv-cache
uv venv .venv --python 3.11 --managed-python --seed --cache-dir .uv-cache
uv pip install --python .venv\Scripts\python.exe -r requirements.txt --cache-dir .uv-cache
```

## 配置 `.env`

先复制配置模板：

```bat
copy .env.example .env
```

然后填写以下变量，变量名必须与模板完全一致：

```dotenv
FEISHU_APP_ID=
FEISHU_APP_SECRET=

FEISHU_INBOX_DIR=./data/inbox
FEISHU_MAX_DOWNLOAD_BYTES=104857600
LOG_LEVEL=INFO
```

- `FEISHU_APP_ID`、`FEISHU_APP_SECRET`：企业自建应用凭证；
- `FEISHU_INBOX_DIR`：Excel 收件箱目录，相对路径按项目根目录解析；
- `FEISHU_MAX_DOWNLOAD_BYTES`：单文件本地下载上限，默认 100 MB；
- `LOG_LEVEL`：可选 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。

`.env` 已被 `.gitignore` 排除。不要把 App Secret、完整访问令牌或含真实密钥的 `.env` 提交到版本库。

## 飞书侧需要手动完成的配置

代码不能代替你修改飞书开放平台后台。运行机器人前，需要手动确认：

1. 应用是企业自建应用，并已启用机器人能力与长连接；
2. 至少授予获取与下载消息中文件资源所需的权限；
3. 应用版本已经发布或在测试范围内生效；
4. 机器人已加入目标单聊或群聊。

## 运行机器人

激活虚拟环境后执行：

```bash
python feishu_bot_listener.py
```

也可以不激活环境，直接在 Windows 上执行：

```bat
.venv\Scripts\python.exe feishu_bot_listener.py
```

向机器人所在的单聊或群聊直接发送 `.xlsx`、`.xls` 或 `.xlsm` 文件后，程序会将文件保存至 `data/inbox/`。

下载采用临时 `.part` 文件后原子改名；同一消息重复投递时不会重复下载。实际 Excel 文件被 Git 忽略。该功能只处理会话中直接上传的附件，不处理云文档链接、卡片附件、合并转发子消息或保密消息。

日志默认写入：

```text
logs/feishu_bot_listener.log
```

## 运行测试

```bash
pytest
```

或：

```bat
.venv\Scripts\python.exe -m pytest
```

测试全部使用 mock，不访问真实飞书，也不会修改任何飞书数据。测试临时文件只写入当前项目的 `tests_runtime/`。

## 当前限制

- 只处理直接上传的 Excel 附件，不解析 Excel 内容；
- 没有数据清洗、字段映射、去重、汇总或导入逻辑；
- 没有数据库和 Web 服务；
- token 缓存仅存在于当前进程，进程退出后不会落盘。

## 官方接口参考

- [获取 tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal?lang=zh-CN)
- [获取消息中的资源文件](https://open.feishu.cn/document/server-docs/im-v1/message-resource/get?lang=zh-CN)
