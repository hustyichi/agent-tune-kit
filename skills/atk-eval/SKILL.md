---
name: atk-eval
description: 按明确且经过校准的评测标准运行、导入或重新判定 Agent 证据。
---

# 评测证据

阅读 `../WORKFLOW.md`，遵循其中默认中文的产物规则。明确选择 `run`、`import` 或 `reassess`。
配置采集时检查各 Record 的 `capture.status/missing/issues`，并通过 `view_execution` 核对声明阶段。
执行完成与日志完整分别判断；缺失证据只影响需要它的维度。原生审计通过现有批次引用读取，不重复导入。
运行前先配置 runtime。`concurrency` 使用 Round 计划冻结的正整数，默认 1。
默认 runner 限制活跃 attempt 数，并随完成写入结果。中断后检查 `running_record_ids`、`completed_record_ids` 和
`not_started_record_ids`；仍兼容旧单运行适配器。Case 附件缺失或改变会阻断重放，需导入新 Dataset；已冻结时另建 Round。
每次 attempt 有独立输出目录，会话和外部写入隔离仍由目标 Agent 提供。

运行调用 `run_evaluation`，指定 Dataset ID、Case ID、Revision ID、Round ID（已冻结时）、重复次数、冻结的
`timeout_seconds`、用途及 `phase=incremental/final`。最终验证必须覆盖冻结两侧，各运行一次；候选工作前预留所有 attempts。
关联外部修复 Round 仅在新 B0 上运行 `phase=external_fix`，包含原受影响及保护 Case。
直接组件检查是另一个获准的 `diagnostic_probe`，需通过的 Assessment，不能混为端到端运行。

获准的基础设施重试提供 `retry_batch_id` 和失败的 `retry_execution_ids`，保持相同 Round、Revision、Dataset、用途和阶段。
新批次保留全部已有 Execution，并追加关联重试。不得重跑已完成的 Agent 失败，也不得从已有后继批次的旧批次分叉。
由目标项目的 Python 执行 `.atk/adapters/runner.py`，每次 attempt 有独立 Execution ID。
使用 `continue_batch_id` 前先检查部分或中断批次：已确认结果沿用，只有未开始槽位自动重跑。
未知槽位必须有 `unknown_execution_authorization`，记录 `source=user`、前驱批次 ID、精确未知 Record ID、原因和风险。
续跑消耗新执行预算，不改前驱证据。检查部分输出、runner 退出状态、实际组件指纹、批次前后服务版本、身份失败和加载证据。
诊断探针需冻结的 `probe_authorization_id`，匹配命令、runner、Case、隔离记录及独立预算；它不是正式效果证据。
明确配置 runtime 后，仅分析 Round 可在无 Git B0 情况下以 `revision_id=null` 运行探针。
已封存未决候选可临时重放记录的父版本或 B0；再次运行前先恢复被中断的切换。
重放旧检查点时进程中断，按操作 ID 调用 `inspect_or_recover_operation`。
恢复只还原已知源码内容并重新执行冻结准备；未知修改需检查。

导入调用 `import_evidence`：批量结果使用 `source_kind=batch_results` 和明确字段映射；
Langfuse 使用 `source_kind=langfuse`，并选择 `adapter_profile=langfuse_trace_bundle` 或 `langfuse_observation_rows`。
提供来源命名空间、映射版本、过滤范围及额外脱敏键。支持 JSON、JSONL、CSV 和 `.gz`。
不要推断缺失的 Trace 父项、最终输出或总体线上成功率。
Trace、Observation 和 Score 分文件时，用 `file_roles` 逐文件分类为 `trace`／`observation`／`score`。
CSV 嵌套字段在 `json_columns` 声明；来源字段名变更在 `mapping.trace`、`mapping.observation` 或 `mapping.score`
按 `{canonical_name: source_name}` 声明。仅在唯一识别最终根 Observation 时使用 `mapping.root_observation_name`。
`reassess` 不重新运行 Agent。

评分前定义有版本的 `evaluation_spec` 和 `judger`。硬约束用确定性检查，语义判定由当前 Codex 会话完成。
正式评测分别记录执行证据可信性、组件契约或产物检查、任务效果维度。
只有明确规则允许时才标为 `not_applicable`；证据缺失标为 `unknown`。
没有可信预期结果时，判定独立可观察维度；无法判定的任务成功维度保持 `unknown`。
输出存在或导入分数本身不证明任务成功。
新规则或规则变更需用确认的正反示例校准，未解决则标为未校准。
向 `store_assessment` 提交每条记录、每个维度的完整判定行，每项引用 `{batch_id,evidence_id}`。
CSV 是评分明细唯一权威来源。新标准创建新 Assessment，并使旧比较失效；不要原地修改旧 Assessment。
需要时用 `render_assessment_html` 从封存 CSV 重建已转义的本地视图，不能把 HTML 当作独立评分来源。
