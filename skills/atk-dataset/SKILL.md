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
