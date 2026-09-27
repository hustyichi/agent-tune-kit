# vNext 实现与验收记录

日期：2026-09-27（Asia/Shanghai）

开发起点：`1fb37af382d05afeeec3ba8543ceb1421967ae06`

依据：[vNext 改造方案](agent-tune-kit-vnext-refactor-plan.md)

## 交付范围

| 工作包 | 本次结果 |
| --- | --- |
| W1 协议与状态 | 已实现 v2 ID、不可变证据／Assessment、CSV 指纹、显式错误、原子写入和轮次锁。 |
| W2 Git 检查点 | 已实现 B0、原目录候选封存、限定路径提交／恢复、临时版本复跑、后缀回退和提交中断核验。旧 Revision 复跑现要求冻结缓存／制品重建方式，切换与恢复两侧均执行并核验；新增文件切换、重建失败恢复和提交成功后 Decision 中断恢复已有合成测试。特殊路径、hook 与磁盘中断矩阵尚未完整验收。 |
| W3 多来源 Evaluation | 已实现本地批次、批量结果和 Langfuse Trace bundle／Observation 行导入、脱敏、显式 profile、重判及校准元数据。实际导出已做只读导入；评分器校准的语义正确性仍需人工验收。 |
| W4 runner 接入 | 已实现项目本地 runner、独立 Execution、部分结果、组件身份与 Skill 加载证据。新增授权基础设施重试、跨批次重试链、探针命令／Case／runner 许可与独立预算门禁；用可控 Agent 验证。真实 Magic Workspace 自动加载仍只有先前单 Case 验收。探针隔离依赖项目记录的环境检查，不提供 OS 沙箱。 |
| W5 诊断与知识 | 已实现多 Issue 证据存储、根因状态门槛、范围外交接／修复链接和知识适用性检查。冻结 Issue 依赖后，未解决的外部问题阻塞普通候选采用；显式授权的规避候选需通过门禁且不关闭原 Issue。修复后新轮须保留原受影响和保护 Case，组件直接探针与端到端 Assessment 均通过才可结案。已知责任层配对案例的语义诊断尚未验收。 |
| W6 累积调优与最终验收 | 合成 Prompt Agent 已验证通过候选提交、失败恢复、最终 B0 复跑及后缀回退。新增执行／候选预算、最终复验额度预留、单侧运行防重复、冻结的质量／效率指标门槛和跨轮 holdout 来源暴露记录；真实多候选收益与异常矩阵仍待验收。 |
| W7 Skills 与精简 | 已打包七个入口和共享流程，删除 12 个旧 Skill、旧 Agent 模板及旧 `.atk` runner 模板；README、manifest、安装及发布检查已更新。 |
| W8 端到端验收 | **部分完成**。T34 的单 Case 真实导出／自动加载／无效果候选恢复链路已跑通；T42、T48、T49 的协议及异常路径新增合成测试。T35–T39 已知根因区分、真实范围外组件修复后新轮复验未执行。 |

## 新产物与兼容性

`.atk/project.json` 使用 `schema_version=2`；`datasets/`、`evidence/`、`assessments/`、`rounds/`、`knowledge/` 以显式 ID 引用。Assessment 的 `assessment.csv` 是唯一权威评分明细，manifest 保存指纹。旧版 `.atk` 不自动迁移，遇到旧目录会停止。对外仍保留 `atk install`；七个 Skill 经 `atk internal <operation> --request ... --output ...` 调用本地确定性操作。旧版入口和模板已经删除，此 checkout 属于破坏兼容的开发状态，尚未发布。

## 已运行的验证

- 离线单测：`UV_OFFLINE=1 uv run --frozen pytest -q`，42 项通过（含 Prompt 闭环、可控本地 Agent 自动加载业务 Skill、真实导入协议的合成夹具、Assessment 篡改检测、路径越界拦截、2 Case×2 次执行与授权重试、预算、探针许可、效率门槛、跨轮 holdout，以及新增的外部 Issue 规避／新轮复验、旧 Revision 缓存恢复与提交中断恢复场景）。
- 静态与打包：`uv run --frozen ruff check .`、`ruff format --check .`、`python3 scripts/validate_skill_pack.py`、`uv build --no-sources`、`uv run --frozen python scripts/check-release.py` 通过；后者包含 wheel／sdist 安装烟测。Python 运行环境 3.13.13，uv 0.11.6；项目声明 Python >=3.11、运行时无第三方依赖。
- 实际导出只读导入：`projects/magic-workspace/traces/20260926/onl/SES_2103665448775192576` 下 8 个 Langfuse Trace bundle，来源文件集合指纹 `76bcd1bc43705473d2a5da6b42c7d05a106fec56c8a2c33d1faedd93979cc946`。临时目录中生成 8 条 Trace Record、1666 条证据索引；重复导入返回原批次；修正根 Observation 无父节点误报后，8 条均无结构缺口。原始内容未提交到 ATK 仓库。
- 严格脱敏结构复验：将上述实际导出的 Trace／Observation ID 一致替换、所有自由文本值替换为 `[REDACTED]`，仅在临时目录保存并重新导入；仍得到 8 条 Record、1666 条索引，脱敏文件集合指纹 `3e25b2269fc368b50e04392966dbe2ac2ccbabfb480e3154a700c55c37c1e9c0`。该副本验证实际层级结构；不用于内容语义判断。
- 真实本地 Agent：在 `projects/magic-workspace/official-toolkit` 的忽略目录 `.atk/` 中建立 `round-02486338-3d01-4e4b-b3a5-1f321e99ad18`。通过 `scripts/run-eval.sh --ids L1a-groupBy --port 9595` 执行 B0 和一个只改 `create-util/SKILL.md` 的候选；Magic run 分别为 `atk-vnext-f5595b97dd184af494e05b921ef565ad`、`atk-vnext-9aebf00c8bdd4d1f8e108c1aecac23b3`。两侧 `casePass=true`、可比、`skill-usage.json` 显示实际加载；注入工作区的 Skill SHA-256 分别等于 B0 `1f7a3bed016726678e85554cb7c9e629374fd7d9cbbc5f84f3184cf7483382b6` 与候选 `6a36eef49e569387edcb9c7cfd6cac8974363dedfe374e6ad49879abf4278858` 源文件。root／MCS／Official Toolkit commit、模型配置、运行时 Prompt、Case 和超时设置两侧相同；Agent 配置哈希随 Skill 改变。
- ATK 增量门禁 `validation-6e439430-d1fb-4e95-bb46-1730d43a2d56` 给出 `no_effect`；候选已 `reject` 并恢复，轮次以 `close_without_adoption` 结束。Official Toolkit 回到 B0 `5ccf3a39c325fc728b4185a229bf11ac8d088fcb` 且工作区干净；Magic 现有 `datasets/case-index.json` 内容哈希在两次运行前后均为 `68da116f2ee92cd234f699f0ddf80067b65e1ef5ec6d8d9ab5c56ca19980e87c`。仅本地忽略目录保留运行证据；未推送或发布。

## 未完成的验收与限制

- **新增功能的验收边界**：T29／T43 的重试链、T45 的最终额度预留和效率门槛、T46 的跨轮 holdout 暴露、T47 的探针许可与额度、T42 的外部修复协议、T48 的提交中断恢复、T49 的缓存重建与原目录恢复已有合成测试。组件探针的 `kind` 和修复身份由接入者冻结，协议核对其 Assessment 与证据关联，不能独立证明探针实际调用了声明的外部制品。目标 Agent 的外部写隔离由项目环境实际提供，`isolation_ref` 只记录核查依据，不是 OS 沙箱；候选可控制的指标来源不能未经核查作为可信成本门槛。已设置费用门槛而费用不可观测时返回证据不足，不能声称满足费用限制。
- T34 的最小真实链路已完成，决策走的是 `no_effect → reject → 恢复`；真实有效候选的 `keep → commit` 仍只有合成 E2E 覆盖。原始导出是否已全量脱敏未被证明，语义分析不得直接使用其未经审阅的自由文本；严格脱敏结构副本只在临时目录生成，不保留可分享原始 fixture。
- T35–T39 的已知根因区分，以及 T42 的真实范围外交接、实际修复和新轮端到端复验未执行。当前 Codex 的语义判断不等于存储 Schema 验证。
- T12、T16–T27、T40–T41、T50–T52 等累积收益、Git 和异常场景尚未逐项跑通；T48／T49 只覆盖上述新增场景，不能推断异常矩阵全部安全。
- 未完整验证目标 Agent 的外部副作用隔离、真实业务多 Case 保护集、以及发布包在用户环境中的实际运行。不得以当前结果声称 vNext 达到方案的最终 Definition of Done 或线上普遍提升。

## 下一步开发顺序

1. 补 T12、T16–T27、T40–T41、T48–T52 中尚未覆盖的 Git、累积收益和异常场景，尤其特殊路径、hook、磁盘中断及回退链，确认中断后不会误提交、误恢复或误判通过。
2. 用可核验的已知缺陷配对材料完成 T35–T39 的语义诊断，并在真实本地 Agent 上完成一次范围外修复交接、新轮复验（T42）。
3. 扩展到真实非 Skill、多 Case 和多候选链路，核验外部副作用隔离及可信指标来源，收口 W8 与最终 Definition of Done。
