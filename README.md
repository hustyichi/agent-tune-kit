# Agent Tune Kit

最后更新：2026-09-27。状态：vNext 核心链路和 Magic Workspace 单 Case 真实接入已验证，完整验收待收口。详见 [实现与验收记录](docs/vnext-implementation-report.md)。

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
| `atk-validate` | 对比候选与父检查点，最后对比累计版本与 B0 |
| `atk-decide` | 保留并提交、拒绝并恢复、回退已接受后缀或结束本轮 |

核心顺序：`atk-init → atk-dataset（按需）→ atk-eval → atk-diagnose → [atk-optimize → atk-validate → atk-decide]×N → atk-validate（最终）→ atk-decide`。一轮只有一个未决候选；通过的候选在原工程形成一个本地 commit，失败候选保存证据后恢复父检查点。最终门禁以冻结的完整 Case、保护集与重复计划验证累计效果。不会自动 push、发布、部署、改写 Git 历史或修改其他仓库。

## 三种证据入口

- **本地运行**：项目 `.atk/adapters/runner.py` 用目标项目的 Python 环境按显式尝试列表调用现有 Agent；每次尝试保存一个 Execution。Prompt、代码及业务 Skill 使用同一协议。Skill 的 `available`、`loaded`、`invoked` 状态分开记录，无法证明实际加载时不能正常通过 Skill 门禁。
- **批量结果导入**：CSV/JSON/JSONL 按保存的字段映射导入，不重新运行 Agent；缺少尝试边界时不伪造 Execution。
- **Langfuse 文件导入**：支持 Trace bundle 和 Observation 行两种显式 profile。保留 Trace/Observation ID、父子关系、来源、缺失与过滤范围；外部分数只当证据，不直接换算 ATK 的通过率。原文件只读，默认遮蔽常见凭证字段；项目敏感字段需要追加脱敏键。

没有 Ground Truth 也能调查证据；只有具备固定判据的维度才能给出确定判定。`reassess` 创建新 Assessment，不重跑 Agent，不覆盖旧评分。评分标准、被测边界、组件和运行条件变化会使旧对照失效。

## 主要产物

```text
.atk/
├── project.json                # 项目运行配置与组件声明
├── runtime.md                  # 接入调查、加载证明和局限
├── adapters/runner.py          # 项目本地 runner
├── datasets/<id>/              # 不可变 Case 快照
├── evidence/<batch-id>/        # records.jsonl、来源索引、执行状态
├── assessments/<id>/           # manifest.json + 唯一权威 assessment.csv
├── rounds/<id>/                # 冻结计划、Issues、候选、验证、决策和操作日志
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

离线测试使用小型 Git 仓库和假 Agent。真实轨迹、真实自动加载业务 Skill、已知责任层案例和外部修复后复验须分别验收；离线测试通过不能替代这些结果。尚未确认安全隔离的目标 Agent 外部写操作不得在正式跑测中启动。
