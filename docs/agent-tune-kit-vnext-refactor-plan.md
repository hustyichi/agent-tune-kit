# Agent Tune Kit vNext 正式改造方案

> 文档版本：1.2<br>
> 整理日期：2026-09-27  
> 项目：`hustyichi/agent-tune-kit`  
> 版本性质：Breaking change，不兼容旧 Skill 名称、运行目录和产物协议。  
> 交付形态：本地 Codex 插件；用户通过 Skill 操作，当前 Codex 会话承担推理，本地确定性工具承担执行与状态管理。  
> 本文用途：作为产品边界、开发实现和验收的共同依据。本文描述的是目标设计，不表示相关能力已在仓库实现。

---

## 1. 核心决策与实施边界

### 1.1 已确定的产品定位

Agent Tune Kit，以下简称 ATK，是面向**已有本地 Agent 的通用调优工具包**，不是单独的 Skill 优化器，也不是 Agent 项目生成器。

同一流程应支持业务 Skill、Prompt、Agent 代码及配置等资产的优化。一期重点打通业务 Skill 场景，同时通过至少一个非 Skill 场景证明核心实现没有绑定 `SKILL.md`。

ATK 自身的调优流程 Skills 与目标 Agent 的业务 Skills 必须分开：前者描述如何开展调优，后者才可能是本轮允许修改的资产。正常调优不得修改 ATK 自身的工作流、评分规则或验证器来使候选通过。

### 1.2 一期必须交付

| 编号 | 决策 |
|---|---|
| D01 | 保留 Evaluation、Intelligence、Optimization、Governance 四个核心域；它们是包内职责，不拆成独立服务。 |
| D02 | 仅提供七个公开 Skill：`atk-init`、`atk-dataset`、`atk-eval`、`atk-diagnose`、`atk-optimize`、`atk-validate`、`atk-decide`。 |
| D03 | 当前 Codex 会话负责语义判定、诊断、知识归纳和候选修改；不引入独立模型 Backend。 |
| D04 | Evaluation 同时接收待执行数据集、已有批量执行结果，以及 Langfuse 导出的轨迹文件。 |
| D05 | 目标 Agent 一期以本地可运行为主；Codex 调查既有调用和 Skill 自动加载方式，生成项目本地 runner。 |
| D06 | 一期仅支持 Git 仓库的候选管理；实验从干净的基线 commit 开始。导入证据、判定和诊断本身不强依赖 Git 或可运行 Agent。 |
| D07 | 一轮可以包含多个问题和多个候选；在当前已接受版本上串行累积优化，逐项验证边际效果，最后对比本轮 B0 验收总体效果。 |
| D08 | 直接修改原工程；有效候选经决策后各保存一个本地 Git commit，失败候选封存证据后恢复父检查点。不创建额外 worktree。 |
| D09 | 本轮授权覆盖限定路径内的编辑、候选暂存与提交、受控回退；不自动推送、合并分支、改写既有历史或部署。 |
| D10 | 失败候选可以暂时出现在原工程中，但不得成为下一候选的基线；诊断、补丁、验证与拒绝原因必须保留。 |
| D11 | 不兼容旧版，不保留旧入口别名和隐式迁移；也不得自动删除或覆盖用户的旧版数据。 |
| D12 | 诊断必须区分 Skill／Agent 使用错误、工具／运行时缺陷、评测错误与证据不足；结论升级需有可核查的调查依据。 |
| D13 | 支持范围外问题的本地交接记录及修复后的新轮复验；一期不自动跨仓修改或发送问题。 |

### 1.3 一期明确不做

不实现 `CodexExecHandler`、无人值守调度、后台任务、自动部署、线上持续采集、Langfuse 账号连接器、非 Git 候选管理、worktree 生命周期管理、独立候选池与组合搜索、脏基线自动搬运、跨仓库修改或知识图谱。

允许用户在当前会话中一次授权处理一个有限的问题列表；这属于交互式批处理，不属于后台无人值守。用完本次范围或预算后必须结束，不得自行开展无限搜索。

一轮只在同一个工程目录内串行生成、验证和决策，一次最多有一个未决候选；不同时运行多个优化 Codex 实例，也不与人工编辑或其他修改源码的任务并行。目标 Agent 的样本执行并发可配置，默认值为 1；不同 Revision 的执行不得并发。

本版按 2026-09-27 用户确认的方向，将“独立候选＋组合＋最终写回”替换为“原工程累积修改＋候选 commit＋检查点回退”。Git 操作是实现手段，组件身份、证据、诊断和服务修复后复验契约继续保留。

一期必须实现通用的组件身份、证据来源、诊断检查、责任交接与复验契约。Magic Workspace 等项目的 runner 桥接、MCS／APE Editor 日志映射、构建与重放命令可以二期适配；核心 Schema、状态机和门禁不得写死这些项目名称或枚举。授权内读取相关仓库的源码和契约不等于允许修改该仓库。

### 1.4 本文的规范用语

- **必须／不得**：实现和验收的硬约束。
- **默认**：可由项目显式配置改变；实际值必须写入实验记录，不能在验证中途静默变化。
- **示例**：仅说明含义，不代表预置业务标准或已取得的实验结果。

本文已对未逐项讨论的实现细节给出一期默认值，不再把多个架构选项留给开发 Agent 自行选择。项目特有的入口、数据映射和运行依赖，属于 `atk-init` 或导入时的接入配置，而不是待定产品架构。

---

## 2. 现有仓库的改造基点

当前 README 描述了数据集准备和 Agent 评测调优两条路径，评测主链路为 `atk-init → atk-run → atk-find-failures → atk-report → atk-tune`，并以 `.atk/results/vN/` 组织结果。[S1]

当前 `atk-init` 负责生成项目本地 runner；`atk-tune` 已允许修改 Prompt、代码、参数和工具配置，但没有实现候选隔离、基线管理和正式采用流程。[S2][S3]

因此本次不是重建一个模型运行时，而是重构证据模型、工作流职责和实验管理。现有 runner、日志、CSV、HTML 渲染及安装工具中有价值的确定性逻辑可以提取复用，旧版结果目录和跨轮标题解析不再沿用。

实施开始时必须记录实际开发分支的 commit，并复核文件位置。本文核对的是整理日期可读取的公开文件，不将 `main` 视为永久不变的实现快照。

---

## 3. 四个核心域与公开 Skill

### 3.1 模块边界

| 核心域 | 内部职责 | 公开入口 | 禁止越界 |
|---|---|---|---|
| Evaluation | 数据接入、真实执行、证据标准化、判定器准备、结果评判 | `atk-eval`；配套 `atk-init`、`atk-dataset` | 不根据失败直接修改 Agent；不伪造缺失轨迹和评分 |
| Intelligence | 诊断、根因假设、问题聚合、成功模式、持久知识 | `atk-diagnose` | 不把假设直接写成已验证事实；不直接应用候选 |
| Optimization | 优化计划、范围控制、候选构建、补丁封存 | `atk-optimize` | 不修改评分标准；不宣布自己的候选有效 |
| Governance | 增量／最终验证、候选决策、提交检查、回退与完成记录 | `atk-validate`、`atk-decide` | 不把提交等同于最终通过；不超出授权范围提交或回退 |

### 3.2 原 Skill 处理清单

| 原入口 | 新版处理 |
|---|---|
| `atk-init` | 保留名称，重写为项目接入、运行调查和约束配置 |
| `atk-run` | 删除公开入口；执行能力进入 Evaluation 内部 |
| `atk-find-failures` | 合并进 `atk-eval` |
| `atk-find-failures-by-rule` | 合并进 `atk-eval` |
| `atk-init-failure-rule` | 合并进 `atk-eval`；准备规则后可在同一次操作中执行 |
| `atk-report` | 删除；诊断交给 `atk-diagnose`，对照验证交给 `atk-validate` |
| `atk-tune` | 删除，由 `atk-optimize` 替代 |
| `atk-build-dataset` | 合并进 `atk-dataset` |
| `atk-build-ground-truth` | 合并进 `atk-dataset` |
| `atk-tune-ground-truth` | 合并进 `atk-dataset` |
| `atk-visualize-dataset`、`atk-visualize-failures` | 删除独立入口；可复用的 HTML 渲染成为内部辅助能力 |
| `atk-new-agent` | 删除；测试夹具可有小型 Agent，但不提供新建 Agent 产品能力 |

不新增公开的 `atk-judge`、`atk-setup-judge`、`atk-learn`、`atk-accept` 或 `atk-reject`。

### 3.3 最小使用流程

```text
首次本地接入：atk-init
按需准备任务：atk-dataset

atk-eval → atk-diagnose → atk-optimize → atk-validate → atk-decide
                              ↑                         │
                              └── 下一候选基于已接受 commit ┘

候选处理结束 → atk-validate（当前累计版本 vs B0）→ atk-decide（完成／回退）
```

`atk-decide` 对通过的候选执行 `keep` 并创建 commit，对失败候选执行 `reject` 并恢复父检查点。候选提交是本轮内的检查点；最终 `complete` 还需整轮验证通过。源文件已在原工程内，不再设置单独的组合、合并或最终写回阶段。

---

## 4. 必须统一的术语和对象关系

| 对象 | 定义 |
|---|---|
| Case | 一条任务定义；包含输入以及可选预期结果、文件和环境要求 |
| Execution | 目标 Agent 对某个 Case 的一次真实尝试；不等于评分结果 |
| EvidenceBatch | 一次执行或导入形成的一批证据；可包含多个 Execution 或历史 Trace |
| Assessment | 在固定判定标准下，对一个证据批次进行的一次评判 |
| Round | 一轮调优：聚合一批问题，冻结本地基线和验证约定，管理多个候选直到结束 |
| Issue | 一个有证据支持的问题或能力缺口；一条轨迹可以关联多个 Issue |
| Candidate | 针对一个主要问题提出的一组不可变修改；可以关联其他相关问题 |
| Revision | 可以重建和校验的源文件状态；由已有 commit，或父 commit＋封存的候选修改表示 |
| Validation | 对指定两个 Revision 在固定任务和判定条件下进行的对照结果 |
| Decision | 保留并提交、拒绝并恢复、暂缓、回退检查点、完成本轮等动作及其理由；记录实际 commit |
| Knowledge | 可复用经验、适用范围、证据、反例和验证状态 |

`batch_id` 是 EvidenceBatch 的 ID；`execution_id` 只表示一个 Case 的一次尝试。一个批次可包含多个 Case 和多次尝试，不再定义与 EvidenceBatch 重叠的 Run 对象。诊断探针也使用 Execution，但必须标明用途，不混入正式效果统计。第 7.2 节规定 ID 分配、重复及重试关联。

核心关系：

```text
EvidenceBatch → Assessment → Issues
                          ↓
Round ── 固定 B0 commit
          ↓
        C1 基于 B0 → 通过、keep、commit K1
          ↓
        C2 基于 K1 → 失败、reject、恢复 K1
          ↓
        C3 基于 K1 → 通过、keep、commit K3
          ↓
        最终验证 K3 vs B0 → 完成，或回退已记录检查点
```

Round 保存不可变的 B0 和可推进的 `current_revision_id/current_commit`。Candidate 保存真实父版本；已接受链只有一条，不另建 Integration 对象。

**EvidenceBatch、Execution、Round 和 Candidate 不得复用同一个 ID 或版本号语义。** 一轮中可以有许多批次、许多评判记录和许多验证，但原始基线 B0 不变。

ID 由共享存储模块生成，采用带类型前缀的 UUID 字符串。文档中的 `R1/P1/C1/B0/K1` 是展示别名，不是目录命名算法。当前对象必须通过显式引用读取，不通过最大的目录序号猜测。

---

## 5. Evaluation：三种证据入口

### 5.1 输入与行为

| 输入 | 入口行为 | 是否执行目标 Agent |
|---|---|---|
| 待执行数据集 | 读取任务映射，调用项目 runner，保存实际输出与轨迹，再判定 | 是 |
| 已有批量执行结果 | 导入结果、日志和原始评分，建立样本关联，补充或复核判定 | 否 |
| Langfuse 导出轨迹 | 按导出类型还原 Trace/Observation 关系，整理任务证据，按可支持维度判定 | 否 |

`atk-eval` 内部支持 `run`、`import`、`reassess` 三种动作。用户可用自然语言表达；Skill 解析后将明确动作写入请求，不得仅凭一个模糊 CSV 文件就自行再次调用 Agent。

`reassess` 只生成新的 Assessment，不重新执行 Agent、不覆盖旧 Assessment。

每次正式本地 `run` 开始前都必须绑定一个实际文件 Revision，并保存固定运行条件。后续 Round 只有在该 Revision 与冻结 B0 一致时才能直接采用这次执行为基线结果；不能把“最近一次执行”自动指定为基线。用途为 `diagnostic_probe` 的执行遵守第 7.6 节，不充当正式基线。

### 5.2 三种能力不能混为一谈

每条证据必须记录：

| 能力 | 状态 |
|---|---|
| 可分析性 | `sufficient`、`limited`、`none`，附缺失项 |
| 可判定性 | 按维度列出是否具备判据；不是只有全有或全无 |
| 可重放性 | `ready`、`needs_setup`、`unavailable`，附任务与环境缺口 |

缺 Ground Truth 的轨迹仍可能具备工具契约、结构或安全约束方面的判据。只有没有可靠判据的维度才记为 `unknown`。

历史线上 Trace 默认不是可重放 Case。必须补齐输入、文件状态、工具条件和会话前缀，才能转为可重放任务。

### 5.3 完整执行状态与完整评判状态分开

Execution 终态：`completed`、`agent_error`、`infra_error`、`timeout`、`cancelled`、`unknown`。执行中的中间状态允许 `planned/running`；异常退出后不能判明是否执行完成时以 `unknown` 封存，不能因输出文件存在而写为 `completed`。

Assessment 单维度结果：`pass`、`fail`、`unknown`、`not_applicable`。

例如，目标 Agent 崩溃可以按固定任务标准判为任务失败；评分器崩溃则是评判失败，不能判 Agent 失败。工具调用报错后成功恢复，不自动等于任务失败。

必须记录计划样本数、已执行数、已判定数、缺失数和导入过滤范围。只导入失败轨迹时，只报告该样本集的情况，不计算总体线上成功率。

### 5.4 被测边界、有效性与分母

正式评判必须绑定版本化的 `evaluation_spec`，记录被测组件、固定依赖、指标定义、各维度适用条件、有效尝试条件、错误归属和分母规则。它随判定器冻结，不能从错误字符串或 `infra_error` 自动推断责任。

判定至少分别保存：执行证据是否可信、组件契约／产物检查、任务效果。每个维度有独立的 `validity=valid/invalid/unknown` 和原因；它表示该维度的评测是否成立，不表示任务成功。`validity` 非 `valid` 时，该维度 `verdict` 必须是 `unknown`；不适用维度以明确规则记为 `not_applicable`。

| 情形 | 判定规则 |
|---|---|
| 被测工具处理合法请求时崩溃，证据充分 | 对工具可靠性是有效失败；不能以“基础设施错误”从分母移除 |
| 固定外部依赖不可用，导致无法考察 Skill 能力 | Skill 能力维度无效或未知，服务问题另建 Issue；若另有端到端可用性维度，按其事前规则记录失败 |
| runner、评分器或证据解析器自身出错 | 受影响维度无效或未知，不判被测 Agent 失败 |
| 工具报错后恢复并完成任务 | 工具事件保留；任务是否通过由任务标准决定 |

报告列出计划、适用、有效、无效、未知和未执行数量及原因。描述性成功率可按冻结规则报告有效样本分母，但不得代替完整性门禁。对照使用冻结的同一 Case／重复槽位集合；任一侧必需维度无效或未知时不得各自缩减分母后宣布改善，应按原因返回 `invalid` 或 `insufficient`。诊断定位责任不会自动修改已有 Assessment；重判必须走 `reassess`。

### 5.5 证据定位与来源

每条被诊断或判定引用的事件／附件必须具有批次内唯一的 `evidence_id`，并记录内容指纹、可定位的文件及行／JSON Pointer、来源组件、关联 Execution 或原始 Trace／Observation、采集方式和缺失项。`evidence_refs` 每项固定为 `{batch_id, evidence_id}`，不得只引用无法定位的报告标题；CSV 中按 JSON 数组序列化。导入 profile 负责规范化原始字段，通用存储校验引用与指纹。

`origin` 固定为 `original_execution`、`diagnostic_probe`、`source_contract`、`derived_analysis`。诊断重放保存在新批次，记录原证据引用、实际输入／初始状态、组件版本、时间及与原执行的差异；它不能补写成原执行已经发生的事实。分析摘要引用其上游证据，不能单凭另一份摘要提升根因状态。

涉及调用边界时，分别保存可获得的调用方请求、被调用方实际收到的输入、原始响应、运行时加工后的响应及关联 ID。拿不到某一侧时显式标记未知，不推断两者一致。冻结前／后文件或差异也是证据；工具退出码、最终答复和日志任一项均不能独立替代产物检查。证据内容继续遵守第 6.5 节脱敏要求。

---

## 6. Langfuse 文件适配规范

### 6.1 支持范围

Langfuse 官方 UI 导出支持 CSV 和 JSON，导出会受所选表格过滤条件影响；Observation、Trace、Session 是不同层级的概念。[S5][S6]

一期支持以下本地文件：

| 类型 | 一期支持 |
|---|---|
| JSON 对象、JSON 数组、显式 `data` 数组封装 | 支持 |
| JSONL | 支持 |
| CSV | 支持；仅对已声明为 JSON 的列解析嵌套对象 |
| 上述文件的 `.gz` 压缩形式 | 支持；使用标准库解压，设置解压大小限制 |
| Parquet、直接访问云存储、线上 API 拉取 | 不支持；返回明确的格式或接入错误 |

不宣称“所有 Langfuse 导出都自动兼容”。当前没有用户实际导出样例，开发中必须取得至少一份脱敏的真实文件作为最终接入验收；合成夹具只能证明协议实现，不能代替实际格式验收。

### 6.2 两类解析配置

`langfuse_trace_bundle`：文件提供 Trace 记录，并可内嵌完整 Observations，或关联单独的 Observation、Score 文件。

`langfuse_observation_rows`：文件主要是 Observation 行，按来源项目与 `trace_id` 聚合。需兼容配置声明的 camelCase 与 snake_case 字段名。

读取器先识别确定的结构，再使用保存的字段映射。不明确时由当前 Codex 根据样例生成映射或项目本地转换脚本；保存后由确定性代码重复执行。不允许运行时用大模型“补全”不存在的记录。

### 6.3 最小字段映射

| Langfuse 含义／常见字段 | ATK 内部字段 | 规则 |
|---|---|---|
| 项目或导出来源 | `source_namespace` | 必填；文件中缺少时在导入配置指定，防止跨项目 ID 冲突 |
| `traceId` / `trace_id`；Trace 行自身 `id` | `source_trace_id` | Trace 行与 Observation 行的 `id` 含义必须分开 |
| Observation `id` | `event_id` | 保留原值；不能把重复工具调用合并成一个 |
| `parentObservationId` / `parent_observation_id` | `parent_event_id` | 缺失父节点时记录悬挂关系，不制造父节点内容 |
| `sessionId` / `session_id` | `source_session_id` | 只建立关联；默认不把整个 Session 合并为单个评测样本 |
| `type`、`name` | `event_type`、`event_name` | 保留原始类型；未知类型进入通用事件 |
| `input`、`output` | `input`、`output` | 保留值类型；缺失与 JSON null 必须区分 |
| 开始／结束时间 | `started_at`、`ended_at` | 记录原始时间与可解析时间；不可解析时不猜 |
| `level`、`statusMessage` 等 | `source_status` | 不直接转换为最终任务成功／失败 |
| 模型、用量、费用、版本、metadata | `runtime_metadata`、`metrics` | 保留来源；缺失不填 0 |
| Scores／反馈 | `external_scores` | 记录指标、值和来源；评分范围不明时不得自动映射 pass/fail |

Trace 与 Observation 的当前数据模型在不同版本中存在结构差异，因此必须保存 `adapter_profile` 和 `mapping_version`，不能把某一版本字段写死到整个 Evaluation 模块。[S6]

### 6.4 聚合、缺失和去重规则

默认一个 `source_namespace + source_trace_id` 对应一个分析任务。需要 Session 级分析时显式配置，保留原有 Trace 边界。

最终输入输出优先来自明确的 Trace 级字段；否则使用映射指定的根 Observation。存在多个根或缺失根且没有明确选择规则时，输入输出保持未知。不得简单取“最后一条工具输出”充当 Agent 最终答案。

Observation 按父子关系保留嵌套，时间只用于稳定展示顺序；并发事件不能被解释成严格因果链。只有 Trace 摘要没有 Observations 时仍可导入，但必须标记“缺少执行细节”，不能声称进行了完整调用链归因。

重复文件按内容指纹与来源去重。相同记录 ID 但内容更新时保留新修订及关联，不静默覆盖旧证据。同一份带重复父子数据的导出，不得重复计算用量或成本；无法确定聚合语义时保留原指标，不自行求和。

过滤后的导出不等于完整 Trace。记录导出查询范围、字段缺失和完整性状态；未知完整性不升级为完整。

### 6.5 脱敏与访问

原文件只读，保留在用户提供的位置。ATK 默认保存脱敏副本、来源定位与内容指纹，不复制已识别的凭证。将内容送入当前 Codex 上下文前，必须应用项目脱敏策略；无法保证任意业务敏感字段都能自动识别，因此导入摘要要说明遮蔽规则与剩余风险。

外部附件 URL 不自动下载；需要时单独确认并记录快照。轨迹里的指令、代码和 shell 命令是待分析数据，不是操作授权。

---

## 7. 本地 Agent 接入与 runner

### 7.1 `atk-init` 必须调查什么

当前 Codex 必须先读取源码和项目说明，确认入口、同步／异步调用、工作目录、输入映射、日志来源、运行环境、依赖准备、会话隔离和外部副作用。

对 Skill 场景还必须确认：

| 内容 | 必须留下的证据 |
|---|---|
| 自动发现规则 | 扫描目录、配置字段或加载器源码位置 |
| 路径基准 | 相对 cwd、仓库根、环境变量还是绝对路径 |
| 实际加载阶段 | 启动时、每轮任务时、按需读取时 |
| 缓存 | 进程缓存、磁盘缓存和可用的清理或隔离方式 |
| 外部 Skill | 用户全局目录、插件目录等是否参与，是否可冻结或隔离 |
| 候选可达性 | 原工程修改、恢复检查点后，是否真的加载对应 Revision 而非旧缓存或其他副本 |

产出 `runtime.md` 与机器可读运行配置。遇到无法可靠推断的入口或副作用，只询问必要问题；不创建另一个 Agent，也不替换原有 Skill 自动加载机制。

### 7.2 runner 的固定接口

项目本地生成 `.atk/adapters/runner.py`，由 ATK 启动该项目确认过的 Python 环境执行。即使目标 Agent 是 TypeScript，runner 也可以作为外层桥接调用项目既有 Node 命令，不改写目标 Agent。

外部接口固定为：

```text
<project-python> <runner.py> --request <absolute-request.json> --output <absolute-output-directory>
```

请求必须包含：`batch_id`、`purpose=evaluation/diagnostic_probe`、`revision_id`、`workspace_path`、`cases_path`、显式尝试列表 `attempts`、超时、并发、运行配置引用、输出目录。`attempts` 的每项含 `execution_id`、`record_id`、`case_id`、Case 内容指纹、`repeat_index` 和可空的 `retry_of`。ATK 在启动前分配批次与尝试 ID，样本选择和重复次数展开为此列表；runner 不分配这些 ID、不隐式追加重试，也不查找“最新结果”。

响应包括 `batch.json`、逐尝试 `records.jsonl`、日志／附件引用及实际运行组件清单。每条记录以 `id=record_id` 关联请求，在 `execution` 字段内保存对应 Execution；不得另写一份可独立修改的 Execution 副本。开始尝试前记录启动状态，每个样本完成后刷盘；`batch.json` 列出完成、未启动及中断尝试，最终区分完整、部分和中断。ATK 校验后将批次元数据封存为 EvidenceBatch 的 `manifest.json`。

重复槽位 `repeat_index` 从 1 开始；一次实际调用对应一个新的 `execution_id`。重试生成新 ID 并通过 `retry_of` 引用原尝试，沿用原 Case 与重复槽位，全部尝试保留；按冻结的重试规则聚合，不择优。中断后，仅明确未启动的计划项可继续使用预分配 ID；可能已执行的项不得重用 ID 掩盖重跑。已封存批次的后续尝试进入关联的新批次。

历史导入只有在原始数据足以识别 Case／尝试边界时才建立 Execution；否则保留 EvidenceRecord 与原始定位，相关字段为空并记录缺口，不伪造重放身份。重新评判复用原记录 ID，不制造新 Execution。

Ground Truth、诊断、候选提案、验证答案与 ATK 状态文件不得作为目标 Agent 的业务输入。runner 只传递已映射的业务字段；Agent 能访问哪些测试文件和系统资源必须显式配置。

### 7.3 候选加载的验证要求

必须记录实际 cwd、入口源码路径、可用 Skill 目录及文件指纹，以及能够获得的真实加载／读取事件。`available`、`loaded`、`invoked` 是不同状态，不得混用。

Skill 调优的正式验证必须证明候选处于实际加载路径，并在针对性任务上获得足以确认读取候选的证据。仅证明“候选文件存在”不够。缺少可观测性时可先做不改变行为的路径／读取记录适配；仍无法证明则标记 `loading_unverified`，不能正常通过该 Skill 的有效性门禁。

不得通过把候选 Skill 正文直接拼进用户请求来伪装自动加载。必要的只读观测改造属于运行适配，必须在基线和候选双方一致使用。

### 7.4 防止误加载旧版本与运行污染

检查 Python editable install、`PYTHONPATH`、Node 本地链接、绝对路径和上级目录扫描，确认被测源码和业务 Skill 解析到本次 Revision 的原工程路径；不得误用已发布包、全局副本或旧构建制品。

候选执行、父版本复跑和 B0 最终复跑复用同一工程目录；切换规则见第 12.4 节。每次执行前后核对源码／Skill 指纹，重启目标进程，并按冻结方案重建相关制品、重置缓存与任务状态。实际内容身份以 Revision 和组件清单为准，不能单凭 `HEAD` 推断当前运行内容。

默认每次执行使用新的会话和隔离的任务输出目录；纯依赖缓存可共享，行为相关写缓存不得未经重置复用。目标 Agent 若修改冻结源码、暂存文件或自行创建 checkpoint commit，验证无效并暂停调查；接入时应关闭此类行为或将其作用域限制到任务输出目录。

Git 检查点不能撤销网络、数据库或其他外部副作用。会造成真实外部写入的工具必须使用测试环境、替身或已确认的保护措施；未配置时阻塞相应重放。

### 7.5 实际运行组件清单

`atk-init` 在运行配置中声明组件清单；runner 每批返回观测到的实际清单。组件项至少包含：`component_id`、角色、`change_role=variable/fixed`、预期版本／指纹、实际制品版本／指纹、身份核验依据、可用的源码位置与源码版本、契约引用。未知字段保留为空并说明原因，不用当前源码 HEAD 代替已运行二进制或远端服务的身份。

允许修改的组件由计划列为 `variable`，实际内容必须对应该侧 Revision；其余运行时、工具、模型配置和必要依赖为 `fixed`。源码位置用于调查，修改权限仍由允许路径决定。无需扫描所有传递依赖，但影响本轮指标、重放和责任判断的组件必须纳入；执行前核验、执行后记录漂移。

固定组件身份、契约及初始状态／重置策略进入 `fixed_context_hash`。漂移不得归为候选收益。确实无法冻结的服务按第 11.3 节采用预先约定的配对与漂移规则；涉及根因的关键组件身份仍未知时，只能给出受限诊断，不能声称复现了同版本缺陷。

### 7.6 诊断探针复用 runner

`atk-diagnose` 可以在既有授权和有限预算内，通过同一 runner 的 `purpose=diagnostic_probe` 执行项目测试、直接工具调用或最小重放。请求关联 Issue、竞争解释、检查目的、输入／状态、目标组件及原证据；没有本地源码 Revision 时可为空，但必须记录实际制品身份和重放局限。此类结果不可用作正式候选对照。

探针按普通 EvidenceBatch 脱敏、刷盘、封存和引用；不新增后台执行器或公开 Skill。执行前保存命令参数数组、工作目录、脚本指纹、超时、隔离方式和最大调用数；诊断脚本放在 ATK 管理目录，不改被测源码、评分规则或正式产物。需要改变冻结接入配置时，遵守新轮规则。

已知有副作用的探针使用隔离副本或测试环境。缺少安全条件、调用入口或预算时，记录 `not_run` 和具体缺口，不把未执行的检查写为通过。只读源码／契约检查可直接形成证据，无需为了流程完整而调用 Agent。探针失败和无结论结果同样保留。

---

## 8. 统一评判：准备规则与执行规则合成一次操作

### 8.1 内部流程

```text
已有证据 → 确定标准 → 准备／复用判定器 → 执行判定 → 校验结果 → 保存 Assessment
```

一期支持确定性规则、当前 Codex 语义判定及组合判定。规则可以来自已有测试、字段比较、Schema 检查或 Codex 根据明确业务标准生成的项目脚本。

用户提出“按这条规则评判这批结果”后，`atk-eval` 在同一入口内完成脚本准备、检查和执行，不再要求先调用规则初始化 Skill。

### 8.2 评分标准冻结

保存 `judger_version`：包含规则代码、语义 rubric、维度聚合规则和已使用预期结果的内容指纹。

现成 Langfuse Scores 作为 `external` 证据保存。只有明确其任务粒度、方向、范围和阈值后，才能映射为内部标准；不同口径的分数不能直接比较。

同一轮的候选对照必须使用相同标准。标准修订后新建 Assessment，旧验证不能自动继续有效。只改变判定标准时可以对既有原始执行结果重新评判；任务输入或执行条件改变时必须重新执行。

新增或修订判定器用于正式门禁前，必须以少量人工确认的正例、反例及适用边界样本校准，保存样本来源、预期判定、实际判定和差异。`judger_readiness` 使用 `calibrated/uncalibrated`；可复用身份匹配的已有校准证据。没有可靠预期或仍有影响门禁的未决差异时，Assessment 可用于探索但标记 `uncalibrated`，不得正常通过效果门禁。校准材料与测试答案不进入被测 Agent 的输入。

### 8.3 当前 Codex 判定的边界

不启动独立模型、子 Codex 或 `CodexExecHandler`。Semantic judge 的输入应限于任务、固定标准、必要证据和实际输出，不把优化提案中的自我解释作为判定依据。

同一会话参与修改和评判不等于独立盲评；报告必须标明判定方式。优先用程序验证硬约束，重要语义边界按计划人工复核。不得声称本方案已消除评判偏差。

### 8.4 权威产物

Assessment 由 `manifest.json` 和不可变的 `assessment.csv` 共同构成：前者保存证据、`evaluation_spec`、判定器／校准引用及 CSV 指纹；后者是逐记录／逐维度判定的唯一权威明细，不另存一份独立可编辑的 JSON 判定表。

CSV 最低列为：`record_id`、`dimension`、`validity`、`validity_reason`、`verdict`、`score`、`reason`、`evidence_refs`、`judger_kind`。`record_id + dimension` 在一个 Assessment 中唯一，通过 EvidenceRecord 定位 Execution；需逐事件评判时，细分证据仍由 `evidence_refs` 引用。

`score` 可空；`evidence_refs` 为 JSON 数组字符串。`store_assessment` 接收结构化判定，经字段、引用、唯一性和覆盖校验后写入 CSV，manifest 的计数必须从明细计算。汇总 CSV、`comparison.csv`、失败清单、摘要和 HTML 均为派生视图。文件指纹不符时拒绝使用，不通过修改 manifest 接受手工改分。

---

## 9. Intelligence：一个诊断入口，输出问题集合和知识

### 9.1 诊断流程

`atk-diagnose` 不强依赖已经判为失败的样本。输入可以是 Assessment、未判定但异常的轨迹、用户反馈、成功样本和已有项目知识。

它必须区分观察事实与解释假设。Issue 至少记录：主要症状、受影响记录、根因假设、竞争解释、支持证据与反证、调查步骤、验证状态、建议干预层、责任组件、适用范围、优先级和待确认因素。

建议干预层使用：`skill`、`prompt`、`agent_code`、`tool`、`retrieval`、`runtime`、`dataset_or_judge`、`unknown`。允许一条问题关联多个层，但必须指出本轮允许改变的层。

调查按以下顺序进行；已有充分证据可复用，不机械要求每步新跑实验：

1. 将症状定位到具体尝试和证据位点，区分首次可观察异常与下游连锁结果；时间顺序不直接当作因果。
2. 核对当时的输入、实际组件身份、适用契约、状态和输出；把日志缺失与实际执行失败分开。
3. 列出会改变修复位置的合理竞争解释及各自可观察预测，不为凑数量虚构解释。
4. 优先核对源码／契约和已冻结证据；仍有歧义时选择能区分解释的最小检查，例如直接重放同一工具输入，或比较工具原始响应与运行时转交响应。
5. 记录检查的预期、实际、证据引用和解释更新，给出可支持的责任范围、下一步及停止原因。

“Skill 规则错误”“Agent 未遵守规则”“工具不支持请求”“工具违反已支持契约”和“运行时传递错误”不得合并成一个能力缺口。只证明请求失败而未核对输入合法性与契约时，不能归责工具；只证明候选有效而未验证机制时，不能宣布根因唯一。

### 9.2 多根因聚合规则

相同症状不自动合并为同一根因；同一根因在多条轨迹中出现应聚合。一个样本可以关联多个 Issue，不能为了计数把多因问题强制拆成互斥标签。

根因状态使用下列最低证据要求，不按模型自报置信度自动升级：

| 状态 | 允许的结论与最低依据 |
|---|---|
| `hypothesis` | 有症状和证据支持的待检验解释；必须列出待区分因素或下一项检查 |
| `supported` | 版本／契约对应的直接证据或已执行检查支持具体机制，并对会改变责任归属的竞争解释给出排除依据；剩余局限明确 |
| `intervention_supported` | 有引用到精确候选／累计 Revision 与通过 Validation 的干预收益；只支持已记录范围内的干预有效性，不表示机制或唯一根因已证实 |
| `inconclusive` | 证据冲突、检查不可执行或预算耗尽，尚不能区分关键解释；必须列明缺口和最小下一步 |

机制证据保存于 `mechanism_evidence_refs`，干预收益保存于 `intervention_validation_refs`；两者可同时存在，`intervention_supported` 不高于 `supported`，也不替代其依据。状态更新保留前一版本和引用。缺少规定字段或引用的升级请求由 `store_diagnosis` 拒绝；证据是否真正支持结论由当前 Codex 按上述流程判断，关键责任边界可按计划人工复核，结构校验不能冒充语义核验。

每个 Issue 的针对性回归样本必须能引用回 Case 或原始证据。不可重放证据可支持调查，但不能直接参加执行对照。

### 9.3 知识的轻量持久化

不新增 `atk-learn`。`atk-diagnose` 负责整理经验；共享 KnowledgeStore 保存条目，Governance 通过同一接口补充验证和拒绝反馈。

知识状态：`provisional`、`validated_in_scope`、`contradicted`、`retired`。条目包含适用条件、证据、相关候选和反例。不得将失败候选关联的所有根因一并判错；也不得因候选通过就把所有解释升级为事实。

用户明确确认的长期目标存入 `context.md`；自动归纳知识存入 `knowledge/`，两者分开。

每条知识必须保存其相关组件／契约、判定器和适用输入条件的引用与指纹。检索时比较本轮清单；有相关变化或身份未知时，在本轮标记 `needs_revalidation`，仅作为调查线索，不继承旧的已验证结论。该标记不改写历史知识状态；复核、反证或退役形成关联的新修订。曾进入 holdout 的材料一旦用于知识或候选构建，即记录其已参与优化。

### 9.4 范围外问题交接与修复后复验

Issue 的调查状态与处理去向正交。`disposition` 使用 `local_candidate`、`external_handoff`、`judge_revision`、`needs_evidence`、`no_action`，并记录理由；未经证实的工具猜测应走补证，不冒充已确认服务缺陷。

对于责任组件在本轮允许路径之外的问题，在已有 Issue 内保存交接字段：责任组件及源码／制品身份、触发输入和初始条件、预期／实际、契约与证据引用、最小复现步骤及执行状态、受影响 Case、建议修复位置、修复验收和端到端回归任务。无法取得某项时保留缺口；交接只是本地产物，不自动向外部系统发消息或建单。

暂停该问题的候选生成，并通过 `blocked_by_issue_ids` 阻止依赖它的候选正常提交为下一检查点；其他没有依赖且满足冻结门禁的问题可继续。已经冻结的必需样本不能因阻塞而删除；需缩减实验范围时另开 Round。经授权的临时规避必须新建 Candidate，标为 `change_kind=workaround` 并关联未解决 Issue；需证明该候选能在既定条件下绕开阻塞并完成完整门禁，才能免除它对该 Issue 的阻塞依赖，不能解除其他候选的依赖。规避通过只证明规避有效，不关闭服务缺陷。

外部修复完成后登记修复制品／源码身份与相关证据，旧轮保持暂停或关闭，建立关联的新 Round 和新 B0；重跑最小复现、原受影响任务及保护集。服务自身修复验证与 Agent 端到端复验分别记录，前者通过不能替代后者。只有约定复验通过才能把 Issue 处理状态置为 `resolved`；候选已提交、收到修复说明或采用规避均不等于解决。

评分器误判走独立的判定器修订和校准流程，不通过 Agent 候选改变评分规则。修订后保留原 Assessment，在新轮对双方旧执行统一重判；输入或执行环境变化时再重跑。新建轮必须引用原 Issue／Round，不能继承旧 Validation 的通过结论。

---

## 10. 一轮多问题、多候选的核心流程

### 10.1 固定起点、累积优化、逐项提交

一期采用一条串行优化链。B0 是本轮开始时的基线 commit，整轮不变；`current_commit` 是当前已接受检查点，初始等于 B0。每个候选在当前检查点上修改原工程，验证相对父版本的边际效果。

```text
B0
 └─ C1：编辑 → 封存 → 对比 B0 → 通过 → keep，提交 K1
      └─ C2：编辑 → 封存 → 对比 K1 → 退化 → reject，恢复 K1
      └─ C3：编辑 → 封存 → 对比 K1 → 通过 → keep，提交 K3

最终：K3 对比 B0，完成预先冻结的完整验证
通过 → complete，保留已有提交
未通过 → 按计划恢复 B0，或验证选定的更早检查点
```

候选验证通过并不自动推进检查点；只有提交成功且提交内容与被验证 Revision 一致，`keep` 才完成。失败或暂缓候选不能成为后续候选的隐含父版本。没有独立候选保留池，也不需要再次拼装修改。

C3 的收益是“在 K1 基础上新增的效果”，不能报告为“C3 独立相对 B0 的效果”。最终总体收益直接用最终版本对比 B0 计算，不将逐项修复数相加。

### 10.2 一轮的冻结条件

在第一个候选生成前，必须冻结：基线 B0 commit、当前分支、可编辑路径、保护路径、运行适配版本、运行组件清单及变量／固定角色、模型与环境约定、`evaluation_spec` 与已校准评分标准、验证样本与分组、分阶段重复／重试计划、效果门槛、本次处理范围、候选提交授权和最终不通过时的回退规则。诊断可先于冻结进行，但其预算和允许的检查必须先记录。

导入线上轨迹时可以先创建 `analysis_only` 的 Round。需要生成候选时，再绑定本地 B0 和验证任务，转换为可实验状态。历史线上版本不可自动视为当前本地 B0；针对历史问题，应先确认当前基线是否仍能复现或仍存在相同机制。

如果必须改变评分标准、被测边界、候选范围或被冻结的运行适配／固定组件，暂停或关闭当前实验并创建新 Round，引用旧证据和候选；不得偷偷改写本轮对照口径。只改判定标准时允许在新轮统一重判双方原始执行，不要求无意义地重新运行 Agent；所有正式对照重新生成 Validation。

### 10.3 候选拆分与替代方案

一个 Candidate 默认对应一个主要 Issue，可同时关联其他影响问题。不能把一轮所有修改放进一个候选再声称完成逐项归因。

同一个 Issue 的替代方案也串行处理：前一方案被拒绝并恢复父检查点后，才生成下一方案。若前一方案已经接受，后续改进就是在该方案基础上的新候选；要从其他父版本重新试验，先走显式检查点回退，不能暗中切换基线。

同一候选封存后内容不可修改。调整规则或修复实现必须创建新 Candidate，以 `supersedes` 关联旧版，并消耗新的候选预算；旧 Validation 不继承为新内容的结果。

### 10.4 父版本就是累积依赖

每个候选保存 `parent_revision_id` 和 `parent_commit`，必须对应创建时的当前已接受检查点。它自然携带父版本中的全部修改；一期不另设 `depends_on` 依赖图或自动组合搜索。

回退到更早检查点时撤回的是整个后缀。例如 K3 基于 K1，不能移除 K1 后直接沿用 K3 的评分。需要保留后续思路时，在回退后的父版本上创建新候选并重新验证；不自动 cherry-pick、重排或重放后缀。

`blocked_by_issue_ids` 继续表达尚未解决的服务／运行条件阻塞，与这条源文件版本链分开。

### 10.4.1 当前会话的有限计划

本轮计划记录 `selected_issue_ids`、`max_candidates`、`max_case_attempts`、`max_probe_attempts`、每任务／探针超时和可选费用预算。默认每个选中问题生成一个候选，不自动生成第二种方案；新增或重做候选需要用户扩展本次计划。`max_case_attempts` 按冻结的任务集合、验证阶段、重复次数和允许的重试上限计算；探针调用单独记账，不能借诊断绕过运行预算。探针预算未指定时为 0，可继续只读调查并提出必要检查；用户已授权的检查不重复确认。

目标任务的失败默认不自动重试；明确的基础设施重试必须由计划允许，所有尝试分别记账并保留。超时沿用项目已声明值，缺失时由初始化确认，不允许无限等待。费用不可观测时不得宣称已满足费用上限；可用调用数、样本数和超时作为确定性预算。

### 10.5 内容变化和相互影响

候选直接在当前已接受内容上编辑，因此不需要独立候选之间的补丁合并器。即使没有文本冲突，新增规则也可能破坏已有收益，必须通过增量门禁检查。

发现原工程出现非本轮编辑、HEAD／暂存区漂移或封存内容变化时停止提交与恢复，保留现场；不得自动合并、覆盖或沿用旧评分。人工处理后重新核验，影响冻结条件时另开 Round。

### 10.6 决策动作

| 动作 | 含义 | 工程与 Git 行为 |
|---|---|---|
| `keep` | 接受精确候选，保存验证依据 | 暂存明确候选路径并创建一个本地 commit，成功后推进当前检查点 |
| `reject` | 拒绝候选，保留失败／无收益记录 | 封存补丁后恢复父检查点；不为失败候选创建 commit |
| `defer` | 证据不足或暂不决定 | 封存后恢复父检查点，或保留现场并暂停本轮；不可带入下一候选 |
| `rollback_to` | 撤回当前已接受链的后缀 | 恢复本轮已记录的较早检查点内容，创建恢复提交；不改写历史 |
| `complete` | 最终验证通过，结束本轮 | 核验当前内容、commit 和最终 Validation 一致；不重复提交或写回 |
| `close` | 放弃本轮采用 | 清理未决候选，恢复 B0；已产生提交时用恢复提交保留历史 |

`atk-validate` 产出事实和建议；`atk-decide` 执行决策及必要 Git 操作。用户一次授权“在指定范围串行调优，通过就提交、失败就回退”后，本轮内无需逐项重复确认。未授权提交时仍可评估和诊断，但不能把未提交候选标为已接受并继续累积。推送、部署和扩大范围不包含在此授权中。

### 10.7 最终验证失败后的处理

逐项 `keep` 只代表增量门禁通过，最终完整验证仍可能失败。冻结计划默认规定：最终结果非 `pass` 时恢复 B0 并以未采用关闭；若需要继续调查，可暂停并明确报告当前检查点尚未完成最终验收，不能标记成功。

用户可事先或届时选择一个较早检查点，回退后重新执行它相对 B0 的完整门禁；预算不足则不得正常完成。只支持回退线性链的后缀，不枚举候选子集。恢复遇到并发编辑或其他未知状态时先暂停，不能为了关闭流程覆盖现场。

已接受候选的历史 Validation 和 commit 保留。撤回后不抹去当时的有效证据，也不继续声称这些修改仍在当前采用链中。

### 10.8 明确的示例与测试预期

以下为合成测试场景，不是实际项目成绩。10 个任务中，B0 通过 1–6，失败 7–10；任务 2、3 为关键保护任务。表中结果使用同一完整样本集，便于验收计算。

| 候选 | 实际父版本 | 本次效果 | 决策与检查点 |
|---|---|---|---|
| C1 | B0：6/10 | 修复 7、8，无退化：8/10 | keep，提交 K1 |
| C2 | K1：8/10 | 修复 9，但破坏关键任务 2：8/10 | reject，恢复 K1 |
| C3 | K1：8/10 | 保持 7、8，新增修复 9：9/10 | keep，提交 K3 |
| C4 | K3：9/10 | 修复 10，但破坏关键任务 3：9/10 | reject，恢复 K3 |

C3 的增量修复数为 1；即使其规则也影响已通过的任务 8，也不得再次计作修复。最终 K3 相对 B0 修复 7、8、9，共 3 个任务，最终完整门禁通过后完成本轮。

若最终复验发现 K3 仍有回归，则按第 10.7 节回退或暂停；不能以两个候选已经 commit 为由宣布整轮成功。

---

## 11. 验证与效果门禁

### 11.1 两类验证

| 类型 | 对照对象 | 目的 |
|---|---|---|
| `incremental` 增量验证 | 当前已接受父 Revision 与 Candidate Revision | 验证本次边际收益，同时保护已有收益 |
| `final` 最终验证 | B0 与当前已接受累计 Revision | 验收整轮总体效果，决定是否完成或回退 |

两类验证复用 Evaluation 的 Runner、Judger、数据格式和指标计算。取消独立筛选后再做增量组合的重复阶段。

### 11.2 数据范围

每个 Issue 有针对性样本；每轮有保护集，至少包含关键业务样本、历史回归和代表性成功样本。增量验证不能只验证之前失败的任务。

增量验证使用“本候选针对性样本＋本轮保护集＋当前采用链此前已修复任务”。新增保护项从冻结任务池中选择，父版本缺少对应结果时补跑，不能以旧结果覆盖不足为由跳过。最终验证使用本轮预先确定的完整验证集；数据量小时默认就是本轮全部可重放任务加历史保护集。

可选的保留测试集只用于阶段性验收，不用于每个候选反复调整。一旦其细节被用来指导修改，就应标记为已用于优化，不再宣称独立保留测试。

Case 元数据保存 `source_group_id` 与 `usage=optimization/protection/holdout`；来自同一原始任务、会话或近重复变体的样本归为同组。独立 holdout 与已用于优化／保护的样本不得有来源组重叠；来源未知时不能声称独立性。按冻结的里程碑或连续轮数安排阶段验收，并保存 holdout 暴露记录；长期反复使用的保护集不能充当泛化证明。新增样本或用途变化在新轮冻结，保留历史快照。

### 11.3 可比性检查

必须一致的条件：任务内容及附件版本、Ground Truth／rubric、判定器版本、运行适配、冻结环境约定、重复计划和重置策略。候选明确允许改变的目标资产不参与固定条件一致性要求，但必须属于 Revision 差异。

`fixed_context_hash` 排除临时输出目录、时间戳等无语义差异，使用逻辑路径与文件内容；同一工程路径也不能掩盖实际源码、加载内容或构建制品不同。

可复用基线运行的前提是所有固定条件匹配，且项目声明环境可重建或漂移可接受；仅模型名称相同不够。变化中的外部服务需按事前规定的运行窗口、交错／配对顺序和身份核验规则重新配对运行。无法控制且足以解释收益的漂移应返回 `insufficient` 或 `invalid`，不能仅附一条局限后照常通过。原始执行与诊断重放不能混为同一组对照。

### 11.4 一期默认质量门禁

默认主要目标为任务成功率。项目可以在冻结前设置效率目标，但不能在看到结果后改换目标。

质量门禁默认要求：

1. 所有必需样本已执行且关键维度有效、可判定，判定器已校准；缺失、未知、加载证据不足、环境不一致或未完成计划内最终复验都不能视为通过。
2. 至少修复一个针对性任务，且主要指标严格改善；`min_fixed_cases=1`、`min_primary_delta=0`，使用严格大于。
3. 关键任务退化数为 0；默认其他保护任务退化数也为 0。放宽时必须事前配置。
4. 满足事前设置的成本、耗时和工具限制。未设置的成本门槛只报告，不自行创造；必需成本指标缺失则证据不足。
5. 无越界修改、运行异常归因错误或候选加载错误。

结果枚举：`pass`、`no_effect`、`regression`、`insufficient`、`invalid`。

无效果候选默认建议拒绝；回归候选默认建议拒绝；证据不足建议暂缓；无效实验必须修复前置条件后重新验证。

上述门槛是操作性验收，不是统计显著性证明。小样本通过只表示在给定样本和条件中通过，不能写成“已证明线上普遍提升”。

效率目标下，必须先满足质量不退化，再达到预先确认的效率门槛；不能用质量目标的 `min_fixed_cases` 强迫效率候选修复失败任务。

### 11.5 重复与不确定性

增量初筛默认每个任务一次执行，报告 `evidence_level=single_run`。计划记录 `repeatability=deterministic/stochastic/unknown` 及判断依据；最终验证对 `stochastic/unknown` 默认双方各执行 3 次，对有依据的 `deterministic` 可各执行 1 次。项目可在冻结前提高次数或指定另一项至少 2 次的重复计划；有波动／未知场景若仅能承担单次执行，结果可作探索或人工例外，不能正常通过整轮完成门禁。次数不代表统计显著性承诺。

最终重复验证在精确累计 Revision 锁定后按计划执行，不从初筛历史中选择好成绩。双方任务、重复次数、超时、重置、配对和重试规则相同，所有尝试保留；不能只重跑候选直到得到好分数。最终复验预算须在候选生成前预留，耗尽时输出 `insufficient`。

不得取多次运行的最佳一次作为主成绩。每 Case 按预先规定的均值或通过比例聚合，报告分布、样本数和变化；修复／退化按冻结的 Case 级聚合阈值计算，不把同一 Case 的多次成功算成多个修复。未知不能自动当 0，重试不增加 Case 分母。不一致到无法做可靠决定时输出 `insufficient`，不无限追加运行。

指标至少包括：修复、退化、持续失败、稳定成功、未知数量、覆盖率和可获取的成本。样本匹配依据稳定 Case ID 与内容指纹，不按 CSV 行顺序配对。

### 11.6 人工例外

用户可以明确要求将未充分验证的候选提交为试用检查点，或保留尚未通过最终验收的累计版本。记录 `override=true`、原验证结论、风险和用户理由；不把人工许可改写成验证通过。

路径越界、目标文件冲突、补丁不完整和未授权外部副作用等安全检查不得通过此例外跳过。默认“无效／无收益就放弃”仍然成立，例外必须是用户明确动作。试用检查点可在授权内继续调优，但本轮含此例外，只有后续最终验证实际通过才能正常完成；否则只能以 `completed_with_override` 明示例外结束。

---

## 12. Git 管理：原工程、候选提交与检查点回退

### 12.1 技术选择

一期采用 **干净 B0 commit＋原工程串行编辑＋有效候选 commit＋失败候选补丁留档**。不创建额外 worktree，不维护第二套完整源码快照，不设置独立候选池或最终补丁写回器。用户原工程本身可以是已有 linked worktree，ATK 无需管理其生命周期。

Git commit 保存检查点，`git restore --source=<commit> --worktree -- <明确路径>` 可恢复工作区指定文件而不移动分支；恢复已提交修改也可以形成新的提交，保留原历史。[S7][S8] 这些原语仍需以下范围、内容和中断检查，不能直接用全仓强制重置代替。

### 12.2 初始 Git 检查

实验要求有至少一个 commit、位于明确本地分支的非裸 Git 仓库。存在未完成 merge/rebase/revert/cherry-pick、冲突条目、detached HEAD、影响运行的未知嵌套仓库变化或无法安全读取的文件时，阻塞候选操作；只读导入和诊断仍可继续。

源码修改一期限定为当前仓库普通文本文件的新增、修改和删除；重命名按删除＋新增记录。submodule 内容修改、LFS 二进制、符号链接改写、文件模式变更不属于一期优化目标。路径检查拒绝绝对目标路径、`..` 越界、符号链接穿透、大小写冲突及路径别名歧义。Git 路径参数必须按字面值传递，使用参数数组和必要的 NUL 分隔清单，不能把文件名当通配符或 shell 代码。

`.git`、ATK 状态和工具目录、评分标准、验证数据始终是候选保护区。Git 元数据只可由受控 Git 操作修改，不能由候选直接编辑。接入脚本准备是独立接入动作，不属于候选。

### 12.3 干净基线与已有修改

冻结 B0 前要求暂存区及已跟踪工作区干净，运行所需业务源码／Skill 已纳入该 commit。B0 记录完整 commit ID、分支和运行源码清单；不再把未提交修改自动叠加为隐含基线。

已有未提交或暂存修改时列明阻塞，保持原状。用户可先自行整理提交，或明确授权对列出的既有修改创建基线提交；“处理本轮候选”的授权不等于允许打包所有历史修改。接入脚本、数据和证据默认位于被排除的 `.atk/`，不混入候选 commit。

未跟踪文件需分类：运行所需源码必须先进入基线；秘密配置通过外部注入，依赖和生成物按运行配置处理；其他无关文件记录占用并保留，不自动 add 或 clean。不实现复杂的脏工作区暂存区重建，也不使用 stash 搬运用户状态。

### 12.4 原目录内的候选与版本复跑

一个可执行 Round 独占一个工程的源码修改与 Git 操作。开始候选前核验分支、`HEAD=current_commit`、干净暂存区、已跟踪内容及冻结组件，记录父 Revision 和父 commit。Codex 只编辑允许路径，同时登记本候选新建文件；不得在候选未决时启动下一候选。

父版本或 B0 的合格执行可按第 11.3 节复用；需要复跑时，先封存当前候选或确认当前已接受 commit，再在原目录临时恢复指定 Revision 的文件，执行后恢复原内容。仅修改两版本差异涉及的受管路径；不切分支、不移动 HEAD，不修改暂存区。临时恢复期间锁住优化与决策，运行请求明确记录目标 Revision，而不是把仍指向当前检查点的 HEAD 当作运行身份。

已提交内容从 Git 恢复，未提交候选从父 commit＋封存文件恢复。每次切换核对文件清单、存在性和指纹，按目标版本是否存在该文件分别处理恢复与删除；未跟踪的新文件不能指望 Git 自动清理，只删除明确属于本轮且内容匹配的新增文件。清除或重建受影响的缓存／制品，按第 7.4 节启动新进程。未知文件占位、额外编辑或恢复失败时暂停，不能继续对照。

版本切换是带操作记录的有限文件恢复，不承诺多文件原子性。每次记录起点、目标、受管路径和完成阶段；完成后的原工作内容必须可核对，不能因为测试失败就遗留一个冒充当前版本的 B0 工作区。

### 12.5 候选封存与提交

封存工具从原工程相对父 commit 的实际完整差异生成 `changes.patch`、`files.json` 及新增／修改文件内容，记录删除项；未跟踪的新文件不得遗漏。父文件由 Git 保存，无需再复制整仓。检查所有变更是否属于候选与允许范围，并以实际路径、类型、内容计算 Revision 指纹，不能相信提案中的 diff 摘要。

验证针对封存内容执行。`keep` 前再次核验父 HEAD、分支、暂存区和实际文件；只暂存本候选明确路径，确认暂存差异与封存 Revision 一致后提交。不得使用 `git add .` 或 `commit -a`。提交记录包含 Round／Candidate 标识，消息遵循目标仓库规范；Decision 保存提交前后完整 ID、Validation 引用和授权。

提交后检查单一父 commit、实际 tree 与已验证内容一致、无意外路径、工作区及暂存区已恢复干净，再推进 `current_commit/current_revision_id`。核验还需覆盖 Git clean/smudge 过滤、换行转换或构建时嵌入 commit ID 等实际存在的行为；若提交或从 Git 恢复改变了被测内容／制品，原结果不可直接沿用，先修正接入或重新验证精确检查点。commit ID 是持久检查点，内容指纹仍负责核验；不得改写已封存 Revision 以迎合提交结果。

提交失败、签名或 hook 拒绝时不推进检查点，也不跳过仓库既有检查。hook 若改写候选内容，原验证不再适用于新内容；保留现场，重新封存并验证。只有明确属于本次操作且指纹匹配的暂存项可撤销，不能清空未知人工暂存。

### 12.6 失败候选恢复与已接受链回退

`reject` 前先保存补丁、诊断、全部验证和拒绝原因，再恢复父 commit 中被本候选修改或删除的文件，并移除本候选独有且内容匹配的新增文件。确认恢复完整后保持当前检查点不变；失败候选不用 commit 保存。未封存草稿中断时，先记录已登记路径及现场差异，存在归属不明的编辑就暂停人工核对。

已接受链需要回退时，只接受本轮记录的 B0 或较早检查点。先要求无未决候选与额外编辑，将当前累计差异恢复为目标检查点内容，再创建一个普通恢复 commit，记录 `rollback_to` 和撤回的候选后缀。当前 Revision 指向恢复后的已知内容，`current_commit` 指向新的恢复提交，二者关联必须通过内容校验。

不自动 `reset --hard`、`clean -fd`、rebase、amend 或强制推送；不删除旧检查点，也不自动挑选中间候选。用户自行改写历史或提交其他内容后，旧轮暂停并重新核验，不能自动接管变化。

### 12.7 中断恢复与操作一致性

Git 和 `.atk/` 记录无法作为一个原子事务提交。修改文件、暂存、commit、更新 Decision／Round 前，保存包含操作 ID、预期父 commit、候选指纹、动作和阶段的记录；同一动作重试时先核对实际结果。

尤其要覆盖“commit 成功但状态尚未写入”的情况：只有实际 HEAD 的父提交、候选标识和完整内容都匹配，才补记原决策；不得再造一次 commit。文件恢复中断时逐项核查，仅继续恢复仍等于已知起点或目标内容的路径；出现第三种内容、未知暂存或无法解释的 HEAD 时暂停，保留现场和明确恢复指引。

不实现复杂的自动冲突修复器。正常路径应可恢复，无法证明安全的状态由人核对；中断本身不能被报告为拒绝、提交成功或本轮完成。

### 12.8 最终交付与下一轮

最终验证通过后交付 B0 与最终 commit、候选及恢复提交列表、最终累计 diff、完整验证报告和采用／拒绝记录。`complete` 核验当前 Revision 与最终 Validation 完全一致；文件已经在原工程，commit 已存在，不再执行第二次写回或创建重复“最终提交”。

最终不通过按冻结规则执行回退或暂停；以人工例外保留时明确标识，不能将“Git 已保存”解释为“效果已通过”。下一轮从当前干净 commit 创建新 B0，重新冻结运行与验证条件。本地提交授权不包括 push、发布、部署或对外消息。

---

## 13. 七个 Skill 的执行契约

| Skill | 前置条件 | 核心动作 | 主要输出 | 不能做 |
|---|---|---|---|---|
| `atk-init` | 有项目可检查；纯导入可仅初始化存储 | 调查运行方式、Git、自动加载、组件身份和范围；生成 runner；执行必要的加载检查 | 项目配置、`runtime.md`、组件清单、适配脚本、加载证据 | 创建新 Agent、擅自提交既有修改、默认跑完整数据集 |
| `atk-dataset` | 有样例、反馈或待转化轨迹 | 构建／修订任务及 `ground_truth`；补齐重放条件 | 版本化数据集、任务索引、修订说明 | 把 Agent 原答案直接当正确答案、覆盖历史验证数据 |
| `atk-eval` | 明确 run/import/reassess；run 需可用 runner | 获取证据、准备并校准评分规则、按被测边界评判 | EvidenceBatch、Assessment、有效性与覆盖报告 | 无请求重新运行、伪造缺失评分 |
| `atk-diagnose` | 有可读证据；Assessment 可缺省 | 核对契约与责任、区分竞争解释、按需执行有预算的探针、聚合 Issue 和知识 | Issue、检查证据、范围外交接、知识变更 | 直接修改正式 Agent、把猜测或规避当作已修复根因 |
| `atk-optimize` | 有可实验的 Issue、冻结基线、范围和预算；无未决候选或阻塞依赖 | 在当前已接受检查点上编辑一个候选、检查、封存 | Candidate、父 commit、Revision、提案、差异 | 把多个不相关根因混为一次不可拆改动、自动保留或提交 |
| `atk-validate` | 有确切父／候选或 B0／最终对照及验证计划 | 增量／最终验证；必要时受控切换源码并恢复现场 | Validation、配对明细、门禁结论 | 改写候选、创建采用 commit、选择性丢弃失败尝试 |
| `atk-decide` | 有精确候选／检查点、验证或例外依据及相应授权 | keep 提交、reject/defer 恢复、rollback_to、complete/close | Decision、commit 关联、当前检查点、最终报告 | 未授权提交或回退、推送／改写历史、将例外改写为通过 |

所有 Skill 结束时使用同一交接结构：本次对象、已完成动作、结果状态、产物位置、阻塞原因、下一步。没有下一步时不强行启动下一个 Skill。

一个用户请求可以覆盖多个已明确的顺序操作，例如“处理 P1–P3，逐项验证并保留有效修改”。必须先保存包含原工程编辑、提交和回退规则的有限计划，再在当前会话执行；已有明确授权不重复询问，超出范围时再确认。

---

## 14. 数据契约和存储

### 14.1 格式分工

业务数据集继续使用原有列，推荐 CSV，预期结果只要求可选 `ground_truth`；不要求用户增加根因、置信度和工作流状态列。

内部机器元数据统一使用 JSON，嵌套轨迹使用 JSONL，扁平评判与比较使用 CSV，报告与知识正文使用 Markdown。不使用 YAML 作为项目配置格式；公开 Skill 自身的标准 frontmatter 不属于项目配置。

这是内部最小稳定契约，不是强迫目标 Agent 改用统一业务 Schema。

### 14.2 关键对象的最低字段

| 对象 | 必须字段／约束 |
|---|---|
| ProjectConfig | `schema_version`、工作区／Git 引用、运行命令、输入映射、允许／保护路径、加载验证方式、组件配置、诊断只读来源／探针配置、脱敏策略 |
| Case | `id`、内容指纹、输入／初始条件、可选预期、附件、`source_group_id`、`usage`、来源与缺口 |
| EvidenceBatch | `id`、`purpose`、来源类型、文件引用和指纹、适配版本、`records_ref`、实际组件清单、导入／执行状态、完整性与过滤信息；诊断批次另含 Issue、检查与原证据引用 |
| EvidenceRecord | `id`、来源定位、Case／Trace／Session 引用、可空的内嵌 `execution`、输入输出的值与存在标记、事件／附件证据索引及其 `origin`、能力缺口 |
| Execution | `id=execution_id`、`batch_id`、`record_id`、Case ID／指纹、`repeat_index`、可空 `retry_of`、`purpose`、Revision 引用、运行配置／实际组件清单引用、开始／结束时间、状态；按第 7.2 节内嵌存储 |
| Assessment | `id`、证据批次、`evaluation_spec` 与指纹、判定器／校准引用、`judger_readiness`、维度、权威 CSV 引用及指纹、从明细派生的计数与完成状态 |
| Round | `id`、证据与 Assessment 引用、B0 Revision／commit 可空引用、分支、Issue 列表、冻结计划、全部候选、`current_revision_id`、`current_commit`、有序 `active_candidate_ids`、分阶段预算、状态、可空的前序 Round／外部修复引用 |
| Issue | `id`、主要症状、证据、根因假设、竞争解释与反证、检查记录、`mechanism_evidence_refs`、`intervention_validation_refs`、根因状态、责任组件／干预层、相关 Case、优先级、`disposition`、`resolution=open/resolved`、按需交接／解决证据 |
| Revision | `id`、`base_commit`、实际运行源码清单与内容指纹、父 Revision、可空 Candidate、可空补丁／封存文件引用；基线由 commit 重建，未提交候选由父 commit＋差异重建 |
| Candidate | `id`、`round_id`、主要 Issue、`parent_revision_id`、`parent_commit`、`blocked_by_issue_ids`、`change_kind=fix/workaround`、可空 `supersedes`、变更路径、补丁与 Revision 引用 |
| Validation | `id`、模式、左右 Revision、计划／边界／判定器指纹、Execution／Assessment 引用、配对覆盖、Case 级聚合、比较结果、`evidence_level`、门禁、局限 |
| Decision | `id`、对象、动作、理由、可空 Validation 引用及缺失理由、授权来源、例外标记、操作 ID、操作前／后 commit、关联 Revision、回退时的目标与撤回后缀、时间；正常 keep／complete 必须有对应通过的 Validation |
| Knowledge | `id`、状态、适用条件、相关组件／契约／判定器指纹、证据与反证、候选／验证引用、可空的前一修订引用；复用时单独记录本轮适用性 |

所有对象包含 `schema_version=2`、创建时间和稳定 ID。引用必须由工具校验存在且类型正确，不能仅在 Markdown 中用名字关联。

组件清单、`evaluation_spec`、证据索引和诊断检查为上述对象的内嵌结构或封存附件，不新增独立服务。检查记录至少含检查目的、要区分的解释、执行方式／预算、预期与实际、`status=not_run/completed/failed/inconclusive`、证据及未执行原因；封存后更正通过新记录引用旧记录。Schema 必须覆盖这些字段和第 5、7、9 章的条件约束，不能只验证 JSON 可解析。

### 14.3 候选记录示例

以下使用简化 ID 便于阅读：

```json
{
  "schema_version": 2,
  "id": "C3",
  "round_id": "R1",
  "primary_issue_id": "P3",
  "related_issue_ids": ["P1"],
  "parent_revision_id": "REV-C1",
  "parent_commit": "<K1 的完整 commit ID>",
  "revision_id": "REV-C3",
  "blocked_by_issue_ids": [],
  "change_kind": "fix",
  "supersedes": null,
  "changed_paths": ["skills/schema-generation/SKILL.md"],
  "patch_ref": "candidates/C3/changes.patch",
  "content_status": "sealed",
  "created_at": "2026-09-27T00:00:00Z"
}
```

以上 `candidate.json` 在封存后不可变。可变化的选择和索引单独放入 `state.json`，例如：

```json
{
  "candidate_id": "C3",
  "state_version": 2,
  "latest_validation_id": "V3",
  "selection": "kept",
  "keep_decision_id": "D-KEEP-C3",
  "accepted_commit": "<K3 的完整 commit ID>"
}
```

`state.json` 是可重建的状态投影，必须由已存在的 Validation 和 Decision 更新，不能充当判定证据。`latest_validation_id` 不覆盖其他验证历史。`selection=kept` 必须有成功提交且内容匹配的 Decision，但不表示本轮最终验证通过；它也是历史选择，当前是否仍采用由 Round 的 `active_candidate_ids` 表示。恢复提交可以对应已存在的 Revision，关联写入 Decision，不修改该 Revision。示例中的 commit 占位符在真实记录中必须是 Git 可解析的完整 ID。

### 14.4 推荐目录

```text
.atk/
├── project.json
├── context.md
├── current.json                    # 显式当前对象引用，不是事实来源
├── adapters/
│   ├── runner.py
│   └── import_mapping.json
├── datasets/<dataset-id>/
│   ├── manifest.json
│   ├── dataset.csv
│   └── cases.jsonl
├── evidence/<batch-id>/
│   ├── manifest.json
│   ├── records.jsonl
│   ├── source/                     # 允许保留的脱敏副本
│   └── artifacts/
├── assessments/<assessment-id>/
│   ├── manifest.json
│   ├── assessment.csv
│   └── summary.md
├── knowledge/
│   ├── index.json
│   └── <knowledge-id>.md
├── rounds/<round-id>/
│   ├── round.json
│   ├── plan.json
│   ├── baseline/
│   ├── issues/
│   ├── diagnosis.md
│   ├── candidates/<candidate-id>/
│   │   ├── candidate.json          # 封存的候选定义
│   │   ├── state.json              # 可重建的状态投影
│   │   ├── proposal.md
│   │   ├── changes.patch
│   │   ├── files.json
│   │   └── files/                 # 未提交候选的封存内容，拒绝后仍可调查
│   ├── revisions/<revision-id>/
│   ├── validations/<validation-id>/
│   │   ├── validation.json
│   │   ├── comparison.csv
│   │   └── report.md
│   ├── operations/                # 提交／恢复中的最小操作记录
│   ├── decisions.jsonl
│   └── summary.md
└── locks/
```

只按需创建目录。ATK 运行产物必须排除出被测源码、调优扫描和候选提交；初始化可在已有授权内配置本地 Git exclude，并明确记录，不静默改项目 `.gitignore`。已被 Git 跟踪的 `.atk/` 不会因 exclude 自动消失，必须在实验前由用户整理，不自动取消跟踪。源码持久检查点由 Git 保存，这里不再维护完整源码副本。

### 14.5 权威性、状态与可恢复性

JSON 元数据、`records.jsonl` 证据及封存内容是权威记录；Assessment 的判定明细唯一存于第 8.4 节定义的 `assessment.csv`。`comparison.csv`、其他统计 CSV 和 Markdown 报告是派生视图，不反向改写事实。知识正文可用 Markdown，但状态与证据引用由元数据管理。模型生成的 JSON 必须先经结构与引用校验，再由共享存储模块写入；写出 CSV 后不保留另一份可独立改分的权威副本。

已封存的 Evidence、Assessment、Candidate 定义、Revision、Validation 不原地覆盖；内容修订创建新 ID。Issue／Knowledge 的调查结论与处理状态更新保留带稳定对象 ID 的新修订，引用包含修订号；不得覆盖旧 Validation 所引用的结论。尚在执行的批次允许写入中间状态，完成或终止后封存；后续重试关联新执行记录。Round 当前状态和候选 `state.json` 由带版本号的原子更新维护，决策日志追加写入，冻结的 `plan.json` 不随状态更新而改变。

写入采用临时文件＋替换，追加日志使用锁；一次只允许一个当前可执行 Round 和一个修改状态的 ATK 操作。切换新轮前旧轮必须暂停或关闭；恢复暂停轮前停用当前轮并重新核验其冻结条件，条件已变则只能只读查看并另开新轮。异常退出保留 `running/interrupted`，恢复前检查进程、锁、文件与指纹；不能只因有输出文件就标为完成。

重新执行外部任务可能产生副作用，因此中断恢复只复用已确认完成的样本；状态不明的尝试必须检查后再决定是否重跑。旧尝试不删除。

---

## 15. 状态约束与权限

### 15.1 Round 状态

`analysis_only → ready → optimizing → finalizing → completed / completed_with_override / closed_without_adoption`

可从执行状态进入 `paused`，恢复前核验实际文件、分支和 commit，回到记录阶段。`completed` 要求精确当前版本通过最终门禁；`completed_with_override` 保留未通过的原结论与明确授权；`closed_without_adoption` 要求工作内容已恢复 B0，未绑定 B0 的纯分析轮则直接关闭。不能在恢复失败时标记关闭成功。

候选提交并不使 Round 完成。没有接受任何候选时，以未采用关闭；不制造空提交或声明获得改善。

### 15.2 Candidate 与验证状态正交

候选内容状态：`draft → sealed → archived`。

候选选择状态：`pending / kept / rejected / deferred`。

验证结论独立保存：`pass / no_effect / regression / insufficient / invalid`。

不再设置组合参与状态。候选是否仍在当前累计版本中由 Round 的有序采用链派生；例如候选曾 `kept`，后来因后缀回退退出当前采用链，历史 Validation 和接受 commit 仍然保留。

提交进行中或失败只写操作状态，不能提前标为 `kept`；拒绝／暂缓后的恢复未完成时也不得开放下一候选。服务阻塞由 `blocked_by_issue_ids` 表达，不与 Git 操作失败混用。

### 15.3 关键操作权限

| 操作 | 权限 |
|---|---|
| 读取轨迹、生成报告、创建临时产物 | 用户当前任务授权范围内执行 |
| 有预算的运行、原工程候选修改与版本复跑 | 用户确认的范围与运行环境内执行 |
| 候选暂存、commit、失败恢复及已接受链回退 | 本轮明确授权可一次覆盖；按冻结路径、门槛、回退规则执行，无需逐项重复确认 |
| 将用户既有修改整理为 B0 commit | 需明确指定既有修改及授权，不能从候选授权推断 |
| 修改评分标准、扩大范围、使用真实外部写操作 | 单独确认 |
| 最终完成或例外保留 | 正常完成按冻结门禁；例外必须明确记录用户授权 |
| push、merge/rebase、改写历史、部署、发送消息 | 一期不执行 |

---

## 16. 实现架构与内部接口

### 16.1 分层

```text
七个 Skills：流程说明、上下文调查、推理任务与交接
                         ↓
确定性操作接口：参数检查、明确动作，不提供自主思考循环
                         ↓
Evaluation / Intelligence / Optimization / Governance
                         ↓
共享 Contracts、Store、Git Checkpoint、Patch、Redaction、Reporting
                         ↓
项目 runner 与真实目标 Agent
```

不创建抽象的 Codex Handler，也不留下假实现占位。需要当前 Codex 推理时，Skill 明确其输入、允许读取的材料和产物要求；工具不在内部偷偷调用另一个模型。

### 16.2 代码组织建议

```text
src/agent_tune_kit/
├── cli.py                         # 安装能力＋内部确定性操作入口
├── contracts/
├── storage/
├── evaluation/
│   ├── sources/
│   │   ├── batch_results.py
│   │   └── langfuse.py
│   ├── execution.py
│   ├── assessment.py
│   └── metrics.py
├── intelligence/
│   ├── issues.py
│   └── knowledge.py
├── optimization/
│   ├── scope.py
│   ├── candidates.py
│   └── revisions.py
├── governance/
│   ├── comparison.py
│   ├── gates.py
│   └── decisions.py
├── workspace/
│   ├── git.py
│   └── checkpoints.py             # 提交、有限路径恢复及中断核验
└── reporting/

skills/<七个入口>/SKILL.md
templates/                         # runner、rubric、提案与报告
schemas/                           # 内部产物协议
scripts/validate_skill_pack.py
```

保留项目现有 Python 包和本地插件交付形态。当前 `pyproject.toml` 使用 Python 3.11+，运行时没有声明依赖；一期继续标准库优先，测试可使用项目的 pytest 工具链，不为导入文件引入完整 Langfuse SDK。[S4]

### 16.3 固定内部服务接口

以下是应用层契约，不要求所有参数直接成为公开 CLI 子命令：

| 接口 | 输入 → 输出 |
|---|---|
| `import_evidence(request)` | 文件、来源、映射、脱敏规则 → EvidenceBatch |
| `run_evaluation(request)` | 用途、Revision／实际制品、Cases／探针输入、有限运行计划 → EvidenceBatch；诊断用途不进入效果统计 |
| `store_assessment(request)` | 证据、被测边界、固定判定与校准结果 → 经校验的 manifest＋权威 CSV |
| `create_round(request)` | 证据、Issue／目标、可选 B0 → Round |
| `freeze_round(request)` | B0、范围、验证与预算 → 不可变计划版本 |
| `store_diagnosis(request)` | Issue、检查证据、交接／复验与知识草稿 → 经引用、状态升级条件和范围校验的新修订 |
| `prepare_candidate(request)` | Round、Issue、当前 Revision／commit → 原工程核验结果与 Candidate draft |
| `seal_candidate(request)` | 原工程相对父 commit 的实际差异 → Candidate、Revision、越界检查结果 |
| `compare_and_gate(request)` | 左右 Assessment 与冻结计划 → Validation |
| `decide(request)` | 精确对象、动作、验证、授权 → 必要 Git 操作、核验后的 Decision 与当前检查点；失败只记录操作状态 |
| `inspect_or_recover_operation(request)` | 已记录操作、实际 Git／文件状态 → 补记已完成动作、安全恢复或明确阻塞 |

版本复跑的临时文件恢复由 `run_evaluation` 调用同一 Git 检查点模块并在结束时还原，不新增公开 Skill 或独立工作区后端。

请求和响应通过 JSON，返回统一的 `status`、`artifact_refs`、`warnings`、`error_code`、`next_required_action`。当前 Codex 调用内部入口，例如 `atk internal <operation> --request ... --output ...`；这不是新增面向用户的调优 CLI 工作流。

ATK 工具使用其安装环境；目标 runner 使用目标项目环境，二者不能混淆。工具启动命令使用参数数组，不拼接未转义的 shell 字符串。

### 16.4 必须明确的错误码

至少包括：`UNSUPPORTED_EXPORT_FORMAT`、`AMBIGUOUS_MAPPING`、`INCOMPLETE_EVIDENCE`、`NOT_REPLAYABLE`、`JUDGER_INVALID`、`LOADING_UNVERIFIED`、`BASELINE_DRIFT`、`SCOPE_VIOLATION`、`REVISION_MISMATCH`、`COMPARISON_INVALID`、`BUDGET_EXHAUSTED`、`WORKSPACE_CONFLICT`、`GIT_OPERATION_INTERRUPTED`、`COMMIT_FAILED`、`DIRTY_BASELINE`、`UNSUPPORTED_GIT_STATE`。

错误必须说明已完成什么、未完成什么以及可以恢复的对象，不只返回一段泛化建议。报告不能因为脚本退出码为 0 就省略产物完整性检查。

---

## 17. 开发工作包与顺序

| 工作包 | 交付内容 | 依赖 | 验收后才能进入 |
|---|---|---|---|
| W1 协议与状态 | 核心 Schema、批次／尝试 ID、组件与证据来源、权威明细、存储、锁、错误、状态测试 | 无 | 所有其他模块 |
| W2 Git 检查点与候选 | 干净 B0、原工程编辑／封存、精确候选提交、失败恢复、版本复跑、恢复提交及中断核验 | W1 | 真实候选优化 |
| W3 多来源 Evaluation | 批量／Langfuse 导入、脱敏、来源关联、被测边界、有效性／分母、判定器校准 | W1 | 真实诊断与评判 |
| W4 runner 接入 | 本地调用、候选加载与组件身份证明、部分执行／重复／重试、隔离探针 | W1、W2 | 正式对照与可执行诊断 |
| W5 诊断与知识 | 竞争解释、机制检查、结论升级、范围外交接／复验、知识适用性、当前 Codex 交接 | W1、W3；执行探针依赖 W4 | 问题驱动候选 |
| W6 累积调优与最终验收 | 父版本链、逐项决策、边际收益、既有收益保护、后缀回退、最终重复门禁及保留集隔离 | W2–W5 | 完整闭环 |
| W7 Skills 与精简 | 七入口、共享流程、旧入口删除、模板、README、包资源更新 | 与 W2–W6 同步，最终集成 | 发布候选 |
| W8 端到端验收 | 真实 Skill／非 Skill、多来源、多候选、已知根因区分、服务交接后复验与安全验收 | W1–W7 | 正式发布 |

W2 必须早做，不能在最后把“保留／放弃”补成几个危险 Git 命令。W6 是一期必需能力，不得推迟为无人值守阶段。

W3/W4 联调阶段即取得真实脱敏导出，并用一个真实本地 Skill 跑通“基线取证 → 单问题诊断 → 原工程候选 → 加载证明 → 对照判定 → 提交或恢复”的最小链路；W5/W6 提供该链路所需的最小实现，先验证协议再扩展完整多候选流程。此检查是 W8 的提前风险验证，不替代最终验收。真实材料缺失时允许继续离线实现，但必须显式保留接入未验证状态，不能到发布时用合成数据补记完成。

每个工作包提交代码前应运行其单测和相邻契约测试。开发 Agent 不得通过放宽门禁、删除失败断言或绕过加载检查使测试变绿。

开发仓库提交与 ATK 目标仓库检查点是两类动作；后者只能通过带范围、内容核验和中断记录的决策流程执行，不得把任意工作区 diff 自动打包。

---

## 18. 验收测试矩阵

### 18.1 必需测试

| 编号 | 场景 | 必须满足 |
|---|---|---|
| T01 | 七个 Skill 打包与加载 | 只有七个公开入口，无旧名称别名，资源路径可解析 |
| T02 | 三个失败入口合并 | 单次 `atk-eval` 完成规则准备和判定，无额外规则 Skill |
| T03 | 已有结果重新判定 | 不重新运行 Agent，保留旧 Assessment |
| T04 | Langfuse JSON／JSONL／CSV 导入 | Trace、Observation ID 正确，不把工具调用当独立任务 |
| T05 | 不完整／过滤后 Trace | 标记缺口，不伪造父节点、最终答案或整体成功率 |
| T06 | Langfuse 字段别名与新旧 profile | 映射版本显式；模糊结构阻塞而非猜测 |
| T07 | Scores 与 token 重复 | 外部口径不混用，父子用量不重复累计 |
| T08 | 同文件重复导入与更新 | 正确去重；同 ID 内容更新保留修订 |
| T09 | 无 Ground Truth | 仍可分析；可判维度单独评判，其他 unknown |
| T10 | 执行中断和评分器异常 | 部分结果保留，不当作完整通过或 Agent 失败 |
| T11 | 正确候选加载 | 同一工程路径中确认实际读取指定 Revision，恢复 B0／父版本后不读取旧缓存 |
| T12 | editable install／绝对路径／运行器自行提交 | 发现其他安装副本与旧制品；运行器改源码、暂存或提交时阻塞正式门禁 |
| T13 | 多根因一批数据 | 输出多个有证据的 Issue；重叠样本可关联多个问题，下游症状不冒充独立根因；责任正确性另由 T35–T39 验收 |
| T14 | 多个累积候选 | C1 通过并提交 K1 后，C2/C3 以 K1 为父；未决候选不能成为下一候选基线 |
| T15 | 无效果与回归候选 | 证据留档后仅恢复本候选修改，保留此前检查点及其收益；失败候选无 commit |
| T16 | 边际效果与重叠收益 | 第 10.8 节中 C3 增量修复 1 个，最终 K3 相对 B0 修复 3 个，总分 9/10 |
| T17 | 增量通过、最终不通过 | 不得正常完成；按冻结规则恢复 B0、暂停或完整复验较早检查点，历史 commit／证据保留 |
| T18 | 恢复旧检查点后再编辑 | 只基于实际当前父版本生成新候选，不隐式重放旧补丁；改内容必须重新验证 |
| T19 | 回退累积链后缀 | 撤回前序候选时后续一并退出当前采用链；后续思路需重新构建验证，不能继承旧成绩 |
| T20 | 候选 seal 后被修改 | 原 Validation 不能用于新内容 |
| T21 | 已有未提交与暂存修改 | 阻塞冻结而不改原状；明确整理为干净基线后才可开始，不自动混入候选提交 |
| T22 | 新增、删除、特殊路径 | 空格、中文及 pathspec 元字符按字面处理；新文件留档可恢复，候选恢复与版本切换不漏删或误删 |
| T23 | 候选修改越界 | 封存或验证阻塞，不以建议性警告放行 |
| T24 | 原工程／HEAD／暂存区意外漂移 | 停止提交及恢复，不覆盖第三方内容；本轮预期提交正常推进不误报漂移 |
| T25 | 恢复中断／磁盘错误 | 记录操作阶段，不提前更新当前检查点；匹配已知内容才续恢复，未知内容暂停 |
| T26 | 候选提交及 hook／内容转换 | 授权内一个有效候选对应一个 commit；父提交、tree 与封存内容一致，无额外路径；hook、Git 过滤或 commit 身份影响实际内容时阻塞或重验 |
| T27 | 人工例外提交或保留 | 保留原验证结论与 override 授权；不冒充正常完成，安全检查仍有效 |
| T28 | Ground Truth／判定器变化 | 旧对照失效；新轮统一重判原执行或按输入／环境变化重跑，重新校准并生成 Validation |
| T29 | 多次尝试 | 双方分阶段重复／重试计划相同；全部保留，不挑最好一次，重复不增加修复 Case 数 |
| T30 | 新 Codex 会话恢复 | 仅靠明确产物重建当前轮，不依赖旧聊天文本 |
| T31 | Prompt／代码调优 | 走同样七入口，不要求出现业务 Skill |
| T32 | 轨迹凭证／恶意指令／HTML | 脱敏，不执行轨迹命令；本地 HTML 转义不可信内容 |
| T33 | 旧版工作目录 | 明确不兼容；不删除、不静默转换、不覆盖 |
| T34 | 真实接入 | 至少一份脱敏 Langfuse 实际导出，以及真实本地自动加载 Skill 的闭环 |
| T35 | Skill 错误与 Agent 未遵守的对照 | 前者定位错误规则，修订后目标／保护集通过；后者在规则正确的证据下定位执行偏差，不伪称 Skill 原文错误 |
| T36 | 合法工具请求触发缺陷 | 核对实际版本与契约，直接重放取得同类失败证据；形成工具 Issue／交接，不默认用 Skill 绕过并关闭问题 |
| T37 | 工具响应正确，运行时转交错误 | 比较调用边界两侧证据，定位运行时；缺少一侧时降级结论，不归责工具 |
| T38 | 评分器误判 | 正反例暴露误判，进入独立修订／校准；新轮重判双方，不生成改答案或改评分的 Agent 候选 |
| T39 | 缺证据／竞争解释／探针失败 | 无法区分时输出 hypothesis 或 inconclusive，列出缺口／下一步；未运行不写通过，引用存在不等于机制已证实 |
| T40 | 原始执行与事后重放、源码与制品不一致 | 来源／版本各自保留，探针不混入正式分数；发现固定依赖漂移或身份未知时正确阻塞归因升级／对照 |
| T41 | 同一服务错误、不同被测边界 | 服务可靠性记有效失败；受阻 Skill 维度及评分器异常分别处理；不得通过删去失败分母取得通过 |
| T42 | 范围外修复与临时规避 | 依赖候选受阻，独立问题可继续；规避不关闭缺陷；修复后新 B0／新轮引用原 Issue 并完成最小复现与端到端保护回归 |
| T43 | 两 Case×两次执行＋一次授权重试 | 一个初始批次含 4 个不同 Execution，重试新增 ID 并关联原尝试；重判无新执行，恢复无隐式重跑，Case 分母仍为 2 |
| T44 | Assessment 权威性与校准 | CSV 唯一键、引用、覆盖及 manifest 指纹一致；派生视图可重建，篡改 CSV 被拒绝；未校准判定器不能正常通过 |
| T45 | 波动与最终复验预算 | 单次偶然改善不替代冻结的最终重复验证；次数不足／预算耗尽为 insufficient，所有重复均进入既定聚合 |
| T46 | 来源泄漏与过期知识 | 同来源组不得同时宣称优化与独立 holdout；暴露后变更用途；组件／契约变化触发知识复核，不继承已验证状态 |
| T47 | 根因状态升级与探针边界 | 缺少规定证据的 supported／intervention_supported 更新被拒绝；探针只执行获准命令、隔离与预算，失败／未执行均留痕 |
| T48 | commit 已成功但 Decision 未保存 | 根据操作 ID、父提交、候选标识和 tree 补记原动作，不重复提交或重复推进 |
| T49 | 原目录复跑 B0／父版本 | 临时内容身份正确，HEAD／暂存区不变；复跑结束恢复起点，缓存／制品重建，异常不冒充当前版本 |
| T50 | 已接受链恢复提交 | 恢复为精确目标检查点内容且保留旧历史；无 hard reset、clean、自动重排，下一候选基于恢复 commit |
| T51 | 最终完成与空轮 | 正常完成必须匹配最终 commit／Revision／Validation；未接受候选不造空提交，无额外 worktree 或最终写回 |
| T52 | ATK 产物和未知文件 | 证据／评分数据不进入候选 commit；未知文件不被清理，已跟踪 .atk 不因 exclude 被错误视为已隔离 |

### 18.2 测试层次

使用确定性假 Agent 和小型 Git 测试仓库覆盖绝大多数逻辑，禁止依赖真实付费模型才能运行基础测试。Git 用临时仓库验证主 HEAD、暂存区和文件变化；Langfuse 使用无敏感信息的 fixture。

真实本地 Agent 测试作为人工启动的集成验收，不进入默认离线单测。验证报告必须区分“单测通过”“合成 E2E 通过”“真实接入通过”，不能相互替代。

T35–T39 使用表面症状相似、已知责任层不同的配对材料；夹具含可核验契约、输入／响应、版本与最小复现，期望答案不作为诊断输入。离线单测验证存储、引用、状态与门禁；当前 Codex 的调查能力另以人工启动的验收核对责任层、关键证据、反证处理与处置是否正确，不比较措辞，也不把格式正确计作根因正确。至少用一个真实已知缺陷或在真实本地 Agent 链路中可控注入的组件缺陷完成诊断和修复后复验，并与纯合成夹具结果分列。

项目现有静态校验脚本需要更新，因为新版本已经改变原先的交付边界和入口集合；只通过旧静态检查不能证明新闭环成立。[S9]

---

## 19. 一轮实际交互示例

以下为期望用户体验，不代表现有命令已经具备这些能力。

```text
$atk-init
检查本地 Agent 的调用入口和 Skill 自动加载方式。
本轮允许直接修改 skills/schema-generation/，其他代码和评分标准冻结。
在干净 B0 上串行累积优化；通过的候选提交为本地检查点，失败恢复父版本。
不推送；最终验证不通过时恢复 B0。

$atk-eval
导入 ./exports/langfuse-observations.json，保留完整工具轨迹。

$atk-diagnose
分析根因并整理 P1、P2、P3，标出能在本地重放的任务。

$atk-dataset
准备针对性回归任务、保护集和本轮完整验证集。

$atk-eval
执行并判定当前基线 B0。

$atk-optimize
先为 P1 生成 C1，直接在当前已接受版本上修改。

$atk-validate
对比 C1 与其父版本，同时检查保护集和此前已修复任务。

$atk-decide
通过则 keep 并提交；失败则 reject、保存证据并恢复父版本。
随后对 P2、P3 依次重复 optimize → validate → decide。

$atk-validate
对比最终累计版本与 B0，执行完整验证及计划内重复运行。

$atk-decide
通过则完成本轮，报告最终 commit 与总收益；不通过则按约定回退。
```

用户可一次授权“按上述范围处理 P1–P3，通过就提交、失败就回退，最后验收总效果”，由当前会话执行有限序列，无须逐项再次确认。未决候选先完成决策和恢复；不能先批量叠加所有修改再补记逐项验证。

若诊断发现工具或运行时问题超出允许范围：先以有预算的最小检查取证，保存 `external_handoff` Issue 和复验条件；依赖该问题的候选暂停，其他独立问题继续。外部修复后，用户提供修复身份，`atk-eval`／`atk-validate` 在关联的新轮验证原失败和保护集，`atk-decide` 按证据记录是否解决。具体服务名称只出现在项目配置与 Issue 中，不进入通用工作流实现。

---

## 20. AI 开发执行准则

本文件是 vNext 的规范来源。旧 README、旧 PRD、旧 Skill 和旧测试若与本文冲突，应一并修改或删除，不能为了兼容旧实现破坏新职责。

开发应先建立数据与 Git 安全契约，再接入推理性 Skill。任何新增自动化能力必须仍然运行在当前用户授权的会话与范围内；不得私自加入模型 API、子 Codex 执行器或后台调度。

实现报告必须列出：完成的工作包、删除的旧模块、新产物协议、运行的测试、未通过或未执行的测试、已知限制。没有真实 Langfuse fixture 或真实加载验收时，要如实记录，不标记 T34 完成；只有 Schema／状态测试时，不得把 T35–T39 的诊断能力验收记为通过。

最终 Definition of Done：**两种业务对象、三种证据入口、一次多根因多候选调优、原工程内的逐项边际及最终验证、有效候选 commit、失败恢复和已接受链后缀回退均有对应实现与证据；能在已知原因案例中区分 Skill／Agent、工具、运行时、评测错误与证据不足，并完成一次范围外组件问题交接、修复身份登记和新轮端到端复验。** 不以特定业务服务的接入代替通用契约验收，也不把本期完成等同于已证明线上普遍提升。

---

## 21. 参考资料

以下资料用于核对既有仓库边界和外部工具语义。本文新增的 Round、Candidate、累积检查点链、门禁及内部数据契约是 ATK vNext 的设计决定，不是声称上游项目已经提供的功能。

- [S1] [Agent Tune Kit README](https://github.com/hustyichi/agent-tune-kit/blob/main/README.md)
- [S2] [现有 atk-init](https://github.com/hustyichi/agent-tune-kit/blob/main/skills/atk-init/SKILL.md)
- [S3] [现有 atk-tune](https://github.com/hustyichi/agent-tune-kit/blob/main/skills/atk-tune/SKILL.md)
- [S4] [项目 pyproject.toml](https://github.com/hustyichi/agent-tune-kit/blob/main/pyproject.toml)
- [S5] [Langfuse：Export from UI](https://langfuse.com/docs/api-and-data-platform/features/export-from-ui)
- [S6] [Langfuse：Observability Data Model](https://langfuse.com/docs/observability/data-model)
- [S7] [Git：git-restore](https://git-scm.com/docs/git-restore)
- [S8] [Git：git-commit](https://git-scm.com/docs/git-commit)
- [S9] [现有静态校验脚本](https://github.com/hustyichi/agent-tune-kit/blob/main/scripts/validate_skill_pack.py)
- [S10] [现有版本与确认规则](https://github.com/hustyichi/agent-tune-kit/blob/main/docs/shared-versioning-and-confirmation.md)

资料核对日期：2026-09-27。正式实现时应把真实导出夹具、开发起始 commit 和依赖版本一并纳入验收记录。
