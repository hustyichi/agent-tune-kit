# Agent Tune Kit

最后更新：2026-09-28。状态：vNext 核心链路、10 Case 合成累积验收、轮次冻结／候选草稿／候选封存中断续恢复、中断批次／Git 内容恢复与转换／旧检查点复跑中断恢复／并发边界、Python editable／Node 链接加载及合成服务版本前后核查已在本地验证；Magic Workspace 单 Case 已按严格加载路径门禁复验，原生两 Case 批量基线通过；ATK 两 Case 候选对照因 B0 漏加载 Skill 而证据不足，完整验收待收口。详见 [实现与验收记录](docs/vnext-implementation-report.md)。

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

## 证据入口与预算

- **本地运行**：项目 `.atk/adapters/runner.py` 用目标项目的 Python 环境按显式尝试列表调用现有 Agent；每次尝试保存一个 Execution，超时会结束该尝试的同进程组子进程。Prompt、代码及业务 Skill 使用同一协议。Skill 的 `available`、`loaded`、`invoked` 状态分开记录；正式加载门禁核对实际绝对路径与指纹，隔离副本还须说明来源，无法证明时不能正常通过。诊断探针可在冻结许可内改用直接工具或项目测试命令；许可限定 argv、脚本指纹、工作目录、超时、Case、调用数和隔离依据，探针结果不参与正式效果对照。
- **预算与对照**：冻结轮次按尝试预留最终 B0／累计版本复验额度；授权重试生成关联旧 Execution 的新批次，保留全部尝试而不挑最好一次。冻结计划必须说明确定性或波动性的判断依据；波动或未知场景默认双方最终各执行 3 次，缩至 2 次须说明原因。效率或成本门槛仅在预先冻结且指标齐全时判定。独立 holdout 按来源组和里程碑记录暴露，已暴露的组不能在后续轮次重用。
- **运行身份**：冻结计划保存项目运行配置指纹，每批使用自己的配置快照。固定远端组件可声明只读 argv 数组 `version_command`、`expected_version` 和可选的 `version_timeout_seconds`；ATK 在批次前后执行并保存实际版本、命令指纹与失败原因。版本不符或批内漂移不能支持正式效果归因。正式对照要求固定组件身份可核验；未知身份或接入配置漂移返回证据不足或阻塞执行。
- **批量结果导入**：CSV/JSON/JSONL 按保存的字段映射导入，不重新运行 Agent；缺少尝试边界时不伪造 Execution。
- **Langfuse 文件导入**：支持 Trace bundle 和 Observation 行两种显式 profile。保留 Trace/Observation ID、父子关系、来源、缺失与过滤范围；外部分数只当证据，不直接换算 ATK 的通过率。原文件只读，默认遮蔽常见凭证字段；项目敏感字段需要追加脱敏键。
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

离线测试使用小型 Git 仓库和假 Agent，已覆盖 10 Case 四候选累积收益、已采用后缀回退、Case 输入变化后的双方重跑、批内固定文件漂移、其他安装副本误加载、链接源码、过期副本与跨进程提交恢复。Magic Workspace 的真实轨迹导入和单 Case 自动加载业务 Skill 已单独验证；桥接器补充绝对路径与副本来源后，已重新执行正式对照。Magic 原生两 Case 批量基线已通过。外部修复的新轮协议有合成测试，真实组件修复案例、已知责任层案例与有效的 ATK 真实多 Case 收益仍待验收；已有两 Case 候选在 B0 漏加载时被门禁拒绝，并保留明确缺口。尚未确认安全隔离的目标 Agent 外部写操作不得在正式跑测中启动。
