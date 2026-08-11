# 功能计划目录

此目录保存每项可验收功能的实施边界、决策和执行记录。它是 Plan 模式的事实来源；`VersionLog.md` 只记录已经落地的实质变更。

## 计划索引

| 日期 | 计划 | 状态 | 验收摘要 |
| --- | --- | --- | --- |
| 2026-08-11 | [DOCX 表格与图片原样嵌入](2026-08-11_docx-rich-embed_plan.md) | 已完成 | 源 Word 持久化并注入周报；纪要测试 61 通过 1 跳过。 |
| 2026-08-08 | [纪要机器人便携发布包](2026-08-08_meeting-minutes-portable-release_plan.md) | 已完成 | 免安装 Windows x64 交付，随包 OCR 模型与当前配置，数据全新；160 项测试通过。 |
| 2026-08-08 | [人员 YAML 热重载](2026-08-08_people-yaml-hot-reload_plan.md) | 已完成 | 管理员命令重载 people.yaml，校验通过热替换，失败保留原名单；纪要测试 55 通过 1 跳过。 |
| 2026-08-08 | [周日未提交提醒](2026-08-08_meeting-minutes-sunday-reminder_plan.md) | 已完成 | 周日 17:00/20:00 私聊缺交人员，SQLite 幂等；纪要测试 50 通过 1 跳过。 |
| 2026-07-20 | [固定云表链接批量登记](2026-07-20_registered-cloud-source-batch-add_plan.md) | 已完成 | 多链接按序去重，部分失败继续并统一回复；101 项测试通过。 |
| 2026-07-20 | [Windows 单人免安装便携交付](2026-07-20_windows-portable-release_plan.md) | 已完成 | 免 Python、一键后台启停、当前配置原样带入；2026-07-21 最新包 103 项测试与冻结 EXE 验证通过。 |
| 2026-07-20 | [I:T 金额列自适应宽度](2026-07-20_signing-amount-column-autofit_plan.md) | 已完成 | 各月份独立扩展并保留模板下限，`bestFit` 持久化；97 项测试通过。 |
| 2026-07-20 | [I:T 金额会计专用一位小数](2026-07-20_signing-accounting-number-format_plan.md) | 已完成 | 明细与全部汇总金额统一格式，数值和公式不变；96 项测试通过。 |
| 2026-07-20 | [汇总结果自动隐藏黄色/橙色数据行](2026-07-20_yellow-signing-row-hiding_plan.md) | 已完成 | C:G 非空暖色字段触发，空白暖色残留和主题黑色不误判；2026-07-21 复核后 103 项测试通过。 |
| 2026-07-20 | [固定云表数字编号与批量管理](2026-07-20_registered-cloud-source-management_plan.md) | 已完成 | 连续数字编号、完整顺序重排和固定云表批量清空；92 项测试通过。 |
| 2026-07-16 | [签约汇总区块空行](2026-07-16_signing-summary-spacing_plan.md) | 已完成 | 个人与小组、最后小组与部门汇总间各增加一行模板空行；88 项测试通过。 |
| 2026-07-16 | [签约明细保留源字体颜色](2026-07-16_signing-font-color-preservation_plan.md) | 已完成 | A:T 逐格保留源字体颜色，其他样式仍来自模板；88 项测试通过。 |
| 2026-07-16 | [固定云表汇总时刷新](2026-07-16_registered-cloud-source-refresh_plan.md) | 已完成 | 固定来源登记、汇总前刷新、失败禁止旧缓存回退；85 项测试通过。 |
| 2026-07-16 | [下载缓存安全清理](2026-07-16_download-cache-cleanup_plan.md) | 已完成 | 管理员白名单、全局活动文件保护和限定目录清理；74 项测试通过。 |
| 2026-07-16 | [签约工作表优先汇总](2026-07-16_signing-sheet-priority_plan.md) | 已完成 | 单/多工作表签约选表、回款隔离和签约独立输出。 |
| 2026-07-15 | [销售工作簿自动汇总](2026-07-15_sales-workbook-aggregation_plan.md) | 已完成 | SOP 驱动的 XLSX 校验、批次汇总和飞书文件回复；57 项测试通过。 |
| 2026-07-15 | [监听器运行安全](2026-07-15_listener-runtime-safety_plan.md) | 已完成 | 群聊准入、日志脱敏与单实例锁；完整基线 50 项测试通过。 |
| 2026-07-14 | [飞书表格链接导出](2026-07-14_feishu-table-link-export_plan.md) | 已完成 | Sheets/Wiki XLSX 导出与归档；当日基线 46 项测试通过。 |

## 命名与状态

- 使用 `YYYY-MM-DD_<feature>_plan.md` 命名，例如 `2026-07-20_excel-validation_plan.md`。
- 从 [`../templates/feature-plan-template.md`](../templates/feature-plan-template.md) 复制模板开始；一个计划只对应一个可验收目标。
- 初始状态为“草拟”或“待确认”。用户明确要求“开始执行”后改为“执行中”，原子 Checklist 逐项标记为 `[~]` 或 `[x]`。
- 完成或阻塞时记录测试结果、遗留风险和最终状态，并在 `VersionLog.md` 追加同日变更。

## 归档与 Cursor 上下文

- 正在执行和近期完成的计划保留在本目录，便于 Cursor 读取当前工作边界。
- 过期计划可移动到 `docs/plans/archive/`；该归档目录被 `.cursorignore` 排除，避免历史上下文干扰新需求。
- 不在计划中记录 `.env`、完整 Token、真实附件、日志或可识别业务数据。
