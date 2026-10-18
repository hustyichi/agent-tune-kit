---
name: atk-dataset
description: 构建或修订有版本的评测 Case 与预期结果，不运行 Agent。
---

# 准备评测数据

阅读 `../WORKFLOW.md`，遵循其中默认中文的产物规则。
根据已确认的示例、标准和审核反馈准备 CSV 或 JSONL，包含稳定 Case ID、业务输入、可选预期结果、来源组、用途和重放要求。
没有已确认标准时，不把 Agent 答案复制为 Ground Truth。
按来源任务／会话隔离 optimization、protection 和独立 holdout；来源未知不能视为独立。
holdout 运行需冻结里程碑 ID。ATK 跨 Round 记录已暴露来源组，阻止后续独立复用或本轮继续编辑候选。

调用 `store_dataset` 时显式提供列映射。它写入不可变的 `.atk/datasets/<dataset-id>/cases.jsonl` 和 manifest。
更正产生新 Dataset ID；已有 Assessment 和 Validation 仍绑定原 Case 内容。
将 `attachments` 映射为 JSONL 文件路径列表，或 CSV 单元格中的 JSON 列表。
相对路径以源数据集所在目录为基准。ATK 将解析后的路径和实际 SHA-256 纳入 Case 指纹；重放时附件必须存在且内容不变。
默认 runner 为每次 attempt 写入 `attachments.json`，通过命令 argv 的 `{attachments_file}` 暴露路径。
附件影响任务时，配置目标 Agent 读取该文件。确认 Case 可重放前，先说明环境或附件缺口。

## 常驻产物

新建或修订 Dataset 默认只保留以下五项；不改动、迁移或清理既有 `.atk` 数据。
本规则来源于用户确认的 Dataset 精简方案，生效日期为 2026-10-18。

| 位置 | 用途 |
| --- | --- |
| `.atk/datasets/<dataset-id>/cases.jsonl` | 不可变的输入、预期结果、用途、来源组和固定条件快照 |
| `.atk/datasets/<dataset-id>/manifest.json` | Dataset 身份、映射、数量和完整性指纹 |
| `.atk/dataset-preparation/<name-version>/evaluation-spec.json` | 已确认的判断标准、答案授权与来源限制；未校准时明确标记 |
| 同目录 `catalog-slice.json` | 评分依赖的固定参考目录切片；仅在确实需要时保留 |
| 同目录 `README.md` | 唯一人工入口：Dataset ID、数量与用途分布、来源/历史证据引用、保护选择依据、核验摘要、限制与下一步 |

没有已确认判据或不需要参考目录时省略对应文件，不生成占位或编造标准。
已有可复用的版本化规格或目录资产直接引用，不为每个 Dataset 再复制。
README 只保留摘要和引用，不重复展开完整输入、答案、规格、历史输出或审计正文。

来源追溯写入 Case 的 `initial_conditions`：原始文件路径、SHA-256、工作表/行号或等价定位。
保护选择保留历史证据路径、SHA-256、业务键及必要结论；引用原有结果，不另存完整历史输出。
封存前确定规格和参考资产的常驻路径及指纹；它们属于评分侧，不作为 Agent 输入或附件。
不默认生成 `source-rows.json`、`historical-evidence.json`、`provenance.json` 或 `verification.json`；
对应的来源、授权、保护依据和核验结论分别纳入上述既有位置。
只有复杂转换确需重复运行时，才在准备目录保留一份 `prepare.py`，写明输入、依赖和重建方法；
简单映射使用临时转换，不额外留脚本。附件原件及必要重放资产不受五项限制，不得为凑数删除。

## 临时材料与核验收尾

已有可导入的 CSV/JSONL 直接使用。新生成的导入 `source.jsonl`、操作请求/响应和一次性转换脚本放系统临时目录。
临时源引用的附件使用原件的最终路径，不把附件或评分资产放进待清理目录。
临时导入源的 `manifest.source` 和 Case `source.path` 仅记录导入位置；清理后不把它们作为原始数据读取入口，
README 说明临时源已清理，原始来源按 `initial_conditions` 定位，不改写封存对象中的路径或指纹。

1. 核对响应的 `status`、`error_code`、`next_required_action`，按返回 ID 读取正式 manifest 和 Cases。
2. 对齐来源及 Case 数量、输入/答案隔离、用途与来源组、指纹、附件和评分资产引用，确认原始文件未被改写。
3. 将核验摘要写入 README，再清理本次生成且已核验、无运行依赖的临时材料；核对清理结果和保留引用。
   用户原始文件、既有数据、附件、正式对象及恢复必需材料保持原样。清理失败如实说明，不称已清理。

失败或中断时不按成功清理，只按需保留一套恢复/诊断必需材料，并在 README 记录原因与位置；
问题解决且核验通过后再清理，不积累重复正文或多轮现场。以上收尾不运行 Agent，也不替代 atk-eval 的评分器校准。
