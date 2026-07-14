# 飞书多维表格只读连接检查

这是一个最小的 Python 3.11 项目，用于验证企业自建应用能否连接飞书开放平台，并只读获取指定多维表格中的字段和记录。

当前版本只做以下事情：

- 获取并在进程内缓存 `tenant_access_token`，到期前 5 分钟自动刷新；
- 读取两张多维表格的字段结构；
- 通过查询记录接口分页读取记录；
- 每张表最多保留 3 条样例记录到本地检查报告；
- 将检查过程写到终端，并生成 UTF-8 JSON 报告。

项目没有实现任何飞书数据新增、更新或删除接口。

## 项目结构

```text
.
├── config/                  # .env 加载、校验和本地目录初始化
├── clients/                 # 飞书鉴权与多维表格只读客户端
├── scripts/                 # 可执行连接检查脚本
├── tests/                   # 不访问真实飞书的 pytest 测试
├── output/                  # JSON 检查报告
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
FEISHU_APP_TOKEN=

STANDARD_DETAIL_TABLE_ID=
PERSON_SUMMARY_TABLE_ID=

LOCAL_OUTPUT_DIR=./output
LOG_LEVEL=INFO
```

- `FEISHU_APP_ID`、`FEISHU_APP_SECRET`：企业自建应用凭证；
- `FEISHU_APP_TOKEN`：多维表格 Base 的 `app_token`；
- 两个 `*_TABLE_ID`：分别对应「签约标准明细表」和「签约个人汇总表」的 `table_id`；
- `LOCAL_OUTPUT_DIR`：报告目录，相对路径按项目根目录解析；
- `LOG_LEVEL`：可选 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。

`.env` 已被 `.gitignore` 排除。不要把 App Secret、完整访问令牌或含真实密钥的 `.env` 提交到版本库。

## 飞书侧需要手动完成的配置

代码不能代替你修改飞书开放平台后台。运行真实连接检查前，需要手动确认：

1. 应用是企业自建应用，并已启用所需的服务端 API 权限；
2. 至少授予字段列表和记录查询所需的只读权限。飞书控制台通常显示为“查看、评论和导出多维表格”，或更细粒度的“获取数据表信息”“根据条件搜索记录”；
3. 应用版本已经发布或在测试范围内生效；
4. 目标 Base 已向该应用开放，应用在文档协作者或多维表格高级权限中具有相应读取权限；
5. `.env` 中的 `app_token` 和两个 `table_id` 来自同一个目标 Base，并且值准确。

如果 Base 开启了高级权限，API 即使返回成功也可能因为调用身份没有对应数据权限而得到空记录。此时需要在 Base 的高级权限中为应用补充访问范围。

## 运行连接检查

激活虚拟环境后执行：

```bash
python -m scripts.check_connection
```

也可以不激活环境，直接在 Windows 上执行：

```bat
.venv\Scripts\python.exe -m scripts.check_connection
```

脚本会依次执行鉴权，并检查：

1. 签约标准明细表；
2. 签约个人汇总表。

单张表失败不会阻断后续表。空表不算失败；字段读取成功且记录查询返回空列表时，该表仍视为可访问。

进程退出码：

- `0`：鉴权和两张表全部成功；
- `1`：鉴权、表检查或报告写入至少一项失败；
- `2`：本地配置缺失或无效。

## 输出文件

默认生成：

```text
output/feishu_connection_report.json
logs/feishu_connection.log
```

JSON 报告使用 UTF-8 编码和格式化缩进，包含鉴权是否成功、各表字段、最多 3 条样例记录和逐表错误。终端与日志只显示脱敏 token，不输出 App Secret、完整 token 或 Authorization 请求头。

请注意：样例记录本身可能包含业务数据。`output/*.json` 已被 Git 忽略，仍应按公司数据安全要求保管。

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

- 当前版本只支持读取，不支持向飞书写入、修改或删除数据；
- 没有数据清洗、字段映射、去重、汇总或导入日志写入逻辑；
- 没有数据库和 Web 服务；
- token 缓存仅存在于当前进程，进程退出后不会落盘；
- 报告只保存每张表最多 3 条样例记录，客户端方法本身支持分页读取更多记录；
- 数据清洗和数据汇总将在下一阶段实现。

## 官方接口参考

- [获取 tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal?lang=zh-CN)
- [列出字段](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list?lang=zh-CN)
- [查询记录](https://open.feishu.cn/document/docs/bitable-v1/app-table-record/search?lang=zh-CN)
