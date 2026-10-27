# Agent Tune Kit

简体中文 | [English](README.en.md)

[![PyPI](https://img.shields.io/pypi/v/agent-tune-kit.svg)](https://pypi.org/project/agent-tune-kit/)

Agent Tune Kit 是一个**本地 Codex 插件**，用于把你已有的本地 Agent 从“能跑”推进到“可评测、可诊断、可迭代调优”。

它围绕两件事展开：先把评估数据集整理成可复用、可人工校准的资产；再把 Agent 的批量评测、异常发现、报告分析和调优改动串成一个可重复的闭环。

## 架构图

![Agent Tune Kit 工作流程：按需使用 atk-dataset 准备数据、atk-init 接入 Agent；核心闭环为 atk-eval → atk-diagnose → atk-report → atk-optimize → 再次评测。数据与结果浏览均为可选，未判定不算通过。](docs/assets/architecture.svg)

## 适合谁

适合已经拥有本地 Agent、希望开展评测与调优的开发者。开始前请准备：

- 一个本地 Agent、聊天机器人、工具调用 Agent 或 RAG Agent。
- 一份小型评估数据，推荐 CSV；5 到 20 条样例就能开始。
- 一些可以判断好坏的输入、期望答案或人工可验收结果。
- 想让 Codex 协助定位弱点，并调 prompt、代码、参数或工具配置。

## 项目价值

Agent Tune Kit 的价值不只是“跑一次测试”，而是把 Agent 调优拆成两条清晰路径：

- **数据集准备路径**：从业务描述、样例或规则准备数据集，保留已有答案字段，按依据补齐判断标准，用本地 HTML 质检，再根据人工反馈修正预期结果。数据集会沉淀在 `.atk/datasets/`，不绑定某一轮测试结果。
- **Agent 评测与调优路径**：把已有 Agent 接入 runner，批量运行评测，找出异常样本，生成分析报告，浏览失败样本，然后让 Codex 基于证据调优 Agent。每一轮结果写入 `.atk/results/vN/`，方便跨轮验证是否真的变好。

这意味着你可以把 Agent 调优从一次性的主观试错，变成带有样本、结果、报告和调优记录的工程流程。

## 安装

先安装 ATK CLI，再安装配套 Skills：

```sh
uv tool install agent-tune-kit
atk install
```

或使用 `pipx`：

```sh
pipx install agent-tune-kit
atk install
```

兼容一次性安装命令 `uvx --from agent-tune-kit atk install`；日常使用 Skill 需要 PATH 中持续可用的 `atk` 命令。

如果希望使用本地源码版本，先将所需分支的最新代码更新到本地，再在 **ATK 仓库根目录**执行：

```sh
uv tool install --reinstall .
atk install
```

`.` 表示从当前目录构建并安装 ATK 包；`--reinstall` 确保版本号未变时也重新安装本地改动。后续更新代码后，重新执行这两条命令即可。`atk install` 在安装校验成功后自动清理个人 marketplace 下 ATK 的各版本缓存，同版本重装也会刷新。安装失败时保留原缓存；缓存清理失败会返回错误。

安装完成后，刷新 Codex 插件列表：

```text
/plugins
```

选择并启用 `Agent Tune Kit`，然后重启 Codex 并新建会话。旧会话不会自动更新已读取的指令；重装应安排在正在运行的 ATK 任务结束后。

运行 `atk status` 可检查安装目录与缓存的文件内容是否一致。缓存尚未生成时会提示等待 Codex 加载；缓存不一致、不可读取或安装不完整时返回退出码 `1`。内容一致或等待生成时返回 `0`，但这不代表插件已启用或会话已加载最新指令。详情见[安装与缓存说明](docs/skill-template-pack-usage.md#local-plugin-install-and-smoke)。

## 两条核心路径

下面这些命令都在**你的 Agent 项目**里运行，不是在本仓库里运行。

理想情况下，你已经有一个 Codex 能读取和修改的本地 Agent 项目，以及一份评估数据集。数据集建议优先使用 CSV；字段名不必严格固定，Codex 会根据内容判断输入、期望结果和评测方式。

### 路径 A：数据集准备（按需）

用一个入口准备或修订评测数据；已有合适的数据可以直接复用：

```text
$atk-dataset 使用 data/eval.csv 准备评测数据，保留原字段和已有答案，只补充缺失且有依据的判断标准。
```

也可以提供业务示例，或直接说明“修改 atk_id 为 7 的预期结果”。构建样本、补充判断依据和反馈修订由同一个 Skill 按需完成，无需依次调用多个命令。

- 默认产物仍是 `.atk/datasets/dataset.csv`，保留已有字段、有效 `atk_id`、行顺序和未涉及的数据。
- 直接复用 `expected`、`label`、`acceptance_criteria` 或 `ground_truth`，不强制新增规范答案列或选择全局答案风格。
- 缺少可信判断依据的样本保留缺口，下游标为未判定，不编造答案。
- 需要人工浏览时再调用 `$atk-visualize-dataset`。原有 HTML 和 `dataset_review.csv` 反馈格式继续可用，反馈回到 `$atk-dataset`；直接对话反馈无需先导出文件。

准备阶段不运行 Agent、不创建结果版本。未接入时继续 `$atk-init`；已有兼容 runner 时继续 `$atk-eval`。数据或标准修订不改写历史结果，也不等于 Agent 改善。

### 路径 B：Agent 评测与调优

当你已经有可运行 Agent 和评估数据集时，走这条调优闭环：

```text
$atk-init 我的 Agent 入口是 scripts/agent.py，评估数据是 data/eval.csv
$atk-eval
$atk-diagnose
$atk-report
$atk-visualize-failures
$atk-optimize
```

| 命令 | 作用 | 关键产物 |
| --- | --- | --- |
| `$atk-init` | 接入已有 Agent 和评估数据，生成 V2 适配文件并配置证据保留策略，把数据集规范化到 ATK 固定位置 | `.atk/runner/eval_runner.py`、`.atk/datasets/dataset.csv` |
| `$atk-eval` | 由共享执行引擎运行批量评测，分配结果版本并保存执行证据 | `.atk/results/vN/eval_results.csv`、`run.json`、`executions/` |
| `$atk-diagnose` | 统一判断异常并初步定位原因 | `.atk/results/vN/failure_cases.csv`、`judgments.csv` |
| `$atk-report` | 生成当前轮分析报告，并在有上一轮时做跨版本验证 | `.atk/results/vN/report.md` |
| `$atk-visualize-failures` | 生成本地离线 HTML，搜索、筛选、复核失败样本 | `.atk/results/vN/failure_cases.html` |
| `$atk-optimize` | 基于报告和失败样本调 prompt、代码、参数或工具配置 | Agent 改动、`.atk/results/vN/tuning_plan.md` |

`$atk-diagnose` 统一使用规则检查与 Codex 语义判断。已有规则按需复用，需要时在同一步准备并执行，无需选择模式。
它保留确认失败的 `failure_cases.csv`，并生成逐条 `judgments.csv`（通过／失败／未判定及理由）。
未判定样本不会被算作通过，也不混入 `--only-failures`。旧结果缺少判定记录时，仍可查看已知失败，其余状态为未知。
之后继续 `$atk-report` 汇总归因与跨轮复核，再用 `$atk-optimize` 调优。

#### 验证是否变好

调优后再跑一轮。常见做法是只重跑上一轮失败样本：

```text
$atk-eval --only-failures
$atk-diagnose
$atk-report
```

新结果会写入新的 `.atk/results/vN/`，已有非空版本不会被覆盖，最高版本目录为空时可复用。对于 V2 结果，`--only-failures` 从当前评测目标提取有效失败判定，通过 `atk_id` 映射回 `.atk/datasets/dataset.csv`，排除失效判定与未判定样本；旧版结果继续支持从 `failure_cases.csv` 取失败 ID。纯证据导入版本不会成为评测目标。

从第二轮开始，有上一轮 `tuning_plan.md` 且实验条件可比时，`$atk-report` 会说明目标问题是已解决、部分解决、未解决，还是无法判断。只重跑失败集用于验证目标问题，不能代表全量通过率；必要时再运行 `$atk-eval` 检查全量回归。

### 本地调优上下文

ATK 可选读取本地私有的 `.atk/context.md`，用于保存用户沟通后确认的调优目标、Agent 行为标准、`ground_truth` 判定标准、人工反馈和调优决策。它不是数据集元信息或运行日志文件：字段名、行数、版本路径、执行统计等能从 `.atk/datasets/`、`.atk/results/` 或 runner 中恢复的信息不应写入这里。

`$atk-dataset` 在用户确认可复用标准变化时更新相关上下文；`$atk-diagnose`、`$atk-report` 和 `$atk-optimize` 使用这些标准。缺少 `.atk/context.md` 不会阻塞流程。

### 真值复核与裁判校准

数据集可按答案字段记录来源和有效确认；输入、答案或相关标准变化会使确认过期。`atk dataset update --review-path dataset_review.csv` 可只导入确认而不改答案。`atk results calibrate` 用已复核输出检查规则或语义裁判，不调用 Agent 或创建评测轮次。

判定可绑定标准与裁判快照，重判保存历史，比较显式列出可比性差异。旧数据可读，但不自动标为已确认。复核记录、校准输入和判定字段见[可信度契约](docs/truth-and-assessment.md)。

## 输出结构

```text
.atk/
├── context.md             # 可选的本地调优共识
├── datasets/
│   ├── dataset.csv        # ATK 可运行数据集，包含 atk_id
│   └── dataset.html       # 可选的数据集浏览页
├── runner/
│   ├── eval_runner.py     # 项目适配文件
│   └── failure_rule.py    # 按需生成的规则
└── results/
    ├── v1/
    │   ├── eval_results.csv
    │   ├── run.json       # V2 运行清单
    │   ├── executions/    # V2 每项独立执行现场
    │   │   └── <id>/
    │   │       ├── execution.json
    │   │       ├── input.json
    │   │       ├── output.json
    │   │       ├── context/
    │   │       ├── events.jsonl
    │   │       ├── app.log
    │   │       └── artifacts/
    │   ├── judgments.csv
    │   ├── failure_cases.csv
    │   ├── failure_cases.html
    │   ├── report.md
    │   └── tuning_plan.md
    └── v2/
        └── ...
```

常用输出文件：

- `eval_results.csv`：保留原数据字段，并追加实际输出 `agent_output`、日志路径 `log_path` 等执行字段；`atk_truth` 不传入 Agent，答案列仍需由适配文件排除。
- `run.json`、`executions/`：V2 的运行清单与独立执行证据；同一业务 Case 可映射多条样本。旧 V1 结果不要求这些文件。
- `judgments.csv`：逐条通过、失败或未判定的判定、理由与可追溯引用；重判时原记录归档到 `judgment-history/`。
- `failure_cases.csv`：筛选出的异常样本。
- `failure_cases.html`：可选的异常样本浏览页面。
- `report.md`：本轮问题分析和调优建议。
- `tuning_plan.md`：Codex 本轮改了什么、为什么改。

以上为典型评测流程，各步骤按需生成产物。纯证据导入可没有 `eval_results.csv`；中断或失败时保留已生成的现场，不能据此声称执行完成。

## 常用 Skill

- `$atk-dataset`：统一准备或修订评测数据，保留原字段和 ID，按需处理反馈。
- `$atk-visualize-dataset`：将 `.atk/datasets/dataset.csv` 生成本地 HTML 浏览页，便于查看数据、复核答案及其来源。
- `$atk-init`：生成项目适配文件，接入 Agent 并配置执行证据保留策略。
- `$atk-eval`：运行评测并生成新版本结果。
- `$atk-diagnose`：统一识别失败与未判定样本，按需组合规则与语义判断并初步定位原因。
- `$atk-report`：生成分析报告和跨轮验证结论。
- `$atk-visualize-failures`：生成异常样本 HTML 浏览页。
- `$atk-optimize`：根据报告调优 Agent。

## 统一 CLI 能力

固定操作由 Python 包维护，Skill 继续负责语义诊断、报告分析和 Agent 优化。可在目标 Agent 项目中使用以下命令：

```sh
atk dataset prepare --source data/eval.csv
atk dataset inspect --direct --field expected --json
atk dataset update --direct --field expected --updates-path updates.csv
atk dataset update --review-path dataset_review.csv --field expected
atk eval --check --json
atk eval --limit 5 --concurrency 2
atk results inspect --json
atk results run-rule --version v1
atk results apply-judgments --version v1 --judgments-path judgments.csv --source-results .atk/results/v1/eval_results.csv
atk results compare --version v1 --ids 1 2 --json
atk results calibrate --samples-path calibration.csv --rule-path .atk/runner/failure_rule.py --output-path calibration-r1.csv --json
atk visualize dataset --no-open
atk visualize failures --version v1 --no-open
```

先用 `atk results inspect --json` 读取 `evaluation_target.version`，将示例中的 `v1` 及对应路径替换为实际目标版本。`run-rule` 需要已有规则；语义诊断由 `$atk-diagnose` 完成。浏览页已存在时，确认可替换后加 `--overwrite`。

新生成的项目适配文件使用统一执行引擎。旧完整 runner 在结果目录仍为旧格式时可继续运行；存在 V2 或导入证据版本时，需先迁移为托管适配文件。各命令参数见对应 `--help`。

## 本地执行证据

新适配文件声明 `ATK_MANAGED_RUNNER = True`、`ATK_ADAPTER_VERSION = 2` 和 `EVIDENCE["retain"]` 回调。每项执行在 `.atk/results/vN/executions/<id>/` 保存输入、输出、上下文快照、结构化事件和产物；同一业务 Case 的多条样本可共用一项执行。保留策略在证据写入前处理内容，模型、工具等调用轨迹需由项目适配代码显式接入。

现有 V1 适配文件继续支持，不自动迁移历史结果。

```sh
atk results evidence --version v1 --atk-id 1 --json
atk results evidence --execution-dir ./copied-execution --json
atk results import-evidence --source ./native-run --adapter ./importer.py --json
```

`atk results inspect --json` 分别返回最新运行 `latest_run` 和评测目标 `evaluation_target`；纯证据导入不会替换后者，也不能直接评分。诊断、报告和证据查询绑定评测版本，证据变化会使依赖它的判定失效。

导入已有本地运行无需再次调用 Agent；导入适配文件是受信任的 Python 代码，不在沙箱中执行。相同来源与内容可复用已校验的封存包；来源材料变化或封存后补采会被拒绝。导入不负责远程获取或自动清理，采集覆盖范围、保存成功和业务通过分别说明。

接入接口、来源限制、恢复与验证说明见[执行证据文档](docs/execution-evidence-and-tracing-plan.md)；外部导入适配文件参考[导入模板](templates/.atk/runner/import_evidence.py.md)。
