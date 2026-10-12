---
name: atk-diagnose
description: 调查证据、区分责任层，并通过有限检查记录问题。
---

# 诊断问题

阅读 `../WORKFLOW.md`，遵循其中默认中文的产物规则。从准确的证据位置开展调查。
Python 本地执行先用 `view_execution` 定位评分差异对应的 attempt，再用 `read_native_evidence` 查看原生审计。
保留事件 ID、原生行号和执行关联，避免另写摘录、映射表和重放样本来重复已有事实。
只有原始轨迹无法回答明确的竞争解释时才提出探针；未观察到的阶段保持未知，不能补写为原始事件。
区分首个可观察故障与下游症状，核对实际组件身份、输入、契约、原始响应和 runtime 交接。
比较会改变修复位置的可信竞争解释。优先检查源码／契约；只有命令、隔离和预算获准时才运行有限 `diagnostic_probe`。
探针不作为原始执行，也不作为效果验证。

用 `record_source_contract` 保存小范围项目本地源码或契约摘录，指定组件、源码 Revision、准确行号及单独核验的产物身份。
含秘密的行会被脱敏；源码版本本身不能证明运行二进制或远程服务版本。
在冻结计划（冻结前用 `analysis_plan`）中记录每个探针许可：ID、命令 argv 及 `command_hash`、runner 指纹、
允许的 Case ID、`working_directory`、`timeout_seconds`、`max_calls` 和已核验的 `isolation_ref`。
命令调用诊断脚本时，将脚本放在 `.atk/probes/`，记录相对此目录的路径及 `script_sha256`。
ATK 在执行前及执行期间核验指纹。运行请求引用冻结许可 ID；请求临时提供的命令变更会被忽略。
诊断记录包含 Issue、竞争解释、检查目的、初始状态、目标组件、原始证据和重放限制。
修复后直接检查还需在许可中冻结 `kind=direct_component`、修复后的 `component_identity` 和 `evaluation_spec_hash`。
命令必须直接调用组件，不能调用项目端到端 Agent。许可的 Case ID、`required_repeats` 和可选 `required_dimensions`
定义完整直接检查门禁；缺失、未知或失败的必需行均不能支持组件已修复结论。
设置独立 `budget.probes`，默认零。隔离引用只记录已检查设置，不为目标 Agent 创建操作系统沙箱。

用 `store_diagnosis` 记录一个或多个 Issue，包含症状、支持与反向证据、状态为
`not_run/completed/failed/inconclusive` 的检查、机制引用、干预 Validation 引用、责任组件、责任层、关联 Case、
处理方式及下一步。责任层为 `skill`、`prompt`、`agent_code`、`tool`、`retrieval`、`runtime`、`dataset_or_judge` 或 `unknown`。
`supported` 机制要求直接证据和已处理的竞争解释；干预有效是独立结论。

范围外组件记录本地 `external_handoff`，包含触发输入、预期／实际、契约、组件版本、最小复现和修复后的端到端回归要求。
不要发送交接或修改其他仓库。服务修复后必须关联新 Round 并重新验证，才能解决原 Issue。

用 `store_knowledge` 保存可复用结论，记录输入适用性、组件／契约／judger 指纹、支持与反向证据及 Candidate／Validation 引用。
导入任务证据时，在每个证据引用上记录相当于 Case 的 `source_group_id`；本地 Execution 引用从封存 Dataset 推导。
使用 holdout 来源组的知识会使该组失去独立 holdout 资格。
复用前，按当前身份及输入适用性调用 `knowledge_applicability`；`needs_revalidation` 仅是调查线索，不是已验证结论。
只引用已存在的 Candidate 和 Validation ID；`validated_in_scope` 需支持证据或 Validation，`contradicted` 需反向证据。
