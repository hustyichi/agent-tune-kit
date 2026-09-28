# Agent Tune Kit

简体中文 | [English](README.en.md)

[![PyPI](https://img.shields.io/pypi/v/agent-tune-kit.svg)](https://pypi.org/project/agent-tune-kit/)

Agent Tune Kit（ATK）是一个**本地 Codex 插件**，帮助你评测已有 Agent、调查失败原因，并验证 Prompt、业务 Skill、代码或配置的修改是否有效。

你在 Codex 中描述目标，由当前会话分析证据和修改项目；ATK 负责保存评测结果、对比修改前后的表现，以及管理可恢复的本地 Git 检查点。

## 什么时候用

- **已经有一个能运行的 Agent**，想知道它在哪些任务上表现不好，以及应该改哪里。
- **已经有批量结果或 Langfuse 导出**，想先分析问题，不重新运行 Agent。
- **准备修改 Prompt、业务 Skill、代码或配置**，想确认目标问题得到改善，同时没有破坏原本正常的任务。

ATK 接入你现有的 Agent，不要求重写成另一套框架。每次修改都保留对应的证据和决策，方便回看为什么采用或放弃。

## 安装

需要本地 Codex、Python 3.11+ 和可用的 `uv`。在终端安装当前 1.0.0 版本：

```sh
uvx --from agent-tune-kit==1.0.0 atk install
```

然后在 Codex 中输入：

```text
/plugins
```

选择并启用 **Agent Tune Kit**，再打开你自己的 Agent 项目。以下 `$atk-*` 是在 **Codex 对话中输入的 Skill 调用**，不是终端命令，也不需要在 ATK 源码仓库里执行。

包版本与下载见 [PyPI](https://pypi.org/project/agent-tune-kit/)。

> **从 0.x 升级：** 1.0.0 更换了 Skill 和数据结构，不自动迁移或删除旧 `.atk/results/vN/`。遇到旧 `.atk` 时初始化会停止；请先备份并决定如何保留旧数据，或使用干净的目标工程。不要直接覆盖旧目录。

## 第一次使用：评测并改进 Agent

准备好 Agent 的运行入口、少量有代表性的任务，以及你判断结果好坏的依据。正式评测与调优需要干净的 Git 工作区；如果 Agent 会写数据库、发消息或调用有副作用的工具，还需要可核查的测试环境或隔离措施。

下面以 `scripts/agent.py` 和 `data/eval.csv` 为例，请替换成你的实际路径。按步骤在 Codex 中调用，等上一项完成后再继续。

### 1. 接入项目，准备数据

```text
$atk-init 我的 Agent 入口是 scripts/agent.py，请调查运行方式并完成接入。允许修改 prompts/，不要修改其他业务文件。
$atk-dataset 使用 data/eval.csv 建立评测数据，确认输入、预期结果和判定标准，并保留用于检查回归的任务。
```

Codex 会调查依赖、输入输出、实际加载的业务 Skill 和外部副作用，并生成项目本地 runner。数据集中的一条任务称为一个 Case；除了需要改善的 Case，还应保留原本正常的 Case，检查修改是否引入回归。

没有标准答案也可以分析，但只有具备可靠判据的维度才能明确判定成功或失败。不要直接把 Agent 的原回答当成正确答案。

### 2. 跑基线，定位问题

```text
$atk-eval 运行基线评测，先明确本轮的判定标准、运行次数、超时和预算。
$atk-diagnose 分析失败证据，区分 Agent 行为、工具、运行环境和数据问题，指出优先处理的问题。
```

基线是修改前的表现，用于后续对照。ATK 会保存实际输出、逐项判定和问题证据；模型认证失败等环境故障不能当成有效的效果对照。

### 3. 修改、验证，再决定是否保留

```text
$atk-optimize 针对已确认的首要问题，在允许的路径内准备一个候选修改。
$atk-validate 对比候选与修改前版本，检查目标任务是否改善、保护任务是否退化。
$atk-decide 根据验证结果决定保留、拒绝或暂缓这个候选。
```

一轮只处理一个未决候选。正常保留需要通过验证，并在你的项目中创建本地 commit；拒绝或暂缓时，保留证据并恢复候选之前的文件内容。

如果还有问题，重复这三个步骤。所有候选处理完后，还要验证**累计修改与本轮最初基线**的完整对照，再结束本轮：

```text
$atk-validate 执行本轮最终验证，对比累计版本与最初基线，覆盖完整的冻结任务集和重复计划。
$atk-decide 根据最终验证结果结束本轮，列出采用的修改、最终提交和仍未解决的问题。
```

ATK 不会自动 push、发布或部署。

## 只有现成结果？先做分析

已有 CSV、JSON、JSONL 批量结果，或 Langfuse JSON/JSONL/CSV/`.gz` 导出时，可以先导入，不必配置 Agent 运行命令，也不需要 Git：

```text
$atk-init 本次只分析已有结果，使用 analysis_only 模式，不运行 Agent。
$atk-eval 导入 data/traces.json，确认文件类型、字段映射和脱敏规则，再按明确的判据评估。
$atk-diagnose 分析失败原因，标明缺失证据与需要进一步验证的解释。
$atk-decide 本轮不采用修改，保存分析结果并关闭本轮。
```

导入不会重新运行 Agent，外部评分也不会直接变成 ATK 的通过率。之后需要调优时，可以补齐运行配置和 Git 基线，保留已有证据继续工作。具体格式见[使用指南](docs/vnext-usage-guide.md)。

## 七个 Skill 速查

| Skill | 你想做的事 |
| --- | --- |
| `$atk-init` | 接入已有 Agent，或建立只导入结果的分析项目 |
| `$atk-dataset` | 整理任务、预期结果、附件和回归保护集 |
| `$atk-eval` | 运行评测、导入现有结果，或不重跑 Agent 重新判定证据 |
| `$atk-diagnose` | 调查失败原因，记录问题与待验证的解释 |
| `$atk-optimize` | 针对一个问题，在约定范围内准备候选修改 |
| `$atk-validate` | 验证单次修改、累计效果或外部组件修复 |
| `$atk-decide` | 保留、拒绝、暂缓、回退已采用的修改，或结束本轮 |

如果问题来自外部工具或服务，先用 `$atk-diagnose` 保存交接信息和复验条件。修复后开启关联的新一轮，同时检查组件本身和 Agent 端到端表现；仅在 Agent 侧绕过问题，不代表外部缺陷已解决。

## 结果在哪里看

结果保存在**你的 Agent 项目**的 `.atk/` 目录下：

| 位置 | 内容 |
| --- | --- |
| `.atk/runtime.md` | Agent 如何接入、运行条件与已知限制 |
| `.atk/datasets/<id>/` | 本次使用的不可变任务快照 |
| `.atk/evidence/<batch-id>/` | 实际运行或导入的结果、来源和执行状态 |
| `.atk/assessments/<id>/assessment.csv` | 每条记录、每个维度的判定和证据引用 |
| `.atk/rounds/<id>/` | 本轮计划、问题、候选、验证与决策记录 |
| `.atk/knowledge/<id>/` | 带适用条件和证据的经验记录 |

可让 Codex 指出本轮具体文件，或从判定 CSV 生成本地 HTML 供浏览。重新判定会保存新的 Assessment，不覆盖旧评分。

需要中途停止时，让 Codex 暂停当前轮次并记录原因；恢复时先检查工作区和运行配置。发生中断时保留现场，按操作记录恢复，不要手动删除 `.atk/` 或强制重置 Git。

## 更多说明

- [完整使用指南](docs/vnext-usage-guide.md)：附件、预算、并发、导入映射、暂停恢复和外部修复流程。
- [共享 Skill 流程](skills/WORKFLOW.md)：各 Skill 遵循的操作约定与内部接口。
- [实现与验收记录](docs/vnext-implementation-report.md)：已实现范围、验证证据和待验收项。
- [设计方案](docs/agent-tune-kit-vnext-refactor-plan.md)：数据模型和验证协议。

**当前限制：** 真实多 Case 收益、有效候选采用提交、独立责任层诊断及真实外部组件修复闭环仍待验收。离线测试通过不代表你的业务效果已得到证明，请以自己项目中的对照结果为准。

## 源码开发

在本仓库安装插件：

```sh
uv run --frozen atk install
```

运行开发检查：

```sh
uv run --frozen pytest -q
uv run --frozen ruff check .
python3 scripts/validate_skill_pack.py
uv build --no-sources
```

`atk internal ...` 是供 Skill 调用的内部接口。手动排查时，应使用与插件一致的版本：PyPI 安装对应 `uvx --from agent-tune-kit==1.0.0 atk internal ...`，源码安装对应 `uv run --frozen atk internal ...`。
