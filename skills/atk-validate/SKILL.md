---
name: atk-validate
description: 在冻结 Case 和标准上比较精确父版本与候选，或比较 B0 与累计 Revision。
---

# 验证版本

阅读 `../WORKFLOW.md`，遵循其中默认中文的产物规则。
增量验证在 Issue 目标 Case、保护 Case 和此前修复 Case 上评测封存候选及其父版本。
首次 final 运行自动进入 `finalizing`；使用已有最终证据比较时，先调用 `transition_round: start_finalizing` 并记录原因。
此阶段不得有未决候选，也不得创建新候选或启动增量运行。
最终验证在完整冻结 Case 集上比较 B0 和精确当前已采用 Revision，两侧均用冻结重复计划及 `phase=final`。
用 `run_evaluation` 在原目录重放已知提交，运行后恢复开始时检查点。
仅在固定输入／环境允许时重新判定已有执行；不挑选最好重试，也不遗漏未知项。

业务 Skill 两侧都需加载绝对路径和指纹，或有来源链的隔离副本加载证据。
同时检查实际组件身份、runner、固定上下文、Case 内容、判定校准及配对重复槽位。
应用冻结的质量或效率目标和指标限制；必需指标缺失时为 `insufficient`。成本包含所有 attempts 及重试。
成本和工具调用门禁需冻结项目的独立 `metric_sources` 声明，并在每条 Record 中匹配来源。
默认 runner 的 Agent 自写 `metrics.json` sidecar 不可信，不能满足正式门禁。

用准确 Assessment ID 和父版本／B0 提交调用 `compare_and_gate`。
增量目标集来自封存 Candidate 的主 Issue；请求指定其他 Issue 会被拒绝。
两侧判定全部冻结必需维度，使用相同正式超时；比较时不得更换维度。
关联外部修复 Round 调用 `validate_external_fix`，提供新 B0 Assessment、通过的直接探针 Assessment，
以及覆盖冻结直接组件许可所有 Case 和重复次数的证据引用。
许可规格的每个维度都必须通过，除非许可已冻结更窄的必需集；请求不能临时指定直接检查维度。
此验证 Round 不创建 Agent Candidate，也不与旧 B0 比较。

报告 `pass/no_effect/regression/insufficient/invalid`，说明修复、退化、稳定和未知 Case。
随机或未知 Agent 的单次探索改善不是最终证明。此阶段不修改候选、不创建提交。
