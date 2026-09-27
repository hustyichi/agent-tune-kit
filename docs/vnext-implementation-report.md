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
| W4 runner 接入 | 已实现项目本地 runner、每次尝试独立进程、部分结果、组件内容身份及 Skill 加载证据协议。已用可控 Agent 和真实 Magic Workspace MCS 各验证一次自动读取。授权重试与探针预算／隔离尚未实现。 |
| W5 诊断与知识 | 已实现多 Issue 证据存储、根因状态门槛、范围外交接／修复链接和知识适用性检查；已知责任层配对案例的语义诊断尚未验收。 |
| W6 累积调优与最终验收 | 已用合成 Prompt Agent 验证通过候选提交、失败候选恢复、最终 B0 复跑及已接受链回退。冻结计划记录预算和重复次数，但运行时未扣减预算；多候选收益计算、成本门槛、来源暴露与全部异常场景尚未验收。 |
| W7 Skills 与精简 | 已打包七个入口和共享流程，删除 12 个旧 Skill、旧 Agent 模板及旧 `.atk` runner 模板；README、manifest、安装及发布检查已更新。 |
| W8 端到端验收 | **部分完成**。T34 的单 Case 真实导出／自动加载／无效果候选恢复链路已跑通；T35–T39 已知根因区分、范围外组件修复后新轮复验未执行。 |

## 新产物与兼容性

`.atk/project.json` 使用 `schema_version=2`；`datasets/`、`evidence/`、`assessments/`、`rounds/`、`knowledge/` 以显式 ID 引用。Assessment 的 `assessment.csv` 是唯一权威评分明细，manifest 保存指纹。旧版 `.atk` 不自动迁移，遇到旧目录会停止。对外仍保留 `atk install`；七个 Skill 经 `atk internal <operation> --request ... --output ...` 调用本地确定性操作。旧版入口和模板已经删除，此 checkout 属于破坏兼容的开发状态，尚未发布。

## 已运行的验证

- 离线单测：`uv run --frozen pytest -q`，33 项通过（含 Prompt 闭环、可控本地 Agent 自动加载业务 Skill、真实导入协议的合成夹具、Assessment 篡改检测及路径越界拦截）。
- 静态与打包：`uv run --frozen ruff check .`、`ruff format --check .`、`python3 scripts/validate_skill_pack.py`、`uv build --no-sources`、`uv run --frozen python scripts/check-release.py` 通过；后者包含 wheel／sdist 安装烟测。Python 运行环境 3.13.13，uv 0.11.6；项目声明 Python >=3.11、运行时无第三方依赖。
- 实际导出只读导入：`projects/magic-workspace/traces/20260926/onl/SES_2103665448775192576` 下 8 个 Langfuse Trace bundle，来源文件集合指纹 `76bcd1bc43705473d2a5da6b42c7d05a106fec56c8a2c33d1faedd93979cc946`。临时目录中生成 8 条 Trace Record、1666 条证据索引；重复导入返回原批次；修正根 Observation 无父节点误报后，8 条均无结构缺口。原始内容未提交到 ATK 仓库。
- 严格脱敏结构复验：将上述实际导出的 Trace／Observation ID 一致替换、所有自由文本值替换为 `[REDACTED]`，仅在临时目录保存并重新导入；仍得到 8 条 Record、1666 条索引，脱敏文件集合指纹 `3e25b2269fc368b50e04392966dbe2ac2ccbabfb480e3154a700c55c37c1e9c0`。该副本验证实际层级结构；不用于内容语义判断。
- 真实本地 Agent：在 `projects/magic-workspace/official-toolkit` 的忽略目录 `.atk/` 中建立 `round-02486338-3d01-4e4b-b3a5-1f321e99ad18`。通过 `scripts/run-eval.sh --ids L1a-groupBy --port 9595` 执行 B0 和一个只改 `create-util/SKILL.md` 的候选；Magic run 分别为 `atk-vnext-f5595b97dd184af494e05b921ef565ad`、`atk-vnext-9aebf00c8bdd4d1f8e108c1aecac23b3`。两侧 `casePass=true`、可比、`skill-usage.json` 显示实际加载；注入工作区的 Skill SHA-256 分别等于 B0 `1f7a3bed016726678e85554cb7c9e629374fd7d9cbbc5f84f3184cf7483382b6` 与候选 `6a36eef49e569387edcb9c7cfd6cac8974363dedfe374e6ad49879abf4278858` 源文件。root／MCS／Official Toolkit commit、模型配置、运行时 Prompt、Case 和超时设置两侧相同；Agent 配置哈希随 Skill 改变。
- ATK 增量门禁 `validation-6e439430-d1fb-4e95-bb46-1730d43a2d56` 给出 `no_effect`；候选已 `reject` 并恢复，轮次以 `close_without_adoption` 结束。Official Toolkit 回到 B0 `5ccf3a39c325fc728b4185a229bf11ac8d088fcb` 且工作区干净；Magic 现有 `datasets/case-index.json` 内容哈希在两次运行前后均为 `68da116f2ee92cd234f699f0ddf80067b65e1ef5ec6d8d9ab5c56ca19980e87c`。仅本地忽略目录保留运行证据；未推送或发布。

## 未完成的验收与限制

- **尚缺实现**：`run_evaluation` 只创建初始执行，所有 `retry_of` 均为 `None`，没有授权重试入口（T29／T43）；冻结计划虽要求 `budget`，执行和诊断探针只读取次数或授权布尔值，不扣减或阻止超额运行（T45／T47）。`compare_and_gate` 目前只按 Case 通过比例判断改善和退化，尚未执行冻结的效率／成本门槛；来源组冲突只在单个数据集内检查，没有跨轮 holdout 暴露记录（T45／T46）。
- T34 的最小真实链路已完成，决策走的是 `no_effect → reject → 恢复`；真实有效候选的 `keep → commit` 仍只有合成 E2E 覆盖。原始导出是否已全量脱敏未被证明，语义分析不得直接使用其未经审阅的自由文本；严格脱敏结构副本只在临时目录生成，不保留可分享原始 fixture。
- T35–T39 的已知根因区分，以及 T42 的范围外交接、实际修复和新轮端到端复验未执行。当前 Codex 的语义判断不等于存储 Schema 验证。
- T12、T16–T27、T40–T41、T48–T52 等累积收益、Git 和异常场景尚未逐项跑通；已覆盖的合成闭环不能推断这些路径全部安全。T29／T43／T45／T47 还需先补齐上述实现缺口。
- 未完整验证目标 Agent 的外部副作用隔离、真实业务多 Case 保护集、以及发布包在用户环境中的实际运行。不得以当前结果声称 vNext 达到方案的最终 Definition of Done 或线上普遍提升。

## 下一步开发顺序

1. 补齐运行预算、授权重试及探针边界，并用 T29／T43／T45／T47 的小型确定性场景验证；随后补冻结的成本／效率门槛和跨轮来源暴露记录。
2. 补 T12、T16–T27、T40–T41、T48–T52 中尚未覆盖的 Git、累积收益及异常场景，确认中断后不会误提交、误恢复或误判通过。
3. 用可核验的已知缺陷配对材料完成 T35–T39 的语义诊断，并在真实本地 Agent 上完成一次范围外修复交接、新轮复验（T42）；最后扩展到真实非 Skill、多 Case 和多候选链路，收口 W8 与最终 Definition of Done。
