# Agent tune kit

[English](README.md) | 简体中文

用于本地 Agent 迭代调优的本地 Codex 插件，同时保留 legacy copy/register 的 Skill 模板包使用路径。

## 当前范围

本仓库现在以本地 Codex 插件形式交付，包含 `.codex-plugin/plugin.json`、六个 Skill、可复用 runner/filter 模板、共享版本规则、文档、安全的个人 marketplace 安装/冒烟工具和静态校验。原有五个阶段 Skill 以及 `skills/`、`templates/`、`docs/` 的相对引用契约仍然保持兼容，可继续用于 legacy copy/register。

本阶段非目标：no public marketplace（不发布公共 marketplace）、no brand assets（不提供品牌资产/截图）、不做一键编排、无通用 Schema 强约束、无内置示例 Agent/data fixtures、无自动回滚或基线恢复、无完整 E2E 测试套件。

## 适用对象

当你已有一个本地 Agent 实现和评估数据集，并希望 Codex 协助完成手工、可重复的调优闭环时，可以使用 Agent Tune Kit：

1. 用 `agent-tuning-start` 开始或恢复流程；
2. 生成项目本地测试 runner；
3. 用数据集批量运行 Agent；
4. 筛选异常样本；
5. 生成包含归因分析和相邻版本验证的报告；
6. 对 Agent 做聚焦调优；
7. 重复迭代，并在 `agent-tuning/results/vN/` 下保留版本化结果。

## 使用前准备

- 支持本地插件/Skill 的 Codex 环境，或能加载本仓库 `skills/*/SKILL.md` 的 Skill 环境。
- Python 3，用于安装工具、生成的 runner/filter 脚本和静态校验。
- 一个可被 Codex 检查和修改的本地 Agent 项目。
- 一份评估数据集，默认推荐 CSV；其它格式可在生成 runner 时自适配。
- 用户自行管理的 git checkpoint/回滚流程；本插件不做自动回滚。

## 快速开始

1. 校验仓库内容：

   ```sh
   python3 scripts/validate_skill_pack.py
   git diff --check
   ```

2. 预览本地插件注册。默认就是 dry-run：

   ```sh
   python3 scripts/install_plugin.py --dry-run --smoke
   ```

3. 预览结果符合预期后再安装到个人 marketplace：

   ```sh
   python3 scripts/install_plugin.py --apply --smoke
   ```

   安装器会写入或更新 `~/.agents/plugins/marketplace.json`，保持 marketplace `source.path` 为 `./plugins/agent-tune-kit`，并默认用符号链接把 `~/plugins/agent-tune-kit` 指向本仓库。只有明确需要复制兜底时才使用 `--copy`；只有需要替换已有同名 entry 或 plugin-store 目标时才使用 `--force`。

4. legacy copy/register 兜底路径：整体复制或注册本仓库模板包，并保持 `skills/`、`templates/`、`docs/` 在同一相对结构下。不要只复制单个 Skill 目录，除非同时保留或内联共享资产。

5. 在 Codex 中触发 `agent-tuning-start`，检查目标项目的 `agent-tuning/` 状态并获得下一阶段建议。

6. 对新目标项目，触发 `agent-tuning-generate-runner`。向 Codex 提供或指明 Agent 源码和评估数据集位置。该 Skill 会写入 `agent-tuning/runner/test_runner.py`。

7. 手动运行生成的 runner：

   ```sh
   python3 agent-tuning/runner/test_runner.py
   ```

   首次运行会写入 `agent-tuning/results/v1/results.csv`，并保留原始数据集所有列，同时追加必需列 `agent_output`。若能可靠采集日志，也会写入可选的 `app.log`。

8. 使用一个异常筛选 Skill 为当前版本生成 `abnormal_cases.csv`：
   - `agent-tuning-filter-abnormal-rules`：Codex 生成或更新 `agent-tuning/runner/filter_abnormal.py`，然后你手动运行 `python3 agent-tuning/runner/filter_abnormal.py`。
   - `agent-tuning-filter-abnormal-llm`：Codex 直接读取当前 `results.csv` 并写入 `abnormal_cases.csv`。
9. 触发 `agent-tuning-report`，生成 `agent-tuning/results/vN/report.md`，内容包括统计信息、异常分析、归因假设；当存在上一版本 `tuning_plan.md` 时，还会做相邻版本调优验证。
10. 触发 `agent-tuning-apply-tuning`，让 Codex 基于报告调优 Agent，并写入 `agent-tuning/results/vN/tuning_plan.md`。该文件必须包含固定标题：`## 目标异常清单`、`## 调优手段`、`## 关联改动`。
11. 重复整个流程。当前版本已有 `results.csv` 时，下一次运行 `test_runner.py` 会创建新的 `vN`；从 `v2` 开始，报告会验证上一轮调优目标是否已解决。

## 包含文件

- `.codex-plugin/plugin.json`
- `skills/agent-tuning-start/SKILL.md`
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
- `scripts/install_plugin.py`
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

## 校验与冒烟

```sh
python3 scripts/validate_skill_pack.py
git diff --check
python3 scripts/install_plugin.py --dry-run --smoke
python3 scripts/install_plugin.py --marketplace-path /tmp/agent-tune-marketplace.json --plugin-store /tmp/agent-tune-plugins --apply --smoke
```

校验器会在必需 Skill 章节、manifest 字段、安装器行为、PRD 引用、版本辅助函数片段、输出路径、非目标说明或调优/报告契约缺失时直接失败。
