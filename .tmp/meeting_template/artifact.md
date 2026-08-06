# 周例会纪要正式模板：参考模板蒸馏记录

- **参考文件**：`F:\Dene_internship\0805meeting_minutes\周例会纪要.docx`
- **SHA-256**：`d3e1673e4bef5a60f1aca9600680d6d11d0c08f330518dffaf5e5a5269292666`
- **页系统**：单节、A4 纵向（7560310 × 10692130 EMU）；上/下页边距 0.8 英寸，左/右页边距 1.0 英寸；无页眉、无页脚、无表格、无图片。
- **结构**：居中标题“周例会纪要”；其后按总经理、商务部-销售组、人事行政、中台、运营推广组、后期部、美术部、程序部的顺序编排。多人部门以员工姓名作为二级条目，姓名下方留出正文区域。
- **模板槽位**：每名启用人员的正文区域必须是与 `people.yaml` 的 `template_key` 一致的 Jinja 变量：`{{ general_manager }}`、`{{ wu_aoxiang }}`、`{{ yang_yilin }}`、`{{ ye_mengzhen }}`、`{{ zhang_feilong }}`、`{{ lin_baili }}`、`{{ liang_jialong }}`、`{{ liu_hanwen }}`、`{{ xu_xinxin }}`、`{{ gao_canjian }}`、`{{ liu_jindi }}`、`{{ ma_gengbin }}`、`{{ zhang_chunwei }}`。
- **本次改造**：在保留参考文件的 A4 页面尺寸、页边距与部门顺序的基础上，统一标题、部门标题、人员姓名和正文占位符的层级与段落间距；不新增机器人无法填写的其他变量。
- **验证门槛**：使用 `docxtpl` 检查全部变量可识别；使用 `python-docx` 检查 13 个槽位齐全且页面几何与参考一致。环境中没有 LibreOffice，无法执行 PNG 渲染；采用结构性验证作为替代。
