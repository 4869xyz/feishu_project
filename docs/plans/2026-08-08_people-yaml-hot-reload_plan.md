# 人员 YAML 简易维护（热重载）实施计划

**状态：** 已完成  
**创建日期：** 2026-08-08  
**负责人：** Agent

## 1. 背景与目标

- 业务背景：人员会因离职/入职少量变动；管理员固定且能接触全部源码与配置文件。
- 目标：继续以 `people.yaml` 为唯一配置源，管理员改完文件后免重启即时生效。
- 成功标准：管理员命令重载成功后提交权限、提醒名单和 DOCX 渲染立即使用新名单；YAML 或模板校验失败时保留原名单并回复原因。

## 2. 范围

**包含：**

- 共享可热替换的 `PeopleStore`
- 管理员命令 `重载人员配置`（校验 YAML + 模板占位符后原子替换）
- 离职/新人维护 SOP 文档与测试

**不包含：**

- 飞书命令增删改人员字段、人员入库、管理员账号管理 UI、在线改 Word 模板

## 3. 影响分析与技术决策

- 受影响模块：`meeting_minutes_bot` 的 `people`、`service`、`document`、`reminder`、`__main__`
- 已确定的技术决策：
  - `PeopleStore` 持有当前 `PeopleDirectory` 和 YAML 路径；`service`/`renderer`/`reminder` 均经同一 store 读取
  - 重载走现有 `load_people` 校验 + `validate_template(candidate)`，全部通过才 `replace`
  - 权限复用顶层 `admins` 列表；命令幂等（重复执行结果一致），不做事件 claim

## 4. 预计修改文件

| 路径 | 操作 | 原因 |
| --- | --- | --- |
| `meeting_minutes_bot/people.py` | 修改 | `PeopleStore` + `ensure_store` |
| `meeting_minutes_bot/service.py` | 修改 | `重载人员配置` 命令 |
| `meeting_minutes_bot/document.py` | 修改 | 经 store 读人员、支持候选目录校验 |
| `meeting_minutes_bot/reminder.py` | 修改 | 经 store 读人员 |
| `meeting_minutes_bot/__main__.py` | 修改 | 构建共享 `PeopleStore` |
| `tests/meeting_minutes/test_people_reload.py` | 新增 | 重载权限/热生效/回退测试 |
| 文档与 `VersionLog.md` | 修改 | 命令说明、维护 SOP、架构数据流 |

## 5. 原子 Checklist

- [x] 新增 `PeopleStore`，统一持有可热替换的 `PeopleDirectory`
- [x] 管理员命令「重载人员配置」：校验 YAML+模板后替换，失败保留旧配置
- [x] `service`/`renderer`/`reminder`/`__main__` 改为经 `PeopleStore` 读人员
- [x] 补充重载测试，并写清离职/新人 SOP 与 `VersionLog.md`

## 6. 测试与验收

- [x] 自动化测试：`tests/meeting_minutes` 55 passed, 1 skipped（新增 5 项重载测试）
- [x] 异常与回滚：坏 YAML、缺占位符、无配置路径均保留原名单并回复错误
- [ ] 手动验证：真实环境管理员发送 `重载人员配置` 验证热生效（待部署时执行）

## 7. 执行记录

| 日期 | 状态 | 记录 |
| --- | --- | --- |
| 2026-08-08 | 草拟 | 确认走「YAML + 热重载」最简方案，不做飞书 CRUD 与入库 |
| 2026-08-08 | 已完成 | 实现共享 PeopleStore、重载命令、测试与文档同步 |
