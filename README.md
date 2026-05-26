# Agent Tune Kit

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

建议在调优前做一次 git checkpoint，方便你对比或回滚。Agent Tune Kit 不做 Agent 调优流程的自动回滚；安装器的 rollback 只恢复本地 marketplace/plugin-store 安装状态。

## 快速开始：安装插件

普通用户不需要先 clone 仓库。直接用 uvx 运行已打包的安装器：

```sh
uvx --from agent-tune-kit atk install
```

如果希望长期保留 `atk` 命令，可以先安装工具：

```sh
uv tool install agent-tune-kit
atk install
```

如果你使用 pipx：

```sh
pipx install agent-tune-kit
atk install
```

安装器会先校验打包后的插件 manifest，再把插件加入 Personal marketplace，写入或更新 `~/.agents/plugins/marketplace.json`，把打包 payload 复制到 `~/plugins/agent-tune-kit`，并默认执行本地 smoke/status 检查。它只证明本地文件和 marketplace 状态，不会绕过或修改 Codex 隐藏的 UI 启用状态。

常用辅助命令：

```sh
atk preview --smoke   # 只预览，不写入
atk status            # 查看本地安装状态和下一步提示
atk rollback --backup <backup-id>  # 只回滚 installer 管理的本地安装状态
```

如果安装时遇到已有 marketplace/plugin-store 冲突，交互式终端会先确认；非交互式替换必须显式使用 `--yes --force`，且替换前会创建备份并打印 rollback 命令。安装器只支持显式子命令，不保留旧入口；预览请使用 `preview`。

贡献者本地开发路径如下，仅用于修改本仓库本身：

```sh
git clone git@github.com:hustyichi/agent-tune-kit.git
cd agent-tune-kit
uv sync
uv run atk install
# 或：python3 scripts/install_plugin.py install
```

安装完成后，Agent Tune Kit 应该会在 `/plugins` 里可见/可用。

还需要在 Codex 里执行一次启用：

```text
/plugins
```

在插件列表中选择 `Agent Tune Kit`，按界面提示安装/启用。启用后，`$atk-status` 等 Skill 命令才会出现在自动补全里。

如果已在 `/plugins` 启用，但当前会话里仍然看不到 `$atk-status` 的自动补全，这是正常现象：Codex 通常会在会话启动时加载已启用插件的 Skill 列表，刚启用的插件不一定会被当前会话热加载。请重启 Codex，或关闭当前 Codex 会话后重新进入该项目，再输入 `$atk-status` 验证。

如果你不能使用本地插件，请先不要拆分复制单个 `skills/*` 目录；本仓库当前以本地 Codex 插件安装路径作为唯一推荐入口。

## 维护者发布到 PyPI

发布脚本参考 `agent-tune-cli` 的 release gate/publish 两段式设计：默认只做 dry run，只有显式加 `--publish` 才会上传。

先跑完整本地发布门禁；它会校验版本一致、运行静态校验和测试、用 `uv build --no-sources` 构建 wheel/sdist，并在仓库外安装构建产物后执行 `atk` smoke：

```sh
UV_NO_CONFIG=1 uv run python scripts/check-release.py
```

准备正式发布前，先生成干净的 `dist/` 产物但不上传：

```sh
UV_NO_CONFIG=1 uv run python scripts/publish-release.py
```

建议先发 TestPyPI：

```sh
export UV_PUBLISH_TOKEN='pypi-你的-testpypi-token'
UV_NO_CONFIG=1 uv run python scripts/publish-release.py --repository testpypi --publish
```

确认 TestPyPI 包可安装后，再发正式 PyPI：

```sh
export UV_PUBLISH_TOKEN='pypi-你的-pypi-token'
UV_NO_CONFIG=1 uv run python scripts/publish-release.py --repository pypi --publish
```

脚本会在上传前检查当前 `project.name` + `project.version` 是否已存在；如果已存在，请先 bump `pyproject.toml`、`.codex-plugin/plugin.json` 和 `src/agent_tune_kit/__init__.py` 中的版本。不要把 PyPI token 写入仓库或聊天记录。

## 最小调优闭环

下面这些步骤在**你的 Agent 项目**里完成，不是在本仓库里完成。

### 1. 生成测试脚本

输入时请同时说明 Agent 从哪里启动、评估数据在哪里，例如：

```text
$atk-init 我希望调优的 Agent 服务为 scripts/merge_js_simple.py，对应的数据集为 scripts/service_source_codes.csv
```

不要只输入空的 `$atk-init`。Codex 需要这些路径来读取项目代码和数据样例，然后生成：

```text
.atk/runner/eval_runner.py
```

这个脚本会保留你的原始数据列，并额外写入 Agent 的实际输出列 `agent_output`。它还会追加 `agent_output_log_path`；当可信的 Python `logging` 采集已配置时，该列会在串行或同进程并发运行中指向类似 `logs/row_000001.log` 的逐行日志文件。

`$atk-init` 会先把你提供的数据集快照复制到 `.atk/datasets/`，生成的 runner 后续读取这个项目内副本。若同名快照已存在且内容完全一致，会直接复用；若同名但内容不同，会使用 `dataset_2.csv`、`dataset_3.csv` 这样的可读递增名称。

### 2. 跑一遍 Agent

输入：

```text
$atk-run
```

运行完成后会得到：

```text
.atk/results/v1/eval_results.csv
```

如果逐行日志处于启用状态，同一版本目录还会包含 `.atk/results/v1/logs/row_*.log`。逐行日志会在配置了同进程 Python `logging` 的串行运行中生成；当 `CONCURRENT_ROW_LOGGING_ENABLED` 保持启用时，也支持 `--concurrency > 1`。runner 只写入 ATK 行上下文处于活动状态时发出的记录；stdout/stderr、子进程、多进程和行结束后的后台日志不在范围内。若禁用并发逐行日志，并发运行会显式降级到 `app.log`/CSV 证据，不创建逐行日志。

### 3. 找出异常样本

如果你希望 Codex 帮你判断哪些结果异常，直接用：

```text
$atk-find-failures
```

如果你有明确规则，例如“期望答案不等于输出就是异常”，先创建或更新规则脚本：

```text
$atk-init-failure-rule 规则：当 expected 字段与 agent_output 字段不一致时判定为异常
```

Codex 会根据你在命令中给出的规则生成规则脚本：

```text
.atk/runner/failure_rule.py
```

然后执行规则脚本来写入异常结果：

```text
$atk-find-failures-by-rule
```

如果 `.atk/runner/failure_rule.py` 不存在，`$atk-find-failures-by-rule` 会停止并提醒你先运行 `$atk-init-failure-rule`。

异常结果会写入：

```text
.atk/results/v1/failure_cases.csv
```

### 4. 生成分析报告

输入：

```text
$atk-report
```

Codex 会生成：

```text
.atk/results/v1/report.md
```

报告会总结测试情况、异常样本、可能原因，以及建议优先调哪些问题。

### 5. 可选：用 HTML 审阅异常样本

输入：

```text
$atk-visualize-failures
```

Codex 会生成：

```text
.atk/results/v1/failure_cases.html
```

这个可选浏览器只要当前版本存在 `failure_cases.csv` 就能运行；如果同版本 `report.md` 存在，会作为 best-effort、non-blocking 上下文使用，报告缺失或无法解析不会阻止生成可视化。该 Skill 使用插件内固定 Python 标准库脚本生成确定性、无依赖 HTML，并提供 expected-vs-actual 审阅、搜索/筛选/分页、非标准字段角色切换和安全相对日志链接。

### 6. 让 Codex 执行调优

输入：

```text
$atk-tune
```

Codex 会基于报告修改你的 Agent，并写入：

```text
.atk/results/v1/tuning_plan.md
```

这个文件记录本轮想解决哪些异常、采取了什么调优手段、改了哪些文件。

## 怎么确认调优真的有效

调优后，再跑一次测试：

```text
$atk-run
```

这次会生成 `.atk/results/v2/eval_results.csv`。继续执行异常筛选和报告生成：

```text
$atk-find-failures
$atk-report
```

从第二轮开始，报告会读取上一轮的 `tuning_plan.md`，判断上一轮目标异常是已解决、部分解决、未解决，还是无法判断。这样你就能看到调优是否真的带来了效果。

## 你会看到的结果目录

```text
.atk/
├── datasets/
│   └── service_source_codes.csv
├── runner/
│   ├── eval_runner.py
│   └── failure_rule.py
└── results/
    ├── v1/
    │   ├── eval_results.csv
    │   ├── logs/                    # 可选逐行日志
    │   │   └── row_000001.log
    │   ├── failure_cases.csv
    │   ├── failure_cases.html       # 可选异常浏览器
    │   ├── report.md
    │   └── tuning_plan.md
    └── v2/
        └── ...
```

你通常只需要看 `eval_results.csv`、`failure_cases.csv`、可选的 `failure_cases.html`、`report.md`，以及可用时由 `agent_output_log_path` 链接的逐行日志。版本号由脚本自动管理，不需要手动指定。

## 可用 Skill

- `$atk-status`：检查当前进度，告诉你下一步。
- `$atk-init`：生成适配当前 Agent 的测试脚本。
- `$atk-run`：运行测试脚本并生成当前版本结果。
- `$atk-find-failures`：让 Codex 判断异常样本。
- `$atk-init-failure-rule`：创建或更新 `.atk/runner/failure_rule.py`。
- `$atk-find-failures-by-rule`：执行 `.atk/runner/failure_rule.py`，按明确规则筛选异常样本。
- `$atk-report`：生成分析报告和跨轮验证结论。
- `$atk-visualize-failures`：基于当前 `failure_cases.csv` 生成可选的 `.atk/results/vN/failure_cases.html`。
- `$atk-tune`：根据报告修改 Agent，并记录本轮调优计划。
