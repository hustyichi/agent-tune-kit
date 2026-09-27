# Agent Tune Kit

最后更新：2026-09-28。状态：vNext 核心链路和正式验收前复核的五组开发缺口已补齐并做本地回归：必需维度门禁、Candidate／Issue／目标 Case 绑定、正式超时冻结、未封存草稿撤销及中断最终批次续跑。此前的 10 Case 合成累积验收、Git 检查点恢复、默认 runner 并发、Python editable／Node 链接加载、合成服务版本核查和 Magic Workspace 接入验证仍有效。新版 ATK 正式两 Case 基线因模型认证失败记为 `infra_error`，尚未进入有效效果对照；W8 真实多 Case 收益与真实外部组件修复仍待验收。详见 [实现与验收记录](docs/vnext-implementation-report.md)。

2026-09-28 已补齐纯分析轮、暂停／恢复与最终阶段、默认 runner 并发；随后复核发现的五组缺口也已实现。现阶段重点是按[验收前缺口复核与修复](docs/vnext-implementation-report.md#正式验收前开发缺口复核2026-09-28)准备真实业务验收，不将离线门禁通过视为线上收益证明。

前阶段补齐了 Knowledge 来源组隔离、输入适用性复核、无 Ground Truth 的逐维度判定、两个独立 Issue 的串行候选整轮，以及本地工具缺陷的直接复现、修复提交和新轮端到端复验；六份已知责任层合成材料已做同会话语义复核。发布检查使用冻结锁文件。真实多 Case 效果与真实业务组件修复后的新轮验收仍按实现记录中的限制处理。

简体中文 | [English](README.en.md)

Agent Tune Kit（ATK）是用于**已有本地 Agent** 的 Codex 插件。当前会话负责调查、语义判断和修改；本地 Python 工具负责证据导入、运行记录、判定明细、对照门禁和 Git 检查点。可调资产是业务 Skill、Prompt、Agent 代码或配置；ATK 自己的流程 Skill 不属于候选资产。

> 本仓库正在实现 [vNext 改造方案](docs/agent-tune-kit-vnext-refactor-plan.md)。这是破坏兼容的开发状态，未发布为 PyPI 新版本。旧版 `.atk/results/vN/` 和旧 Skill 不自动迁移或删除；新版初始化遇到旧 `.atk` 会停止。

## 安装与入口

已发布版本的本地插件安装命令仍为：

```sh
uvx --from agent-tune-kit atk install
```

要试用此开发 checkout，请在本仓库执行 `uv run --frozen atk install`，再在 Codex 的 `/plugins` 中启用 Agent Tune Kit。只有七个公开 Skill：

| Skill | 职责 |
| --- | --- |
| `atk-init` | 调查已有 Agent 的调用、依赖、副作用及业务 Skill 自动加载；建立项目本地 runner |
| `atk-dataset` | 建立不可变的 Case 数据集，整理 Ground Truth、重放条件和来源分组 |
| `atk-eval` | 显式运行、导入或重判；接收批量结果及 Langfuse JSON/JSONL/CSV/`.gz` 文件 |
| `atk-diagnose` | 从证据调查竞争解释，记录多个 Issue、范围外交接和复验条件 |
| `atk-optimize` | 在干净的 Git B0 上按允许路径准备并封存一个候选 |
| `atk-validate` | 对比候选与父检查点、累计版本与 B0；外部修复后验证新 B0 |
| `atk-decide` | 保留并提交、拒绝并恢复、回退已接受后缀或结束本轮 |

核心顺序：`atk-init → atk-dataset（按需）→ atk-eval → atk-diagnose → [atk-optimize → atk-validate → atk-decide]×N → atk-validate（最终）→ atk-decide`。一轮只有一个未决候选；通过的候选在原工程形成一个本地 commit，失败候选保存证据后恢复父检查点。最终门禁以冻结的完整 Case、保护集与重复计划验证累计效果。不会自动 push、发布、部署、改写 Git 历史或修改其他仓库。

范围外组件修复后，可将关闭的交接 Round 链接到新 Round，登记修复制品身份，在新 B0 分别执行获授权的组件直接探针与受影响 Case／保护集的端到端复验。两份 Assessment 都通过后才能完成新 Round 并把原 Issue 标为已解决；Agent 端规避仍保留原缺陷为开放。旧 Revision 在原目录复跑前须冻结缓存／制品重建命令或无状态依据。

纯导入分析可用 `initialize_project` 的 `analysis_only=true` 初始化，无需 Git 或运行命令；`create_round` 默认沿用该模式，导入、判定、诊断后可直接关闭。需要优化时，以 `configure_runtime=true` 补齐运行配置，`freeze_round` 再绑定干净的 B0，保留已有证据和 Issue。

`transition_round` 支持 `pause`、`resume`、`start_finalizing`，每次记录原因。暂停保留候选并允许切换轮次；恢复前须停用其他活跃轮次，并检查冻结配置、runner、操作日志、Git 和文件内容；首个最终复跑自动进入 `finalizing`，阻止新增候选和增量评测。已暂停轮次须恢复后才能继续或关闭。

## 证据入口与预算

- **样本并发**：冻结计划和正式运行请求使用相同的正整数 `concurrency`，默认 1；默认 runner 按上限并发，每次尝试使用独立输出目录。批次中断保存已完成、运行中和未启动的尝试，并清理仍在运行的 Agent 进程组。不同 Revision 仍串行；会话和外部写入隔离由目标工程提供。
- **冻结判定与续跑**：正式运行的 `timeout_seconds` 是冻结计划的一部分，默认 120 秒，左右两侧及重试必须一致；`primary_dimension` 默认 `task_success`，未显式指定 `required_dimensions` 时全部评分维度都必需。未知／无效必需判定不得通过，其他必需维度退化阻断主要维度收益。中断批次可用 `continue_batch_id` 继续：复用已确认记录，未启动槽位按额度补跑，状态未知槽位需记录明确的用户重跑授权；旧批次保持原样。
- **本地运行**：项目 `.atk/adapters/runner.py` 用目标项目的 Python 环境按显式尝试列表调用现有 Agent；每次尝试保存一个 Execution，超时会结束该尝试的同进程组子进程。初始化须显式声明 `external_effects`，确认无外部写入时填 `[]`；已声明的写入须记录测试环境、替身或获批保护措施及核查依据，否则拒绝重放。接入方可用 `infrastructure_exit_codes` 声明桥接器的非零退出码，将认证等运行环境故障标为 `infra_error`，允许按冻结额度重试，并阻止它成为有效效果证据。未知 Execution 状态会使批次成为 `partial`。Prompt、代码及业务 Skill 使用同一协议。Skill 的 `available`、`loaded`、`invoked` 状态分开记录；正式加载门禁核对实际绝对路径与指纹，隔离副本还须说明来源，无法证明时不能正常通过。诊断探针可在冻结许可内改用直接工具或项目测试命令；许可限定 argv、脚本指纹、工作目录、超时、Case、调用数和隔离依据，探针结果不参与正式效果对照。
- **预算与对照**：冻结轮次按尝试预留最终 B0／累计版本复验额度；授权重试生成关联旧 Execution 的新批次，保留全部尝试而不挑最好一次。冻结计划必须说明确定性或波动性的判断依据；波动或未知场景默认双方最终各执行 3 次，缩至 2 次须说明原因。费用和工具调用门槛须在 `metric_sources` 中声明独立采集来源并由 runner 为每条记录标记相同来源；默认的 Agent `metrics.json` 只供诊断，不参与正式门禁。运行器自身计时仍可用于耗时门槛。独立 holdout 按来源组和里程碑记录暴露，已暴露的组不能在后续轮次重用。
- **运行身份**：冻结计划保存项目运行配置指纹，每批使用自己的配置快照。固定远端组件可声明只读 argv 数组 `version_command`、`expected_version` 和可选的 `version_timeout_seconds`；ATK 在批次前后执行并保存实际版本、命令指纹与失败原因。版本不符或批内漂移不能支持正式效果归因。正式对照要求固定组件身份可核验；未知身份或接入配置漂移返回证据不足或阻塞执行。
- **批量结果导入**：CSV/JSON/JSONL 按保存的字段映射导入，不重新运行 Agent；缺少尝试边界时不伪造 Execution。
- **Langfuse 文件导入**：支持 Trace bundle 和 Observation 行两种显式 profile。Trace／Observation／Score 分文件时，用 `file_roles` 按文件名声明 `trace`、`observation`、`score`；CSV 嵌套列用 `json_columns` 声明。非标准字段名用 `mapping.trace`、`mapping.observation` 或 `mapping.score` 的 `{标准字段名: 来源字段名}` 显式映射，冲突时拒绝导入。只有 `mapping.root_observation_name` 唯一匹配根 Observation 时，才可从它补足缺失的 Trace 输入／输出。导入保留事件 ID、父子关系、原始与解析时间、来源、缺失与过滤范围；外部分数只当证据，不直接换算 ATK 的通过率。相同文件更换映射或脱敏规则会产生关联修订。原文件只读，默认遮蔽常见凭证字段；项目敏感字段需要追加脱敏键。
- **诊断与派生视图**：`record_source_contract` 将项目内少量源码／契约行脱敏封存为诊断证据，同时分别保留源码版本与实际制品身份；`render_assessment_html` 从权威 CSV 重建经过转义的本地 HTML，不另存可独立改分的明细。

没有 Ground Truth 也能调查证据；只有具备固定判据的维度才能给出确定判定。`reassess` 创建新 Assessment，不重跑 Agent，不覆盖旧评分。只改评分规则时，可在旧轮关闭后显式复用相同 commit 与运行配置的 Revision，在新轮对原始执行统一重判。评分标准、被测边界、组件和运行条件变化会使旧对照失效；任务输入或固定运行条件变化时需要重新执行。

## 主要产物

```text
.atk/
├── project.json                # 项目运行配置与组件声明
├── runtime.md                  # 接入调查、加载证明和局限
├── adapters/runner.py          # 项目本地 runner
├── probes/                     # 可选的授权诊断脚本
├── datasets/<id>/              # 不可变 Case 快照
├── evidence/<batch-id>/        # records.jsonl、来源索引、执行状态
├── assessments/<id>/           # manifest.json + 唯一权威 assessment.csv
├── rounds/<id>/                # 冻结计划、Issues、候选、验证、决策和操作日志
├── source-exposure.json        # 跨轮 holdout 来源组暴露记录
└── knowledge/<id>/             # 带适用性和证据的经验修订
```

所有内部操作使用 `atk internal <operation> --request <JSON> --output <JSON>`；这是供七个 Skill 调用的确定性接口，不是另一套用户调优命令。机器产物用显式 ID 引用，不依赖“最新 vN”目录。被测 Agent 不应接触 Ground Truth、判定规则和诊断答案。

## 开发验证

```sh
uv run --frozen pytest -q
uv run --frozen ruff check .
python3 scripts/validate_skill_pack.py
uv build --no-sources
```

离线测试使用小型 Git 仓库和假 Agent，已覆盖 10 Case 四候选累积收益、已采用后缀回退、Case 输入变化后的双方重跑、批内固定文件漂移、其他安装副本误加载、链接源码、过期副本与跨进程提交恢复。Magic Workspace 的真实轨迹导入和单 Case 自动加载业务 Skill 已单独验证；桥接器补充绝对路径与副本来源后，已重新执行正式对照。Magic 原生两 Case 批量基线已通过。外部修复的新轮协议有合成测试，真实组件修复案例、已知责任层案例与有效的 ATK 真实多 Case 收益仍待验收；已有两 Case 候选在 B0 认证失败时被门禁拒绝，随后修正了接入分类并完成单 Case 真实复跑。尚未确认安全隔离的目标 Agent 外部写操作不得在正式跑测中启动。
