请为我编写一个 Python 项目，用于连接飞书开放平台，并读取飞书多维表格中的数据。

## 一、当前阶段目标

本阶段只完成以下功能：

1. 使用 `app_id` 和 `app_secret` 获取飞书 `tenant_access_token`。
2. 使用 `app_token` 定位飞书多维表格 Base。
3. 使用不同的 `table_id` 读取指定数据表。
4. 获取每张数据表的字段结构。
5. 分页读取每张数据表中的记录。
6. 在终端打印连接和读取结果。
7. 将读取结果保存到本地 JSON 文件。
8. 提供基础测试。

本阶段不要实现：

* 数据清洗
* 字段映射
* 数据汇总
* 数据去重
* 写入标准明细表
* 更新个人汇总表
* 写入导入日志表
* 修改或删除飞书中的任何数据

当前程序只能读取飞书数据，不能写入数据。

---

## 二、技术要求

使用：

* Python 3.11 或更高版本
* requests
* python-dotenv
* pytest

要求使用：

```python
requests.Session
```

统一管理 HTTP 请求。

所有函数和类需要：

* 类型注解
* 必要的 docstring
* 清晰的异常处理

---

## 三、环境变量

通过项目根目录下的 `.env` 文件读取以下配置：

```dotenv
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_APP_TOKEN=

RAW_SUBMISSION_TABLE_ID=
STANDARD_DETAIL_TABLE_ID=
PERSON_SUMMARY_TABLE_ID=
IMPORT_LOG_TABLE_ID=

LOCAL_OUTPUT_DIR=./output
LOG_LEVEL=INFO
```

配置含义：

```text
FEISHU_APP_ID
飞书企业自建应用的 App ID

FEISHU_APP_SECRET
飞书企业自建应用的 App Secret

FEISHU_APP_TOKEN
飞书多维表格 Base 的 app_token

RAW_SUBMISSION_TABLE_ID
签约原始提交表 table_id

STANDARD_DETAIL_TABLE_ID
签约标准明细表 table_id

PERSON_SUMMARY_TABLE_ID
签约个人汇总表 table_id

IMPORT_LOG_TABLE_ID
导入日志表 table_id

LOCAL_OUTPUT_DIR
本地结果输出目录
```

要求：

1. 不得将真实密钥写死在代码中。
2. 创建 `.env.example`，但不要填写真实值。
3. 将 `.env` 加入 `.gitignore`。
4. 配置缺失时，程序应明确提示缺少哪个配置项。
5. 程序启动时自动创建 `output` 和 `logs` 目录。

---

## 四、建议项目结构

请创建以下目录结构：

```text
feishu_connection_project/
│
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
│
├── config/
│   ├── __init__.py
│   └── settings.py
│
├── clients/
│   ├── __init__.py
│   └── feishu_client.py
│
├── scripts/
│   ├── __init__.py
│   └── check_connection.py
│
├── tests/
│   ├── test_settings.py
│   └── test_feishu_client.py
│
├── output/
└── logs/
```

不要创建数据清洗、汇总、写入相关模块。

---

## 五、配置模块要求

请在：

```text
config/settings.py
```

中实现配置管理。

建议创建：

```python
@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    app_token: str
    raw_submission_table_id: str
    standard_detail_table_id: str
    person_summary_table_id: str
    import_log_table_id: str
    output_dir: Path
    log_level: str
```

提供：

```python
load_settings() -> Settings
```

功能要求：

1. 使用 `python-dotenv` 加载 `.env`。
2. 校验必填项不能为空。
3. 缺失配置时抛出明确异常。
4. 自动创建本地输出目录和日志目录。
5. 不打印 `app_secret`。

---

## 六、飞书客户端要求

请在：

```text
clients/feishu_client.py
```

中创建：

```python
class FeishuBitableClient:
    ...
```

### 1. 飞书鉴权

实现：

```python
get_tenant_access_token() -> str
```

要求：

1. 使用 `app_id` 和 `app_secret` 获取 `tenant_access_token`。
2. 在当前进程内缓存 token。
3. 在 token 到期前 5 分钟自动重新获取。
4. 不得在日志中打印完整 token。
5. 如需显示 token，只能脱敏显示，例如：

```text
t-abcd12...xyz9
```

### 2. 统一请求方法

实现内部方法：

```python
_request(
    method: str,
    path: str,
    **kwargs
) -> dict
```

要求：

1. 自动添加：

```http
Authorization: Bearer tenant_access_token
Content-Type: application/json; charset=utf-8
```

2. 默认超时时间为 30 秒。
3. 检查 HTTP 状态码。
4. 检查飞书响应中的业务 `code`。
5. 发生错误时，异常信息应尽量包含：

```text
HTTP 状态码
飞书 code
飞书 msg
request_id
请求路径
```

6. 网络临时错误最多重试 3 次。
7. 不要对明显的权限错误和参数错误进行无限重试。

### 3. 获取字段列表

实现：

```python
list_fields(
    table_id: str
) -> list[dict]
```

功能：

1. 获取指定数据表的所有字段。
2. 支持分页。
3. 返回原始字段信息。
4. 至少保留：

```text
field_id
field_name
type
is_primary
```

### 4. 查询记录

实现：

```python
search_records(
    table_id: str,
    page_size: int = 100,
    max_records: int | None = None
) -> list[dict]
```

要求：

1. 使用飞书多维表格记录查询接口。
2. 支持分页。
3. `page_size` 不超过飞书接口允许的最大值。
4. `max_records=None` 时读取全部记录。
5. 指定 `max_records` 时，只读取对应数量。
6. 返回结果中保留：

```text
record_id
fields
created_time
last_modified_time
```

如果接口实际返回字段名称不同，以飞书真实返回结果为准，不要自行伪造字段。

### 5. 本阶段禁止实现的方法

本阶段不要实现：

```python
create_record()
update_record()
delete_record()
batch_create_records()
batch_update_records()
```

如果需要保留未来扩展位置，只能写注释，不要写实际写入代码。

---

## 七、连接检查脚本

请在：

```text
scripts/check_connection.py
```

中实现可直接运行的连接检查程序。

运行命令：

```bash
python -m scripts.check_connection
```

执行顺序：

```text
加载配置
    ↓
初始化飞书客户端
    ↓
获取 tenant_access_token
    ↓
检查签约原始提交表
    ↓
检查签约标准明细表
    ↓
检查签约个人汇总表
    ↓
检查导入日志表
    ↓
输出检查报告
```

四张表配置如下：

```python
tables = {
    "签约原始提交表": settings.raw_submission_table_id,
    "签约标准明细表": settings.standard_detail_table_id,
    "签约个人汇总表": settings.person_summary_table_id,
    "导入日志表": settings.import_log_table_id,
}
```

每张表检查以下内容：

1. `table_id` 是否可以访问。
2. 是否能够读取字段列表。
3. 字段总数。
4. 字段名称。
5. 字段类型。
6. 最多读取 3 条样例记录。
7. 记录中是否存在 `record_id` 和 `fields`。

某张表检查失败后：

* 记录该表的错误
* 继续检查其他表
* 不要立即终止整个程序

---

## 八、终端输出要求

程序运行时，终端输出应清晰，例如：

```text
============================================================
开始检查飞书连接
============================================================

[成功] tenant_access_token 获取成功：t-abcd12...xyz9

正在检查：签约原始提交表
table_id：tblxxxxxxxx
[成功] 字段读取成功
字段数量：12
样例记录数量：3

字段列表：
- 提交时间
- 销售人员
- 客户名称
- 签约金额

正在检查：签约标准明细表
[失败] 无法访问该表
错误信息：code=xxxx, msg=xxxx

============================================================
检查完成
成功表数量：3
失败表数量：1
报告路径：output/feishu_connection_report.json
============================================================
```

不要在终端输出：

* App Secret
* 完整 tenant_access_token
* Authorization 请求头
* 敏感环境变量

---

## 九、本地输出文件

程序运行后生成：

```text
output/feishu_connection_report.json
```

建议格式：

```json
{
  "checked_at": "2026-07-11T14:30:00",
  "authentication": {
    "success": true,
    "error": null
  },
  "app_token": "bascnxxxxxxxx",
  "all_tables_success": true,
  "success_table_count": 4,
  "failed_table_count": 0,
  "tables": {
    "签约原始提交表": {
      "table_id": "tblxxxxxxxx",
      "success": true,
      "field_count": 10,
      "fields": [
        {
          "field_id": "fldxxxx",
          "field_name": "销售人员",
          "type": 1,
          "is_primary": false
        }
      ],
      "sample_record_count": 3,
      "sample_records": [],
      "error": null
    }
  }
}
```

要求：

1. 使用 UTF-8 编码。
2. 使用中文时不得出现乱码。
3. JSON 格式化缩进。
4. 某张表失败时仍生成完整报告。
5. 输出目录不存在时自动创建。

---

## 十、异常处理要求

至少处理以下情况：

### 1. 配置缺失

例如：

```text
缺少环境变量：FEISHU_APP_SECRET
```

### 2. App ID 或 App Secret 错误

明确提示鉴权失败。

### 3. 应用权限不足

尽量显示飞书返回的：

```text
code
msg
request_id
```

### 4. app_token 错误

明确提示无法访问多维表格 Base。

### 5. table_id 错误

指出具体失败的是哪一张表。

### 6. 网络超时

明确提示请求超时，不要只显示笼统错误。

### 7. 飞书返回非 JSON 内容

显示有限长度的响应内容，避免打印大量内容。

### 8. 表格没有数据

这不算连接失败。

只要字段读取成功，就可以认为表格可访问，样例记录数量可以为 0。

---

## 十一、测试要求

请使用 pytest 编写基础测试。

### `tests/test_settings.py`

测试：

1. 正确读取环境变量。
2. 缺失必填配置时抛出异常。
3. 输出目录能够自动创建。
4. `.env` 中的配置能够映射到 `Settings`。

### `tests/test_feishu_client.py`

使用 mock，不访问真实飞书。

测试：

1. 成功获取 token。
2. token 在有效期内被复用。
3. token 即将过期时重新获取。
4. 飞书业务 `code` 非 0 时抛出异常。
5. 字段列表分页正常。
6. 记录分页正常。
7. `max_records` 限制生效。
8. HTTP 错误可以转换成清晰异常。
9. 日志和异常中不出现完整 `app_secret`。

---

## 十二、README 要求

请创建 `README.md`，内容至少包括：

### 1. 项目用途

说明本项目当前只用于：

```text
连接飞书
获取访问凭证
读取多维表格字段
读取多维表格记录
生成本地连接报告
```

### 2. 环境要求

```text
Python 3.11+
```

### 3. 安装命令

```bash
python -m venv .venv
```

Windows：

```bash
.venv\Scripts\activate
```

安装依赖：

```bash
pip install -r requirements.txt
```

### 4. 配置方式

```bash
copy .env.example .env
```

并说明需要填写哪些配置。

### 5. 运行连接检查

```bash
python -m scripts.check_connection
```

### 6. 运行测试

```bash
pytest
```

### 7. 当前限制

明确写出：

```text
当前版本只支持读取，不支持向飞书写入、修改或删除数据。
数据清洗和数据汇总将在下一阶段实现。
```

---

## 十三、requirements.txt

请至少包含：

```text
requests
python-dotenv
pytest
```

如果采用额外重试库，可以加入：

```text
tenacity
```

但请避免添加当前阶段用不到的依赖，例如：

```text
pandas
openpyxl
数据库驱动
Web 框架
```

---

## 十四、验收标准

完成代码后，应满足：

```text
[ ] 能读取 .env 配置
[ ] 能成功获取 tenant_access_token
[ ] token 能够缓存和自动刷新
[ ] 能读取四张表的字段信息
[ ] 能分页读取四张表的记录
[ ] 空表不会被误判为连接失败
[ ] 某张表失败不影响其他表继续检查
[ ] 能生成 feishu_connection_report.json
[ ] 不向飞书写入任何数据
[ ] 不修改或删除飞书数据
[ ] app_secret 不出现在代码和日志中
[ ] 完整 token 不出现在日志中
[ ] pytest 测试能够运行
[ ] README 可以指导新用户完成配置和运行
```

---

## 十五、开发执行顺序

请严格按照以下顺序完成：
山地车
### 第一步：检查项目目录

先阅读当前项目已有文件。

如果项目目录为空，则按建议结构创建。

如果已有相同功能文件，则复用现有文件，不要创建重复模块。

### 第二步：实现配置模块

完成：

```text
config/settings.py
.env.example
.gitignore
```

然后运行配置测试。

### 第三步：实现鉴权

只实现：

```python
get_tenant_access_token()
```

并完成对应 mock 测试。

### 第四步：实现统一请求方法

完成：

```python
_request()
```

并处理 HTTP 错误和飞书业务错误。

### 第五步：实现字段读取

完成：

```python
list_fields()
```

并支持分页。

### 第六步：实现记录读取

完成：

```python
search_records()
```

并支持分页和 `max_records`。

### 第七步：实现连接检查脚本

依次检查四张表，每张表最多读取 3 条样例记录。

### 第八步：生成本地报告

生成：

```text
output/feishu_connection_report.json
```

### 第九步：补充测试和 README

运行：

```bash
pytest
```

确保测试通过。

---

## 十六、代码修改限制

1. 不要编写数据清洗代码。
2. 不要编写字段映射代码。
3. 不要编写汇总统计代码。
4. 不要实现飞书写入接口。
5. 不要修改或删除飞书记录。
6. 不要引入数据库。
7. 不要引入 Web 服务。
8. 不要创建当前阶段用不到的复杂架构。
9. 不要将所有代码写进一个文件。
10. 不要改变已经确认的环境变量名称。

---

## 十七、完成后的输出要求

完成代码后，请向我汇报：

1. 新增了哪些文件。
2. 修改了哪些文件。
3. 每个文件的职责。
4. 安装依赖的命令。
5. `.env` 的配置方法。
6. 运行连接检查的命令。
7. 运行测试的命令。
8. 预期生成的输出文件。
9. 当前版本尚未实现的功能。
10. 是否存在需要我手动完成的飞书权限配置。
11. 如果代码无法直接验证真实连接，请明确说明原因，不要假设已经连接成功。

请先阅读当前项目结构，再开始编写代码。不要一次性扩展到数据处理阶段。
