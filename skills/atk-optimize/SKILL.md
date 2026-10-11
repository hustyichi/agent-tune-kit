---
name: atk-optimize
description: 在当前已采用的 Git 检查点上准备并封存一个范围明确的本地 Agent 候选。
---

# 优化一个问题

阅读 `../WORKFLOW.md`，遵循其中默认中文的产物规则。
Round 未冻结时，先记录干净 B0、`dataset_id`、保护／允许路径、组件身份、校准评分、目标／保护 Case、最终重复次数、
有限预算、提交授权和回退规则，再调用 `create_round` 和 `freeze_round`。
被阻塞的范围外 Issue 不能变成本地修复。冻结 Issue 阻塞关系及获准 workaround ID。
workaround 是 `change_kind=workaround` 的新 Candidate，仍需通过门禁，且外部 Issue 保持未解决。
不得修改 ATK 流程、评分器、Dataset 或 runner 来通过门禁。
候选工作前冻结 `replay_preparation`，使旧 Revision 可在原目录重建缓存与产物后运行。
有限执行预算必须保留最终验证两侧；`budget.candidates` 限制草稿数，未授权时 `max_retries_per_slot` 为零。

`prepare_candidate` 前记录主 Issue 及目标 Case，冻结目标集必须包含在该 Issue 中；关联 Issue 也必须有已记录修订。
调用时指定准确主 Issue 和声明的文件路径。在原工作区修改已有 Agent 的业务 Skill、Prompt、代码或配置，
每个候选聚焦一个主要机制。修改后调用 `seal_candidate`，核对返回的变更路径和 Revision，封存后不继续修改。
在 `atk-decide` 决策前，不提交、不 push、不叠加另一个候选。
同一 Issue 再次修改时，新建 Candidate，用 `supersedes` 引用已决策的封存候选，分别封存并验证新内容。
放弃未封存草稿时，先将文件恢复到父检查点并检查部分封存产物，再用 `cancel_draft` 记录原因。
它释放未决槽位，保留草稿和已消耗的候选预算。
