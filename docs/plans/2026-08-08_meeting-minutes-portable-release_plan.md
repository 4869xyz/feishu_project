# 周例会纪要机器人 Windows 便携发布包实施计划

**状态：** 已完成  
**创建日期：** 2026-08-08  
**负责人：** Agent

## 1. 背景与目标

- 业务背景：需要把纪要机器人直接交给没有 Python 环境的同事运行。
- 目标：交付一个 ZIP，解压后双击即可运行，内置当前飞书应用配置与人员名单，运行数据为全新空白。
- 成功标准：目标电脑无需安装任何依赖；OCR、模板渲染、周日提醒、管理员命令全部可用；发布包不含历史数据。

## 2. 范围

**包含：**

- 冻结运行时项目根目录解析与顶层入口脚本
- PyInstaller spec（含 OCR 模型、原生 DLL、tzdata）
- 构建脚本、中文启停与日志入口、随包说明
- 文档同步

**不包含：**

- 自动开机启动、Web 或桌面 UI、历史数据迁移、飞书应用变更

## 3. 影响分析与技术决策

- 受影响模块：`meeting_minutes_bot.settings`、`meeting_minutes_bot.__main__`、新增 `run_meeting_minutes_bot.py`、新增 `packaging/windows/meeting/`
- 数据安全：发布包含 App Secret 与真实 `open_id`，`release/` 已被 Git 忽略，只能点对点交付
- 已确定的技术决策：
  - onedir 模式，项目根 = EXE 所在目录，与销售机器人一致
  - 保留图片 OCR，接受约 155 MB 包体
  - 纪要的 cmd 与说明放在 `meeting/` 子目录，避免被销售构建脚本的 `*.cmd` 通配符误收
  - 构建脚本在缺少 pip 时回退到 `uv`
  - PowerShell 中文文件名统一用 `[regex]::Unescape` 转义写法

## 4. 预计修改文件

| 路径 | 操作 | 原因 |
| --- | --- | --- |
| `meeting_minutes_bot/settings.py` | 修改 | 冻结运行时项目根 |
| `meeting_minutes_bot/__main__.py` | 修改 | 抽出 `cli()` |
| `run_meeting_minutes_bot.py` | 新增 | PyInstaller 入口 |
| `packaging/windows/meeting/MeetingMinutesBot.spec` | 新增 | 打包配置 |
| `packaging/windows/meeting/build_meeting_portable.ps1` | 新增 | 构建与组装 |
| `packaging/windows/meeting/launcher/*` | 新增 | 启停与日志 |
| `packaging/windows/meeting/*.cmd`、`使用说明.txt` | 新增 | 用户入口与说明 |
| `tests/meeting_minutes/test_settings_people_period.py` | 修改 | 冻结路径测试 |
| 文档与 `VersionLog.md` | 修改 | 同步说明 |

## 5. 原子 Checklist

- [x] `settings.py` 支持冻结运行时项目根目录，并补充对应测试
- [x] 抽出 `cli()` 并新增顶层入口 `run_meeting_minutes_bot.py`
- [x] 编写 spec，收集 OCR 模型、原生 DLL 与 tzdata
- [x] 编写构建脚本：校验、测试、构建、组装、ZIP 与 SHA256
- [x] 制作纪要专用启动、停止、看日志脚本与中文弹窗文案
- [x] 编写随包傻瓜式使用说明，含单实例与保密约束
- [x] 实际构建并验证启动与 OCR 加载
- [x] 同步 `ARCHITECTURE.md`、README、`VersionLog.md` 与计划索引

## 6. 测试与验收

- [x] 自动化测试：160 passed, 1 skipped
- [x] 冻结验证：EXE 以自身目录为项目根，从 `_internal` 加载 OCR 模型且无下载行为，模板与人员校验通过，飞书长连接建立成功
- [x] 包结构核对：三份说明与三个 cmd 齐全，`data/` 与 `logs/` 文件数为 0，销售包未被污染
- [ ] 手动验证：目标电脑解压后走通提交、生成 Word 与停止（待交付时执行）

## 7. 执行记录

| 日期 | 状态 | 记录 |
| --- | --- | --- |
| 2026-08-08 | 草拟 | 确认免安装便携交付、沿用当前配置、数据清空 |
| 2026-08-08 | 已完成 | 构建通过，ZIP 1987 个文件、154,823,786 字节，SHA-256 `12D6AA00D5C5F8E5A665F283E709706B16FEA382AC7CFADE79E67240901C7061` |
