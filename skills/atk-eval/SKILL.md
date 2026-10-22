---
name: atk-eval
description: 分开完成准备、校准、执行和评分，按 Case 核验运行证据后报告批测状态。
---

# 评测证据

阅读 `../WORKFLOW.md`，遵循其中默认中文的产物规则。明确选择 `run`、`import` 或 `reassess`。

## 四个阶段分别报告

`run` 依次记录准备、校准、执行、评分四个阶段。校准、数据准入、自检、导入和评分都不能替代目标 Agent 批量执行。
用户只调用本 Skill 且没有指定分支时，已有 runtime 和 Dataset 默认走 `run`；不要自行降为仅校准后收尾。

1. **准备**：确定 Dataset、Case、Revision、重复次数、并发与超时，调用 `preflight_evaluation`。
   一次展示返回的全部 `blockers` 和 `not_checked` 项，给出报告路径及实际启动数（预检为 0）。
   它只检查本地条件，不调用 Agent、服务版本命令、不切换 Revision、不预留预算。
   外部副作用保护需有真实依据；不得自动添加虚假的 stub 或保护记录来消除阻塞。
   Case 附件变化需新 Dataset，已冻结时另建 Round；运行配置与冻结条件不符也先修复。
2. **校准**：按后文定义有版本的 `evaluation_spec`、`judger` 和确认的正反示例。
   可把二者随预检请求提交，得到独立的 `calibration` 状态；未提供标为 `not_recorded`。
   准入或 checker 单测只能支持对应规则的校准，不能计入 Agent 执行数量。
   未校准不禁止单独取证，但不能宣称已完成正式评分或进入比较门禁。预检的 `calibration_issues` 单独报告。
3. **执行**：调用 `run_evaluation`，它会重新预检，避免沿用过期的 ready 结论。
   指定 Dataset ID、Case ID、Revision ID、Round ID（已冻结时）、重复次数、冻结的
   `timeout_seconds`、用途及 `phase=incremental/final`。轮外运行提供明确 `revision_commit`。
   `concurrency` 用冻结的正整数，默认 1。每次 attempt 有独立目录，外部会话隔离由目标 Agent 提供。
   最终验证覆盖冻结两侧，各运行一次；候选工作前预留全部 attempts。
4. **评分**：先核对执行证据，再调用 `store_assessment`。对未执行槽位不得补造输出或判定为业务失败。
   `inspect_evaluation` 带 `batch_id` 和明确的 `assessment_id` 核验评分覆盖；不按最新目录猜测 Assessment。

## 执行证据与完成条件

每次预检保存在 `.atk/preflights/<id>.json`；受阻不创建伪 Execution 或 EvidenceBatch。
错误响应的 `artifact_refs` 仍返回报告。执行后失败的报告还给出实际批次目录，必须保留已落盘现场。
正式执行证据位于 `.atk/evidence/<batch-id>/`：`request.json` 是 Case 与 attempt 的计划，`batch.json` 是运行中状态，
`records.jsonl` 保存结果，`execution-summary.json` 是封存时的阶段和逐 Case 摘要。
`attempts/<execution-id>/` 保留 `input.json`、`attachments.json`、脱敏的 `stdout.log`、`output.txt`、
原生日志或 `events.jsonl`，以及目标 Agent 写入的实际产物；runner 日志位于批次根目录。
配置 `observability.logs/artifacts` 收集原生文件，要求 adapter 把工作区产物与轨迹存入本次 attempt 的 `{output_dir}`。
外部路径不会自动归档；至少声明真实 `required_stages`，或把原生轨迹声明为必需的 `trace/trajectory/events` artifact。
生命周期事件和 stderr 兜底只说明 attempt 运行，不能声称有完整 Agent 轨迹。
超大行、解码失败、缺少必需日志和超时都会留缺口；不得隐瞒或补造内容。

每次报告前用 `inspect_evaluation` 重新核对记录、计划、配置和文件指纹；不要仅信任旧摘要。
结合 `case_statuses`、`capture.status/missing/issues` 与 `view_execution` 检查每个 Case 的状态和引用。
只有返回 `batch_complete=true` 才可说“批测执行与证据核验完成”；`evaluation_complete=true` 才可说正式评测完成。
`sealed` 仅表示批次已封存，CLI `status=ok` 只表示内部操作返回成功，二者都不等于批测完成。
Agent 正常结束但业务失败仍是已执行，是否通过由 Assessment 决定；基础设施失败、超时、未知或未开始槽位不得报完整。
评分可以针对已有部分证据进行，但不能让不完整批次变成完整。证据缺失仅影响依赖它的判定维度。
收尾明确四阶段状态、计划/启动/确认数量、阻塞项、逐 Case 摘要及证据目录，禁止把校准结果叫作批测结果。
正式证据、预检与失败报告均保留，不按 init/dataset 临时文件规则清理。

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

授权重试保留所有历史 attempts；完成条件按每个 Case／重复槽位的最后一次授权执行核对，旧失败在逐 Case 摘要中标为 `superseded`，不改写为成功。
