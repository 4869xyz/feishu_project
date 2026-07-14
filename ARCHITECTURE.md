# 项目目录与架构说明

本文档说明 `feishu_project` 的目录结构、各文件职责，以及当前只读连通检查的实现方式。

## 项目定位

这是一个**最小只读连通检查项目**：验证企业自建应用能否连接飞书开放平台，并读取两张多维表格的字段结构和样例记录。

当前已实现：

- 获取并缓存 `tenant_access_token`（到期前 5 分钟自动刷新）
- 读取两张多维表格的字段结构
- 分页查询记录（连通检查时每张表最多保留 3 条样例）
- 终端输出检查过程，并生成 UTF-8 JSON 报告

当前**未实现**：

- 任何飞书数据新增、更新、删除
- 数据清洗、字段映射、去重、汇总
- 数据库或 Web 服务

## 目录总览

```text
feishu_project/
├── .env / .env.example     # 真实配置 / 配置模板
├── config/                 # 读配置、校验
├── clients/                # 飞书 HTTP 客户端
├── scripts/                # 可执行入口（连通检查）
├── tests/                  # 单元测试（全 mock，不访问真实飞书）
├── output/                 # 检查报告 JSON
├── logs/                   # 运行日志
├── README.md               # 安装、配置与使用说明
├── plan1.md                # 早期需求/规划草稿
├── ARCHITECTURE.md         # 本文件：目录与架构说明
├── requirements.txt        # Python 依赖
└── pytest.ini              # pytest 配置
```

说明：

- `.venv/`、`.python/`、`.uv-cache/` 是本地 Python 运行环境与缓存，不是业务代码。
- `.env` 含真实密钥，已被 `.gitignore` 排除，不要提交到版本库。

## 各文件 / 目录作用

| 路径 | 作用 |
|------|------|
| `.env` | 真实运行配置：`APP_ID`、`APP_SECRET`、`APP_TOKEN`，以及两张表的 `table_id` |
| `.env.example` | 与 `.env` 同名变量的空模板，供复制填写 |
| `config/settings.py` | 加载 `.env`、校验必填项、创建 `output/` 与 `logs/`，产出不可变的 `Settings` |
| `config/__init__.py` | 对外导出 `load_settings`、`Settings`、`ConfigurationError` |
| `clients/feishu_client.py` | 核心客户端：鉴权、带重试的 HTTP、列字段、查记录 |
| `clients/__init__.py` | 导出客户端类、异常类型与 `mask_token` |
| `scripts/check_connection.py` | CLI 入口：鉴权 → 逐表检查 → 打印结果 → 写 JSON 报告 |
| `scripts/__init__.py` | 使 `python -m scripts.check_connection` 可作为包模块运行 |
| `tests/test_settings.py` | 测试配置加载、缺变量报错、运行目录创建 |
| `tests/test_feishu_client.py` | 测试 token、重试、脱敏、分页等客户端行为 |
| `tests/test_check_connection.py` | 测试报告生成、单表失败不阻断、鉴权失败仍出报告 |
| `tests/conftest.py` | 提供项目内临时目录 `tests_runtime/`，避免写入系统 Temp |
| `output/feishu_connection_report.json` | 最近一次连通检查的完整结果 |
| `logs/feishu_connection.log` | 文件日志（终端另有打印；敏感信息已脱敏） |
| `README.md` | 安装、权限、运行与限制说明 |
| `plan1.md` | 早期规划文档（可能仍含旧版四张表描述，以当前代码为准） |
| `requirements.txt` | 依赖列表（如 `requests`、`python-dotenv`、`pytest`） |
| `pytest.ini` | pytest 基础配置 |

## 当前检查的两张表

`.env` 中需要配置：

| 环境变量 | 对应表 |
|----------|--------|
| `STANDARD_DETAIL_TABLE_ID` | 签约标准明细表 |
| `PERSON_SUMMARY_TABLE_ID` | 签约个人汇总表 |

另外还需：

- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`：企业自建应用凭证
- `FEISHU_APP_TOKEN`：多维表格 Base 的 `app_token`
- `LOCAL_OUTPUT_DIR`（可选，默认 `./output`）
- `LOG_LEVEL`（可选，默认 `INFO`）

## 架构分层

项目分为三层：**配置 → 客户端 → 脚本编排**。

```text
.env
  │
  ▼
config/settings.py          # 校验并产出 Settings
  │
  ▼
clients/feishu_client.py    # FeishuBitableClient 调用飞书 API
  ▲
  │
scripts/check_connection.py # 编排检查流程，写报告与日志
  │
  ├──► output/*.json
  └──► logs/*.log
```

### 1. 配置层 `config`

`load_settings()` 负责：

1. 读取 `.env` 与系统环境变量（显式环境变量优先）
2. 检查必填项是否齐全且非空
3. 解析 `LOCAL_OUTPUT_DIR`、`LOG_LEVEL`
4. 确保 `output/`、`logs/` 目录存在
5. 返回冻结的 `Settings` 对象

缺少必填变量时抛出 `ConfigurationError`，连通检查脚本退出码为 `2`。

### 2. 客户端层 `clients`

`FeishuBitableClient` 封装飞书开放平台 HTTP 调用，对外主要能力：

1. **`get_tenant_access_token()`**  
   使用 `APP_ID` + `APP_SECRET` 换取 `tenant_access_token`，在进程内缓存；到期前约 5 分钟自动刷新。

2. **`list_fields(table_id)`**  
   调用  
   `GET /bitable/v1/apps/{app_token}/tables/{table_id}/fields`  
   自动分页读取全部字段。

3. **`search_records(table_id, max_records=...)`**  
   调用  
   `POST /bitable/v1/apps/{app_token}/tables/{table_id}/records/search`  
   分页查询记录；连通检查中每张表最多取 3 条。

内部还处理：

- 超时、连接失败、HTTP 429/5xx 等可重试错误
- 错误信息脱敏（不输出 App Secret、完整 token）
- 请求 ID 提取，便于排查飞书侧问题

### 3. 编排层 `scripts`

执行：

```bat
.venv\Scripts\python.exe -m scripts.check_connection
```

流程如下：

1. `load_settings()` 加载配置
2. 获取 `tenant_access_token`（鉴权）
3. 依次检查两张表：签约标准明细表 → 签约个人汇总表
4. 每张表：读取字段 + 最多 3 条样例记录
5. 终端打印结果，并写入 `output/feishu_connection_report.json`
6. 两张表都成功 → 退出码 `0`；否则 `1`

行为约定：

- 单表失败**不会**阻断后续表检查
- 空表（字段可读、记录为空列表）仍视为成功
- 鉴权失败时仍会生成报告，并为未检查的表写入跳过原因

## 飞书侧数据关系

```text
企业自建应用 (APP_ID + SECRET)
        │
        ▼ 换取 token
tenant_access_token
        │
        ▼ 定位 Base
FEISHU_APP_TOKEN  ──► 多维表格 Base
                        ├── 签约标准明细表 (STANDARD_DETAIL_TABLE_ID)
                        └── 签约个人汇总表 (PERSON_SUMMARY_TABLE_ID)
```

运行真实连通检查前，还需在飞书开放平台手动确认：

1. 应用已开通多维表格只读相关权限（如 `bitable:app:readonly`）
2. 权限版本已发布或在测试范围内生效
3. 目标 Base 已向该应用开放协作者/数据权限
4. `.env` 中的 `app_token` 与两个 `table_id` 属于同一 Base

## 测试架构

- 测试全部使用 mock / fake client，**不访问真实飞书**，也不修改任何飞书数据
- 临时文件只写入项目内的 `tests_runtime/`（由 `conftest.py` 管理，已 gitignore）
- 运行方式：

```bat
.venv\Scripts\python.exe -m pytest
```

## 输出文件说明

| 文件 | 内容 |
|------|------|
| `output/feishu_connection_report.json` | 鉴权结果、各表字段、最多 3 条样例记录、逐表错误 |
| `logs/feishu_connection.log` | 运行过程日志；token 已脱敏 |

注意：样例记录可能包含业务数据。`output/*.json` 已被 Git 忽略，仍应按公司数据安全要求保管。

## 扩展建议

后续若要做清洗、汇总或写回，建议继续复用现有分层：

- 配置仍走 `config.settings.Settings`
- HTTP 访问仍走 `clients.FeishuBitableClient`（可在只读能力上扩展写接口）
- 业务编排放在 `scripts/` 或新增独立模块，避免把业务逻辑塞进客户端

当前阶段以「能稳定只读连通两张表」为边界；业务处理属于下一阶段。
