# Agent Tune Kit vNext 使用指南

最后核对：2026-09-28。适用于 PyPI 的 1.0.0 版本及本仓库 checkout。产品约束以[正式改造方案](agent-tune-kit-vnext-refactor-plan.md)为准，已实现范围及真实验收状态以[实现记录](vnext-implementation-report.md)为准。

## 1. 选择入口

| 当前材料与目标 | 使用路径 |
|---|---|
| 只有已有结果或 Langfuse 导出，暂不运行 Agent | `atk-init` 的 `analysis_only` → `atk-eval` 导入／判定 → `atk-diagnose` → 无采用时 `atk-decide` 关闭 Round |
| 有本地可运行的既有 Agent，要验证一次修改 | `atk-init` → `atk-dataset` → `atk-eval` 基线 → `atk-diagnose` → `atk-optimize` → `atk-validate` → `atk-decide` |
| 一轮处理多个问题 | 每个候选依次执行 `atk-optimize → atk-validate → atk-decide`，随后对累计版本与 B0 做最终验证 |
| 外部工具或服务有缺陷 | `atk-diagnose` 保存本地交接；修复后新建关联 Round，运行直接组件探针及端到端回归，再用 `atk-validate`／`atk-decide` 收尾 |

ATK 面向**已有** Agent。用户通过七个 Codex Skill 表达目标；当前 Codex 会话负责诊断、判定和修改，本地工具负责确定性的记录、运行与 Git 检查点。ATK 不自动推送、部署、发消息或跨仓修改。

## 2. 开始前

1. 在目标项目确认 Agent 入口、依赖、工作目录、外部写入、缓存、实际加载的业务 Skill／Prompt／代码，以及可复现的任务输入。正式优化需要干净的 Git 基线 B0；仅导入分析不需要 Git。
2. 让 `atk-init` 记录项目配置及 `.atk/runtime.md`，生成 `.atk/adapters/runner.py`。若 Agent 有外部写入，先配置已核查的测试环境、替身或获批保护措施；一条说明文字本身不提供隔离。
3. 准备 Case、判据及正反校准例。输入可来自 CSV、JSONL 或明确映射的原始结果；不要把 Agent 的原答案直接当 Ground Truth。保护集和独立 holdout 要按来源任务／会话分组。
4. 在冻结 Round 前写明允许修改的路径、保护路径、Issue 与 Case 集合、组件身份、重复次数、超时、并发、预算、回退规则，以及旧 Revision 重放时的缓存／制品准备方式。波动或未知的 Agent 默认最终每侧运行三次；缩至两次需要记录依据。

从 PyPI 安装请运行 `uv tool install agent-tune-kit`、`atk install` 和 `atk status`，再在 Codex `/plugins` 启用。升级 CLI 时运行 `uv tool upgrade agent-tune-kit`；建议随后运行 `atk install` 更新 Skill，也可以稍后再做。源码 checkout 可在仓库目录运行 `uv run --frozen atk install`。Skill 调用 CLI 时传入自身插件根目录；版本不同只提示，不阻止执行。

旧 `.atk/results/vN/` 不自动迁移或删除。目标工程若已有旧 `.atk`，初始化会停止；先保留旧目录并人工决定迁移方案，或改用干净的目标工程，不要覆盖旧数据。

## 3. 准备 Case 与附件

`atk-dataset` 通过显式字段映射生成不可变 Dataset。下面的 JSONL 是最小示意；附件路径相对数据文件所在目录解析，也可使用绝对路径：

```jsonl
{"id":"case-1","input":"分析这份文档","expected":"给出有依据的摘要","source_group_id":"task-1","usage":"optimization","attachments":["document.txt"]}
{"id":"case-2","input":"不要泄露凭证","source_group_id":"task-2","usage":"protection","attachments":[]}
```

调用 `store_dataset` 时把 `id`、`input`、`expected`、`source_group_id`、`usage` 和 `attachments` 显式映射到来源列；CSV 的附件单元格使用 JSON 数组字符串。导入时记录附件解析后路径和实际 SHA-256，并纳入 Case 指纹。冻结新轮与运行前会核对文件，默认 runner 在每次尝试前后再核对；原文件缺失或内容变化时，旧 Dataset 不可重放。要使用新内容，请创建新 Dataset；若原 Round 已冻结，还须新建 Round，不能让旧 Validation 沿用原 Case ID。

默认 runner 将本次 Case 的附件清单写入尝试目录的 `attachments.json`。目标 Agent 的命令需使用 `{attachments_file}` 参数读取它；清单项包含 `path` 和 `sha256`。`input_file` 仍只承载业务输入，不包含 Ground Truth、判定规则或诊断答案。若自定义 runner，保持相同的附件核对及交付约定。

## 4. 取证、诊断与候选

`atk-eval` 必须明确选择 `run`、`import` 或 `reassess`。`run` 在目标环境执行 Agent，形成 EvidenceBatch 和 Execution；`import` 只接入已有批量结果或 Langfuse Trace／Observation／Score；`reassess` 对原证据创建新 Assessment，不重新运行 Agent。导入时保存来源、字段映射、过滤范围及脱敏规则；外部分数只作证据，不直接变成 ATK 的通过率。

Assessment 按冻结的 `evaluation_spec` 为每条记录的每个维度写出有效性、判定和证据引用。没有可信任务效果判据时，把该维度记为 `unknown`；输出存在不等于成功。新判定规则需要正反校准例和新 Assessment。判据变化后，旧对照不再直接可比。

`atk-diagnose` 应区分 Agent／业务 Skill、工具、运行时、数据或评分器责任，并记录竞争解释和缺失证据。诊断探针须有冻结的命令、Case、隔离依据和独立预算；它不是原始执行，也不参与正式效果统计。若问题属于范围外组件，只在本地保存 `external_handoff` 与复验条件，不把 Agent 规避写成组件已修复。

`atk-optimize` 一次只处理一个主要 Issue。`prepare_candidate` 后仅编辑冻结允许的目标路径，`seal_candidate` 封存修改与 Revision；封存后不能继续改同一候选。一个 Round 最多一个未决候选，必须先决策并恢复或提交，才能准备下一候选。未封存草稿且工作区已恢复时，可用 `cancel_draft` 释放未决槽位；预算不返还。

## 5. 验证与决策

| 场景 | 必须核对 | 后续动作 |
|---|---|---|
| 单候选 | 候选与精确父版本的目标 Case、保护 Case、此前修复 Case，及冻结必需维度 | `pass` 才可正常 `keep`；失败、回归或证据不足时 `reject/defer` 或暂停 |
| 最终累计效果 | 当前已接受 Revision 与原 B0 的完整冻结 Case 集、重复槽位和预算 | 通过才可 `finish_round: complete`；否则按冻结规则回退或暂停 |
| 外部组件修复 | 新 B0 的直接组件探针覆盖许可中的全部 Case／重复次数及必需维度，再做受影响 Case 与保护集的端到端评判 | 两项完整通过后 `complete_external_fix`，再更新原 Issue 的解决证据 |

直接组件检查的维度由冻结探针许可及其 Assessment 规格决定；`validate_external_fix` 不接受临时选择 `direct_dimension`。任何必需维度失败、未知或缺失，或只引用一部分通过的探针记录，都不能完成修复验证。

`atk-decide` 的 `keep` 需要匹配的通过 Validation；明确记录的 trial override 仍须保留原 Validation 结果。已封存但尚无 Validation 的候选可以 `reject` 或 `defer`：同时填写 `reason` 和 `validation_missing_reason`，保存 Decision 与 Operation 后受控恢复父内容。已有该候选的 Validation 时必须引用它。`reject/defer` 不生成失败提交；`keep` 在原工程创建本地检查点 commit。整轮没有采用候选时，用 `close_without_adoption`，不创建空 commit。

进程或 Git 操作中断时，先用 `inspect_or_recover_operation` 核对操作 ID 与文件身份。未知文件内容、HEAD 或暂存区变化需要人工调查，不能使用 `reset --hard` 或 `clean -fd` 掩盖现场。暂停使用 `transition_round: pause`，恢复前检查其他活跃 Round、配置与工作区。

## 6. 内部接口的最小示例

Skill 会生成 JSON 请求并调用 `atk internal <operation> --plugin-root <当前加载的插件根目录> --request <文件> --output <文件>`。这是确定性接口，不是第二套公开调优命令。每个请求都要含目标工程的绝对 `project_path`；返回后检查 `status`、`error_code`、`next_required_action` 和 `artifact_refs`，使用返回的明确 ID，不找“最新目录”。

例如，纯导入分析可先把以下内容存入项目外的 `init-request.json`：

```json
{"project_path":"/absolute/path/to/agent","analysis_only":true}
```

安装 CLI 后可执行（把插件根目录替换为实际安装位置）：

```sh
atk internal initialize_project --plugin-root /absolute/path/to/agent-tune-kit --request /absolute/init-request.json --output /absolute/init-response.json
```

从本 checkout 则执行：

```sh
uv run --frozen atk internal initialize_project --plugin-root /absolute/path/to/agent-tune-kit --request /absolute/init-request.json --output /absolute/init-response.json
```

这一操作只建立分析存储。若要运行诊断探针，需要另配运行命令、组件、外部副作用保护、分析 Round 中的探针许可和预算；若要正式评测与优化，需要 Git 基线及完整运行配置。完整计划与判定明细由对应 Skill 按目标工程事实生成，不要复用示例 ID、命令或评分值。

## 7. 使用结果的边界

离线单测与合成 E2E 证明协议能运行，不能证明真实业务收益。当前项目的真实多 Case 效果、有效候选采用提交、独立审核的责任层诊断以及真实外部组件修复闭环仍待验收；发布决策需参照[实现记录](vnext-implementation-report.md)中的 W8 状态。七个 Skill 的操作细节见[共享流程](../skills/WORKFLOW.md)及各 Skill 文档。
