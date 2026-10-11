---
name: atk-decide
description: 提交已验证候选、恢复未采用候选、回退已采用后缀，或完成一轮调优。
---

# 决策与检查点

阅读 `../WORKFLOW.md`，遵循其中默认中文的产物规则。
识别准确 Round、Candidate、可用 Validation 和冻结授权。
`decide_candidate` 的 `keep` 要求匹配且通过的增量 Validation，或明确记录的试用例外（用户来源、动作、原因和风险）。
它仅暂存封存路径，并核验提交父节点、变更路径、trailers 和内容。
`reject` 或 `defer` 保留证据，恢复候选父内容，不创建失败提交。
无 Validation 时同时记录 `reason` 和 `validation_missing_reason`；已有 Validation 必须引用。
重试前用 `inspect_or_recover_operation` 检查中断操作。
未封存草稿且工作区已无修改时，用 `cancel_draft` 记录原因，保留取消记录并释放未决槽位，不返还预算。
不得对草稿调用 `decide_candidate`。

所有候选决策后，`finish_round: complete` 必须有匹配本轮、冻结计划、B0、current Revision 和最终提交的通过 final Validation。
`complete_with_override` 记录明确用户例外，保留失败或缺失的 Validation，不表述为正常完成。
最终门禁失败时，按冻结规则 `rollback_to` B0 或更早的已记录检查点，或暂停等待明确人工例外。
回退创建普通恢复提交，撤回已采用后缀，不重写 Git 历史。
暂停用 `transition_round: pause` 并记录原因；后续先恢复中断操作，再用 `action=resume`。
暂停保留候选，工作区检查通过前不能继续；不得手工改写 Round 状态。
没有采用候选时用 `close_without_adoption`，不创建空提交；无 Git 或 B0 的未冻结仅分析 Round 也可如此关闭。
报告已有 B0、所有采用／拒绝候选、最终提交和限制。不得 push 或部署。

关联外部修复 Round 只有通过外部修复 Validation 后才可 `finish_round: complete_external_fix`。
随后为原 Issue 追加 resolved 修订，包含关联 Round、组件身份、直接证据引用和 Validation ID。
