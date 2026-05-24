# Agent tune kit

[English](README.md) | 简体中文

用于本地 Agent 迭代调优的 Codex Skill 模板包。

## MVP 范围

本仓库当前交付的是仓库原生的 Codex Skill 模板包，不是生产级插件安装器。该模板包需要作为一个整体复制或注册使用，包含完整的 Skill 模板、可复用脚本模板、共享版本规则、不确定性确认模式、使用文档和轻量静态检查。

明确的 MVP 非目标：不提供插件安装 UX、marketplace/manifest 打包、一键编排、通用 Schema 强约束、内置示例 Agent/数据集、自动回滚或基线恢复、完整 E2E 测试套件。

## 适用对象

当你已有一个本地 Agent 实现和评估数据集，并希望 Codex 协助完成手工、可重复的调优闭环时，可以使用本模板包：

1. 生成项目本地测试 runner；
2. 用数据集批量运行 Agent；
3. 筛选异常样本；
4. 生成包含归因分析和相邻版本验证的报告；
5. 对 Agent 做聚焦调优；
6. 重复迭代，并在 `agent-tuning/results/vN/` 下保留版本化结果。

## 使用前准备

- 支持 Skill 的 Codex 环境，或能加载本仓库 `skills/*/SKILL.md` 的仓库本地工作流。
- Python 3，用于运行生成的 runner/filter 脚本和静态校验。
- 一个可被 Codex 检查和修改的本地 Agent 项目。
- 一份评估数据集，默认推荐 CSV；其它格式可在生成 runner 时自适配。
- 用户自行管理的 git checkpoint/回滚流程；本模板包不做自动回滚。

## 快速开始

1. 阅读完整使用说明：`docs/skill-template-pack-usage.md`。
2. 整体复制或注册本模板包，或至少保持 `skills/`、`templates/`、`docs/` 在同一相对结构下；单个 Skill 会通过相对路径引用共享文档和模板。
3. 使用前先校验模板包：

   ```sh
   python3 scripts/validate_skill_pack.py
   git diff --check
   ```

4. 在目标 Agent 项目中，通过 Codex 触发 `agent-tuning-generate-runner`。向 Codex 提供或指明 Agent 源码和评估数据集位置。该 Skill 会写入 `agent-tuning/runner/test_runner.py`。
5. 手动运行生成的 runner：

   ```sh
   python3 agent-tuning/runner/test_runner.py
   ```

   首次运行会写入 `agent-tuning/results/v1/results.csv`，并保留原始数据集所有列，同时追加必需列 `agent_output`。若能可靠采集日志，也会写入可选的 `app.log`。

6. 使用一个异常筛选 Skill 为当前版本生成 `abnormal_cases.csv`：
   - `agent-tuning-filter-abnormal-rules`：Codex 生成或更新 `agent-tuning/runner/filter_abnormal.py`，然后你手动运行 `python3 agent-tuning/runner/filter_abnormal.py`。
   - `agent-tuning-filter-abnormal-llm`：Codex 直接读取当前 `results.csv` 并写入 `abnormal_cases.csv`。
7. 触发 `agent-tuning-report`，生成 `agent-tuning/results/vN/report.md`，内容包括统计信息、异常分析、归因假设；当存在上一版本 `tuning_plan.md` 时，还会做相邻版本调优验证。
8. 触发 `agent-tuning-apply-tuning`，让 Codex 基于报告调优 Agent，并写入 `agent-tuning/results/vN/tuning_plan.md`。该文件必须包含固定标题：`## 目标异常清单`、`## 调优手段`、`## 关联改动`。
9. 重复整个流程。当前版本已有 `results.csv` 时，下一次运行 `test_runner.py` 会创建新的 `vN`；从 `v2` 开始，报告会验证上一轮调优目标是否已解决。

## 包含文件

- `skills/agent-tuning-generate-runner/SKILL.md`
- `skills/agent-tuning-filter-abnormal-rules/SKILL.md`
- `skills/agent-tuning-filter-abnormal-llm/SKILL.md`
- `skills/agent-tuning-report/SKILL.md`
- `skills/agent-tuning-apply-tuning/SKILL.md`
- `templates/agent-tuning/runner/test_runner.py.md`
- `templates/agent-tuning/runner/filter_abnormal.py.md`
- `docs/shared-versioning-and-confirmation.md`
- `docs/skill-template-pack-usage.md`
- `docs/codex_agent_tuning_prd.md`
- `scripts/validate_skill_pack.py`

## 目标 Agent 项目中的输出结构

```text
agent-tuning/
├── runner/
│   ├── test_runner.py
│   └── filter_abnormal.py        # 仅规则筛选模式需要
└── results/
    ├── v1/
    │   ├── results.csv
    │   ├── app.log               # 可选
    │   ├── abnormal_cases.csv
    │   ├── report.md
    │   └── tuning_plan.md
    └── v2/
        └── ...
```

## 用户需要了解的版本规则

- 只有生成的 runner 负责创建或复用结果版本目录。
- 不存在任何 `vN` 时，runner 创建 `v1`。
- 数字最大的 `vN` 已包含 `results.csv` 时，runner 创建 `v{N+1}`。
- 数字最大的 `vN` 不包含 `results.csv` 时，runner 复用该目录。
- 非 runner Skill 始终把数字最大的现有 `vN` 作为当前版本；当所需文件缺失时，不会回退到旧版本。

## 校验模板包

```sh
python3 scripts/validate_skill_pack.py
git diff --check
```

校验器会在必需 Skill 章节、PRD 引用、版本辅助函数片段、输出路径、非目标说明或调优/报告契约缺失时直接失败。
