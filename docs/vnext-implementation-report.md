# vNext 实现与验收记录

日期：2026-09-27（Asia/Shanghai）

开发起点：`1fb37af382d05afeeec3ba8543ceb1421967ae06`

依据：[vNext 改造方案](agent-tune-kit-vnext-refactor-plan.md)

## 交付范围

| 工作包 | 本次结果 |
| --- | --- |
| W1 协议与状态 | 已实现 v2 ID、不可变证据／Assessment、CSV 指纹、显式错误、原子写入和轮次锁。 |
| W2 Git 检查点 | 已实现 B0、原目录候选封存、限定路径提交／恢复、临时版本复跑、后缀回退和提交中断核验；特殊路径、hook 与磁盘中断矩阵尚未完整验收。 |
| W3 多来源 Evaluation | 已实现本地批次、批量结果和 Langfuse Trace bundle／Observation 行导入、脱敏、显式 profile、重判及校准元数据。实际导出已做只读导入；评分器校准的语义正确性仍需人工验收。 |
| W4 runner 接入 | 已实现项目本地 runner、每次尝试独立进程、部分结果、组件内容身份及 Skill 加载证据协议。已用可控本地 Agent 验证自动读取；真实业务 Agent 尚未执行。 |
| W5 诊断与知识 | 已实现多 Issue 证据存储、根因状态门槛、范围外交接／修复链接和知识适用性检查；已知责任层配对案例的语义诊断尚未验收。 |
| W6 累积调优与最终验收 | 已用合成 Prompt Agent 验证通过候选提交、失败候选恢复、最终 B0 复跑及已接受链回退；多候选收益计算、波动预算与全部异常场景尚未验收。 |
| W7 Skills 与精简 | 已打包七个入口和共享流程，删除 12 个旧 Skill、旧 Agent 模板及旧 `.atk` runner 模板；README、manifest、安装及发布检查已更新。 |
| W8 端到端验收 | **未完成**。真实导出接入已验证；真实业务 Agent 加载、T35–T39 已知根因区分、范围外组件修复后新轮复验未执行。 |

## 新产物与兼容性

`.atk/project.json` 使用 `schema_version=2`；`datasets/`、`evidence/`、`assessments/`、`rounds/`、`knowledge/` 以显式 ID 引用。Assessment 的 `assessment.csv` 是唯一权威评分明细，manifest 保存指纹。旧版 `.atk` 不自动迁移，遇到旧目录会停止。对外仍保留 `atk install`；七个 Skill 经 `atk internal <operation> --request ... --output ...` 调用本地确定性操作。旧版入口和模板已经删除，此 checkout 属于破坏兼容的开发状态，尚未发布。

## 已运行的验证

- 离线单测：`uv run --frozen pytest -q`，33 项通过（含 Prompt 闭环、可控本地 Agent 自动加载业务 Skill、真实导入协议的合成夹具、Assessment 篡改检测及路径越界拦截）。
- 静态与打包：`uv run --frozen ruff check .`、`ruff format --check .`、`python3 scripts/validate_skill_pack.py`、`uv build --no-sources`、`uv run --frozen python scripts/check-release.py` 通过；后者包含 wheel／sdist 安装烟测。Python 运行环境 3.13.13，uv 0.11.6；项目声明 Python >=3.11、运行时无第三方依赖。
- 实际导出只读导入：`projects/magic-workspace/traces/20260926/onl/SES_2103665448775192576` 下 8 个 Langfuse Trace bundle，来源文件集合指纹 `76bcd1bc43705473d2a5da6b42c7d05a106fec56c8a2c33d1faedd93979cc946`。临时目录中生成 8 条 Trace Record、1666 条证据索引；重复导入返回原批次；修正根 Observation 无父节点误报后，8 条均无结构缺口。原始内容未提交到 ATK 仓库。

## 未完成的验收与限制

- T34 仅完成实际 Langfuse 文件导入及可控本地 Agent 自动加载测试；尚需在真实业务 Agent 中完成 B0 → 候选 → 实际加载证明 → 对照 → 决策。原始导出是否已全量脱敏未被证明；本次只在临时产物中按已知敏感字段脱敏，不能把其余自由文本视为安全。
- T35–T39 的已知根因区分，以及 T42 的范围外交接、实际修复和新轮端到端复验未执行。当前 Codex 的语义判断不等于存储 Schema 验证。
- T12、T21–T27、T29、T43、T45、T48–T52 等 Git、重试和预算的完整异常矩阵未逐项跑通；已覆盖的合成闭环不能推断这些路径全部安全。
- 未验证目标 Agent 的外部副作用隔离、真实业务 Skill 的加载机制和发布包在用户环境中的实际运行。不得以当前结果声称 vNext 达到方案的最终 Definition of Done 或线上普遍提升。
