# Agent tune kit

简体中文 | [English](README.en.md)

Agent Tune Kit 是一个**本地 Codex 插件**，帮助你快速完成本地 Agent 的效果评测与调优。

如果你已经有一个能运行的 Agent，但还不确定它在哪些样本上表现不好、为什么不好、该怎么改，这个项目可以让 Codex 帮你走完一轮闭环：批量测试、发现异常、生成分析报告、修改 Agent，并在下一轮验证调优是否真的有效。

它的核心优势是**上手门槛低**：不要求你先设计复杂数据 Schema，也不要求你的 Agent 暴露统一接口。你只需要准备一个本地 Agent 项目和一小份评估数据，Codex 会阅读项目代码和数据样例，帮你生成适配当前项目的测试脚本和调优流程。

## 适合谁

适合你，如果你：

- 有一个本地 Agent、聊天机器人、工具调用 Agent 或 RAG Agent。
- 有一些测试问题、样例输入、期望答案或人工可判断的结果。
- 希望快速知道 Agent 的薄弱点，并让 Codex 协助改 prompt、代码、参数或工具配置。
- 希望每一轮调优都有结果文件和报告，方便后续对比。

不需要你提前准备复杂评测平台。第一次验证时，用 5 到 20 条 CSV 样例就可以开始。

## 使用前准备

你只需要准备：

- 支持本地 Codex 插件或 Skill 的 Codex 环境。
- Python 3。
- 一个可被 Codex 读取和修改的本地 Agent 项目。
- 一份简单评估数据，推荐 CSV。字段名不必完全标准，Codex 会尽量根据数据内容判断输入和期望结果。

建议在调优前做一次 git checkpoint，方便你对比或回滚。Agent Tune Kit 不做自动回滚。

## 快速开始：安装插件

先把仓库拉到本地，并进入项目目录：

```sh
git clone git@github.com:hustyichi/agent-tune-kit.git
cd agent-tune-kit
```

然后运行校验和安装预览：

```sh
python3 scripts/validate_skill_pack.py
python3 scripts/install_plugin.py --dry-run --smoke
```

确认预览结果正常后安装：

```sh
python3 scripts/install_plugin.py --apply --smoke
```

安装脚本会把插件加入 Personal marketplace，并写入或更新 `~/.agents/plugins/marketplace.json`。此时在 `/plugins` 里会看到 Agent Tune Kit 处于 `Available` 状态。

还需要在 Codex 里执行一次启用：

```text
/plugins
```

在插件列表中选择 `Agent Tune Kit`，按界面提示安装/启用。状态变成 `Installed` 后，`$atk-start` 等 Skill 命令才会出现在自动补全里。

如果状态已经是 `Installed`，但当前会话里仍然看不到 `$atk-start` 的自动补全，这是正常现象：Codex 通常会在会话启动时加载已安装插件的 Skill 列表，刚启用的插件不一定会被当前会话热加载。请重启 Codex，或关闭当前 Codex 会话后重新进入该项目，再输入 `$atk-start` 验证。

如果你不能使用本地插件，也可以走 legacy copy/register 路径：整体复制或注册本仓库，并保持 `skills/`、`templates/`、`docs/` 在同一相对结构下。

## 最短验证流程

下面这些步骤在**你的 Agent 项目**里完成，不是在本仓库里完成。

### 1. 让 Codex 看一下当前状态

在 Codex 中打开你的 Agent 项目，输入：

```text
$atk-start
```

它会告诉你现在应该做哪一步。第一次使用时，通常会建议你生成测试 runner。

### 2. 生成测试脚本

输入：

```text
$atk-setup
```

告诉 Codex 你的 Agent 大概从哪里启动、评估数据在哪里。Codex 会根据项目代码和数据样例生成：

```text
agent-tuning/runner/test_runner.py
```

这个脚本会保留你的原始数据列，并额外写入 Agent 的实际输出列 `agent_output`。

### 3. 跑一遍 Agent

输入：

```text
$atk-run
```

运行完成后会得到：

```text
agent-tuning/results/v1/results.csv
```

### 4. 找出异常样本

如果你希望 Codex 帮你判断哪些结果异常，直接用：

```text
$atk-filter
```

如果你有明确规则，例如“期望答案不等于输出就是异常”，可以用：

```text
$atk-filter-rules
```

异常结果会写入：

```text
agent-tuning/results/v1/abnormal_cases.csv
```

### 5. 生成分析报告

输入：

```text
$atk-report
```

Codex 会生成：

```text
agent-tuning/results/v1/report.md
```

报告会总结测试情况、异常样本、可能原因，以及建议优先调哪些问题。

### 6. 让 Codex 执行调优

输入：

```text
$atk-apply
```

Codex 会基于报告修改你的 Agent，并写入：

```text
agent-tuning/results/v1/tuning_plan.md
```

这个文件记录本轮想解决哪些异常、采取了什么调优手段、改了哪些文件。

## 怎么确认调优真的有效

调优后，再跑一次测试：

```text
$atk-run
```

这次会生成 `agent-tuning/results/v2/results.csv`。继续执行异常筛选和报告生成：

```text
$atk-filter
$atk-report
```

从第二轮开始，报告会读取上一轮的 `tuning_plan.md`，判断上一轮目标异常是已解决、部分解决、未解决，还是无法判断。这样你就能看到调优是否真的带来了效果。

## 一轮流程速记

```text
$atk-start
$atk-setup
$atk-run
$atk-filter
$atk-report
$atk-apply
```

下一轮从再次运行 `$atk-run` 开始。

## 你会看到的结果目录

```text
agent-tuning/
├── runner/
│   ├── test_runner.py
│   └── filter_abnormal.py
└── results/
    ├── v1/
    │   ├── results.csv
    │   ├── abnormal_cases.csv
    │   ├── report.md
    │   └── tuning_plan.md
    └── v2/
        └── ...
```

你通常只需要看 `results.csv`、`abnormal_cases.csv` 和 `report.md`。版本号由脚本自动管理，不需要手动指定。

## 可用 Skill

- `$atk-start`：检查当前进度，告诉你下一步。
- `$atk-setup`：生成适配当前 Agent 的测试脚本。
- `$atk-run`：运行测试脚本并生成当前版本结果。
- `$atk-filter`：让 Codex 判断异常样本。
- `$atk-filter-rules`：按明确规则筛选异常样本。
- `$atk-report`：生成分析报告和跨轮验证结论。
- `$atk-apply`：根据报告修改 Agent，并记录本轮调优计划。

## 当前边界

本仓库当前交付的是本地 Codex 插件和 Skill 模板包，包含 `.codex-plugin/plugin.json`、七个 Skill、runner/filter 模板、共享版本规则、文档、安装/冒烟工具和静态校验。

本阶段非目标：no public marketplace（不发布公共 marketplace）、no brand assets（不提供品牌资产/截图）、no one-click orchestration（不做一键编排）、no universal Schema（不强制通用数据 Schema）、no bundled example Agent/data fixtures（不内置示例 Agent 或数据集）、no automatic rollback（不做自动回滚或基线恢复）、no full E2E test suite（不提供完整端到端测试套件）。

## 仓库内容

- `.codex-plugin/plugin.json`
- `skills/atk-start/SKILL.md`
- `skills/atk-setup/SKILL.md`
- `skills/atk-run/SKILL.md`
- `skills/atk-filter-rules/SKILL.md`
- `skills/atk-filter/SKILL.md`
- `skills/atk-report/SKILL.md`
- `skills/atk-apply/SKILL.md`
- `templates/agent-tuning/runner/test_runner.py.md`
- `templates/agent-tuning/runner/filter_abnormal.py.md`
- `docs/shared-versioning-and-confirmation.md`
- `docs/skill-template-pack-usage.md`
- `docs/codex_agent_tuning_prd.md`
- `scripts/install_plugin.py`
- `scripts/validate_skill_pack.py`

## 校验与冒烟

```sh
python3 scripts/validate_skill_pack.py
git diff --check
python3 scripts/install_plugin.py --dry-run --smoke
python3 scripts/install_plugin.py --marketplace-path /tmp/agent-tune-marketplace.json --plugin-store /tmp/agent-tune-plugins --apply --smoke
```

校验器会在必需 Skill 章节、manifest 字段、安装器行为、PRD 引用、版本辅助函数片段、输出路径、非目标说明或调优/报告契约缺失时直接失败。
