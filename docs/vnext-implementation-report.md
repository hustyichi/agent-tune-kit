# vNext 实现与验收记录

日期：2026-09-28（Asia/Shanghai）

开发起点：`1fb37af382d05afeeec3ba8543ceb1421967ae06`

依据：[vNext 改造方案](agent-tune-kit-vnext-refactor-plan.md)

## 交付范围

| 工作包 | 本次结果 |
| --- | --- |
| W1 协议与状态 | 已实现 v2 ID、不可变证据／Assessment、CSV 指纹、显式错误、原子写入和轮次锁。 |
| W2 Git 检查点 | 已实现 B0、原目录候选封存、限定路径提交／恢复、临时版本复跑、后缀回退和提交中断核验。旧 Revision 复跑要求冻结缓存／制品重建方式。两项已采用候选回退其后缀后，新候选必须基于恢复 commit 和仍有效的父 Assessment 重新验证；意外暂存区与 HEAD 漂移阻断采用，跨进程恢复提交不重复写 commit。hook、特殊路径、回退中断／未知内容及 runner 自行提交也有测试；完整异常矩阵仍待收口。 |
| W3 多来源 Evaluation | 已实现本地批次、批量结果和 Langfuse Trace bundle／Observation 行导入、脱敏、显式 profile、重判及校准元数据。旧轮关闭后，相同 commit 与运行配置可显式复用 Revision，对原始执行按新评分规则重判，不新增执行批次；运行配置变化时拒绝复用。Case 输入变化时旧执行对照返回证据不足，新轮双方重跑后才可获得有效 Validation。Assessment HTML 从权威 CSV 重建，恶意标签转义。实际导出已做只读导入；评分器校准的语义正确性仍需人工验收。 |
| W4 runner 接入 | 已实现项目本地 runner、独立 Execution、部分结果、组件身份与 Skill 加载证据。授权重试和探针独立预算已验证；探针可执行冻结的独立命令，限制工作目录、超时、最大调用数，并核对 `.atk/probes/` 脚本指纹。每批使用项目配置快照，冻结后修改接入配置会阻塞后续执行；带 `source_path` 的本地组件在批次结束时复核文件指纹，执行中漂移会留下前后身份并使批次为 `partial`。需验证加载的组件同时核对实际绝对路径和指纹；隔离副本还需明确来源路径与副本内容，其他安装副本即使内容相同也返回证据不足。直接组件检查不得复用端到端 Agent 命令。真实 Magic Workspace 自动加载仍只有先前单 Case 验收；`isolation_ref` 是接入方核查依据，不是 OS 沙箱。 |
| W5 诊断与知识 | 已实现多 Issue 证据存储、根因状态门槛、范围外交接／修复链接和知识适用性检查。源码／契约行可作为脱敏 `source_contract` 证据，源码版本与实际制品身份独立保存。已知缺陷合成对照分别区分 Skill 规则写错／Agent 忽略正确规则、合法调用在工具内失败／工具正确而运行时转交错误；Skill 与 Agent 代码各自修订后，目标 Case 修复、保护 Case 稳定，增量及最终门禁均通过。未执行探针保留 `inconclusive` 和缺口，评分器负例校准不一致会阻断 `calibrated`。已执行检查须记录预期、实际和有效引用。未解决的外部问题阻塞普通候选采用，获授权的规避候选不关闭原 Issue。修复后新轮须保留受影响和保护 Case，直接探针与端到端 Assessment 均通过才可结案。 |
| W6 累积调优与最终验收 | 合成 Prompt Agent 已验证 10 Case 四候选链：C1 修复 7／8，C2 回归关键任务 2 被拒，C3 仅新增修复 9，C4 回归关键任务 3 被拒；最终 K3 为 9/10，比 B0 净修复 3 例。增量通过而最终无效果时，正常完成被拦截；回退 B0 后新候选须基于恢复 commit 重新封存和评判。最终完成会核对本轮、B0、当前 Revision、最终 commit 与冻结计划；显式例外保留原结论并另记 `completed_with_override`。真实多候选收益仍待验收。 |
| W7 Skills 与精简 | 已打包七个入口和共享流程，删除 12 个旧 Skill、旧 Agent 模板及旧 `.atk` runner 模板；README、manifest、安装及发布检查已更新。 |
| W8 端到端验收 | **部分完成**。T34 的单 Case 真实导出／自动加载／无效果候选恢复链路已跑通；T16–T19、T28、T30 的合成整轮或跨进程恢复，T35–T39 的已知原因配对与证据不足门禁，以及 T20–T27、T32、T40–T42、T47–T52 的部分异常路径已有测试。真实范围外组件修复后新轮复验未执行。 |

## 新产物与兼容性

`.atk/project.json` 使用 `schema_version=2`；`datasets/`、`evidence/`、`assessments/`、`rounds/`、`knowledge/` 以显式 ID 引用。Assessment 的 `assessment.csv` 是唯一权威评分明细，manifest 保存指纹。旧版 `.atk` 不自动迁移，遇到旧目录会停止。对外仍保留 `atk install`；七个 Skill 经 `atk internal <operation> --request ... --output ...` 调用本地确定性操作。旧版入口和模板已经删除，此 checkout 属于破坏兼容的开发状态，尚未发布。

## 已运行的验证

- 离线单测：`UV_OFFLINE=1 uv run --frozen pytest -q`，73 项通过（含 Prompt 闭环、10 Case 四候选与最终 9/10、回退已采用后缀与重新验证、Case 输入变化后的双方重跑、跨轮换判据只重判原执行、跨进程提交恢复、Skill 直读／隔离副本／错误安装副本／缺路径证据、Skill／Agent 已知故障配对及分别修订后的目标／保护集、工具／运行时已知故障配对、评分器误判校准阻断、未执行探针降级、服务与 Skill 两种边界、外部固定组件批间／批内漂移、HTML 转义、候选运行新增未知文件阻断、暂存区与 HEAD 漂移、2 Case×2 次执行与授权重试、独立工具探针、外部 Issue 新轮复验、Git hook／特殊路径／回退中断）。
- 静态与打包：`uv run --frozen ruff check .`、`ruff format --check .`、`python3 scripts/validate_skill_pack.py`、`uv build --no-sources`、`uv run --frozen python scripts/check-release.py` 通过；后者包含 wheel／sdist 安装烟测。Python 运行环境 3.13.13，uv 0.11.6；项目声明 Python >=3.11、运行时无第三方依赖。
- 实际导出只读导入：`projects/magic-workspace/traces/20260926/onl/SES_2103665448775192576` 下 8 个 Langfuse Trace bundle，来源文件集合指纹 `76bcd1bc43705473d2a5da6b42c7d05a106fec56c8a2c33d1faedd93979cc946`。临时目录中生成 8 条 Trace Record、1666 条证据索引；重复导入返回原批次；修正根 Observation 无父节点误报后，8 条均无结构缺口。原始内容未提交到 ATK 仓库。
- 严格脱敏结构复验：将上述实际导出的 Trace／Observation ID 一致替换、所有自由文本值替换为 `[REDACTED]`，仅在临时目录保存并重新导入；仍得到 8 条 Record、1666 条索引，脱敏文件集合指纹 `3e25b2269fc368b50e04392966dbe2ac2ccbabfb480e3154a700c55c37c1e9c0`。该副本验证实际层级结构；不用于内容语义判断。
- 真实本地 Agent：在 `projects/magic-workspace/official-toolkit` 的忽略目录 `.atk/` 中建立 `round-02486338-3d01-4e4b-b3a5-1f321e99ad18`。通过 `scripts/run-eval.sh --ids L1a-groupBy --port 9595` 执行 B0 和一个只改 `create-util/SKILL.md` 的候选；Magic run 分别为 `atk-vnext-f5595b97dd184af494e05b921ef565ad`、`atk-vnext-9aebf00c8bdd4d1f8e108c1aecac23b3`。两侧 `casePass=true`、可比、`skill-usage.json` 显示实际加载；注入工作区的 Skill SHA-256 分别等于 B0 `1f7a3bed016726678e85554cb7c9e629374fd7d9cbbc5f84f3184cf7483382b6` 与候选 `6a36eef49e569387edcb9c7cfd6cac8974363dedfe374e6ad49879abf4278858` 源文件。root／MCS／Official Toolkit commit、模型配置、运行时 Prompt、Case 和超时设置两侧相同；Agent 配置哈希随 Skill 改变。
- ATK 增量门禁 `validation-6e439430-d1fb-4e95-bb46-1730d43a2d56` 给出 `no_effect`；候选已 `reject` 并恢复，轮次以 `close_without_adoption` 结束。Official Toolkit 回到 B0 `5ccf3a39c325fc728b4185a229bf11ac8d088fcb` 且工作区干净；Magic 现有 `datasets/case-index.json` 内容哈希在两次运行前后均为 `68da116f2ee92cd234f699f0ddf80067b65e1ef5ec6d8d9ab5c56ca19980e87c`。仅本地忽略目录保留运行证据；未推送或发布。

## 未完成的验收与限制

- **新增功能的验收边界**：T29／T43 的重试链、T45 的最终额度预留和效率门槛、T46 的跨轮 holdout 暴露、T47 的探针许可与额度、T42 的外部修复协议、T48 的提交中断恢复、T49 的缓存重建与原目录恢复已有合成测试。直接组件检查现使用独立命令，但真实外部制品身份仍依赖接入方提供的可核验证据。目标 Agent 的外部写隔离由项目环境实际提供，`isolation_ref` 只记录核查依据，不是 OS 沙箱；候选可控制的指标来源不能未经核查作为可信成本门槛。已设置费用门槛而费用不可观测时返回证据不足。
- T34 的最小真实链路已完成，决策走的是 `no_effect → reject → 恢复`；真实有效候选的 `keep → commit` 仍只有合成 E2E 覆盖。原始导出是否已全量脱敏未被证明，语义分析不得直接使用其未经审阅的自由文本；严格脱敏结构副本只在临时目录生成，不保留可分享原始 fixture。
- Magic Workspace 既有单 Case 加载证据含触发记录和沙箱副本指纹，但记录中的逻辑 `skill:create-util` 不是绝对路径；新增 T12 路径门禁无法直接重用该批旧证据。后续真实评测须由可信桥接脚本记录被加载副本的绝对路径、源路径及复制证据，重新执行两侧；本轮只以合成直读／隔离副本／错误副本验证新门禁。
- T35–T39 有已知原因合成证据和门禁测试，但缺乏人工审核的真实业务配对；T42 的真实范围外交接、实际修复和新轮端到端复验未执行。当前 Codex 的语义判断不等于存储 Schema 验证。
- T12、T16–T27、T40–T41、T50–T52 的部分场景已有合成覆盖，但尚未逐项完整验收；10 Case 精确边际收益、项目外固定文件的批间／批内漂移与不同被测边界的评分责任已验证，实际服务版本核查和磁盘故障矩阵仍未跑通。
- 未完整验证目标 Agent 的外部副作用隔离、真实业务多 Case 保护集、以及发布包在用户环境中的实际运行。不得以当前结果声称 vNext 达到方案的最终 Definition of Done 或线上普遍提升。

## 下一步开发顺序

1. 补 T12 的 Python editable install／Node 链接／旧构建制品场景及剩余 Git／磁盘故障异常矩阵，确认运行路径、制品身份和恢复边界。
2. 用可核验的已知缺陷配对材料完成 T35–T39 的语义诊断，并在真实本地 Agent 上完成一次范围外修复交接、新轮复验（T42）。
3. 扩展到真实非 Skill、多 Case 和多候选链路，核验外部副作用隔离及可信指标来源，收口 W8 与最终 Definition of Done。
