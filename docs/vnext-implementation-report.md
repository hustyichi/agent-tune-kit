# vNext 实现与验收记录

日期：2026-09-28（Asia/Shanghai）

开发起点：`1fb37af382d05afeeec3ba8543ceb1421967ae06`

依据：[vNext 改造方案](agent-tune-kit-vnext-refactor-plan.md)

## 交付范围

| 工作包 | 本次结果 |
| --- | --- |
| W1 协议与状态 | 已实现 v2 ID、不可变证据／Assessment、CSV 指纹、显式错误、原子写入和轮次锁。Execution 的环境故障状态已统一为方案所定 `infra_error`；runner 输出未知状态时批次为 `partial` 并留下对应 Record ID。评测运行期间持续持有操作锁，候选及诊断修订不能与 Agent 执行交错；注入 `fsync`／替换失败时，旧状态文件与目录内容保持完整。 |
| W2 Git 检查点 | 已实现 B0、原目录候选封存、限定路径提交／恢复、临时版本复跑、后缀回退和提交中断核验。候选封存写入 `files.json`、`revision.json` 或 `candidate.json` 时中断，可在文件内容未变化时续封存并保留同一 Revision；内容不符或封存副本被改写时拒绝采用。旧 Revision 复跑要求冻结缓存／制品重建方式。两项已采用候选回退其后缀后，新候选必须基于恢复 commit 和仍有效的父 Assessment 重新验证；意外暂存区与 HEAD 漂移阻断采用，跨进程恢复提交不重复写 commit。`reject/defer` 文件恢复中断后可逐项确认父版本或封存内容并安全续恢复；出现第三种内容或未知暂存时保留现场。Round 已写入而操作阶段尚未完成时，`keep/reject` 可幂等收尾。恢复后逐项核对磁盘内容和目标 commit，Git `smudge` 使实际字节漂移时不会把拒绝、回退或临时复跑标记为完成。hook、特殊路径、回退中断／未知内容及 runner 自行提交也有测试；候选路径别名、大小写／Unicode 冲突和 Windows 路径歧义现会阻断。完整异常矩阵仍待收口。 |
| W3 多来源 Evaluation | 已实现本地批次、批量结果和 Langfuse Trace bundle／Observation 行导入、脱敏、显式 profile、重判及校准元数据。Trace／Observation／Score 分文件可按显式角色关联；两种 Langfuse profile 均可显式映射非标准来源字段，缺列或冲突会阻断。Trace 和事件均保留原始与可解析时间、来源状态及运行元数据，事件另保留规范化 ID 与父子关系。相同文件改变映射、列解析或脱敏配置时创建关联修订；用量字段不再被凭证脱敏误遮蔽。唯一匹配的指定根 Observation 可补足缺失的 Trace 输入输出，多个根时仍保持未知。旧轮关闭后，相同 commit 与运行配置可显式复用 Revision，对原始执行按新评分规则重判，不新增执行批次；运行配置变化时拒绝复用。Case 输入变化时旧执行对照返回证据不足，新轮双方重跑后才可获得有效 Validation。Assessment HTML 从权威 CSV 重建，恶意标签转义。实际导出已做只读导入；评分器校准的语义正确性仍需人工验收。 |
| W4 runner 接入 | 已实现项目本地 runner、独立 Execution、部分结果、组件身份与 Skill 加载证据。批次开始前固定尝试 ID；runner 每条结果和状态写入刷盘，退出后明确记录已完成、未启动和状态不明的尝试。JSONL 尾部截断时原始文件另存，完整前缀仍可校验，批次只标记 `partial`；批次超时结束 runner 及同进程组子进程。授权重试和探针独立预算已验证；探针可执行冻结的独立命令，限制工作目录、超时、最大调用数，并核对 `.atk/probes/` 脚本指纹。每批使用项目配置快照，冻结后修改接入配置会阻塞后续执行；带 `source_path` 的本地组件在批次结束时复核文件指纹，执行中漂移会留下前后身份并使批次为 `partial`。固定远端组件可用冻结的 `version_command` 在批次前后读取实际版本；结果、命令指纹与失败原因进入组件清单，缺失／版本不符不支持正式归因，批内漂移使批次为 `partial`。runner 漏报声明组件也使批次为 `partial`。需验证加载的组件同时核对实际绝对路径和指纹；隔离副本还需明确来源路径与副本内容，其他安装副本即使内容相同也返回证据不足。直接组件检查不得复用端到端 Agent 命令。Magic Workspace 单 Case 已按新路径协议重新跑通；`isolation_ref` 是接入方核查依据，不是 OS 沙箱。 |
| W5 诊断与知识 | 已实现多 Issue 证据存储、根因状态门槛、范围外交接／修复链接和知识适用性检查。源码／契约行可作为脱敏 `source_contract` 证据，源码版本与实际制品身份独立保存。已知缺陷合成对照分别区分 Skill 规则写错／Agent 忽略正确规则、合法调用在工具内失败／工具正确而运行时转交错误；Skill 与 Agent 代码各自修订后，目标 Case 修复、保护 Case 稳定，增量及最终门禁均通过。未执行探针保留 `inconclusive` 和缺口，评分器负例校准不一致会阻断 `calibrated`。已执行检查须记录预期、实际和有效引用。未解决的外部问题阻塞普通候选采用，获授权的规避候选不关闭原 Issue。修复后新轮须保留受影响和保护 Case，直接探针与端到端 Assessment 均通过才可结案。 |
| W6 累积调优与最终验收 | 合成 Prompt Agent 已验证 10 Case 四候选链：C1 修复 7／8，C2 回归关键任务 2 被拒，C3 仅新增修复 9，C4 回归关键任务 3 被拒；最终 K3 为 9/10，比 B0 净修复 3 例。增量通过而最终无效果时，正常完成被拦截；回退 B0 后新候选须基于恢复 commit 重新封存和评判。最终完成会核对本轮、B0、当前 Revision、最终 commit 与冻结计划；显式例外保留原结论并另记 `completed_with_override`。真实多候选收益仍待验收。 |
| W7 Skills 与精简 | 已打包七个入口和共享流程，删除 12 个旧 Skill、旧 Agent 模板及旧 `.atk` runner 模板；README、manifest、安装及发布检查已更新。 |
| W8 端到端验收 | **部分完成**。T34 的单 Case 真实导出／自动加载／无效果候选恢复链路已跑通，并用绝对路径与隔离副本来源证据重新执行两侧；Magic 原生两 Case 批量基线通过。ATK 两 Case 对照的 B0 一例后来查明是 `authentication_failed` 的不完整 Turn，不能作为正常 Case 失败；原 Validation 因缺少 Skill 加载证据返回 `insufficient`，结论仍有效。接入桥接器现区分此类基础设施失败，单 Case 真实复跑通过；新版正式两 Case 基线均因模型认证失败记为 `infra_error`，故没有进行候选效果对照。有效多 Case 收益仍未证明。T12 增加真实 Python editable 安装、Node 本地链接、PYTHONPATH 误导入、上级目录 Skill 和旧 wheel 制品测试。T25 新增 `reject/defer` 中断续恢复、未知内容／暂存阻断和操作末阶段幂等收尾测试；T26／T49／T50 新增 Git `smudge` 内容转换对失败恢复、已采用链回退及临时复跑还原的合成测试。T36／T40 新增合成服务版本前后核查：稳定版本可用于正式对照，批间变更被拒，批内漂移和未知输出不伪称身份已验证。T16–T19、T28、T30 的合成整轮或跨进程恢复，T35–T39 的已知原因配对与证据不足门禁，以及 T20–T27、T32、T40–T42、T47–T52 的部分异常路径已有测试。真实范围外组件修复后新轮复验未执行。 |

T49 补充：旧检查点复跑进程在源码切换后直接退出时，可按操作 ID 检查并恢复；仅接受当前与目标检查点的已知文件内容，拒绝第三种内容，并重跑冻结的缓存／制品准备命令。真实子进程中断、拒绝未知改动、恢复与幂等查询均已验证。准备命令超时后会结束同进程组子进程；延迟写入回归已验证。

T10／T29 补充：默认 runner 的单次尝试超时会结束该 Agent 的同进程组子进程；两 Case 合成批次中首条超时后，第二条正常完成且没有读到首条进程的延迟写入。

T10 补充：自定义 runner 写出无法解析的 `batch.json` 时保留原文件，合成仅供诊断的缺失状态并封存 `partial` 批次；组件清单缺 ID 时也记录异常，不在生成 manifest 前崩溃。两类异常输出均可通过权威证据校验读取。

T10／T43 补充：Execution 运行环境故障状态已统一为方案的 `infra_error`，正式对照将其视为证据不足，只有该状态或超时可按冻结额度重试。未知状态（包括旧拼写 `infrastructure_error`）会写入 `invalid_execution_status_ids` 并使批次保持 `partial`，不能以完整执行参与正式对照；即使错误评分器将该 Case 标为有效 fail、候选标为 pass，门禁仍返回 `insufficient`。

T44 补充：Assessment 每条判定现在必须至少引用本 Record 自身的证据；可以额外引用同批次其他记录作上下文。合成双记录中互相错引且没有自身证据的判定被拒；Trace Record 引用其 Observation 的判定仍可保存。

T22 补充：候选路径拒绝 `./`、重复分隔符、尾部分隔符、反斜杠、Windows 驱动器路径及大小写变化的 `.git`／`.atk`；候选与现有路径仅大小写或 Unicode 规范化不同也会在准备阶段阻断。定向回归已覆盖这些别名和冲突，不进入候选草稿写入。

外部写入保护补充：初始化必须显式声明 `external_effects`（确认没有外部写入时填空数组）。已声明的写入若缺少 `test_environment`、`stub` 或 `approved_safeguard` 类型及其证据引用，runner 在运行前返回 `NOT_REPLAYABLE`，不创建批次。合成 Agent 已验证缺声明、缺保护时阻断与有保护记录时的执行；保护记录本身不证明实际环境隔离。

T25 补充：轮次冻结先落不可变计划、候选准备先落草稿时，若随后的 Round 状态写入失败，可分别核对同一计划或同一草稿并续写状态；不同内容的重试停止，不生成第二份草稿。两个分析轮次也不能先后冻结为两个活跃轮次。最终收尾的 Decision 先于 Round 状态落盘时，无论状态写入前还是写入后中断，同一请求均复用原 Decision；改动理由的重试被拒绝。候选封存分阶段写入时，先核对既有不可变清单、补丁及文件副本，再复用或创建 Revision 和 Candidate；模拟在 Revision 写入前、Candidate 写入前后中断的三条路径均可安全重试。重试时工作文件改变会阻断，封存副本被改写也会阻断后续 `keep`。

Candidate 协议补充：替代方案现在保存经本轮候选清单和主要 Issue 核对的 `supersedes`，封存产物保存可定位的 `patch_ref`；关联 Issue 必须属于冻结计划。已拒绝候选的新方案保留原记录并消耗新的候选预算；`atk-optimize` 指引已改为先决策再创建替代候选，不再提示对已封存候选重新封存。

T39 补充：增量门禁的 `insufficient` 现在在 Validation 的 `limitations` 中自动列出缺失批次、固定组件身份、配对槽位、重复次数、加载路径、判定或必需指标；Magic 两 Case 实测明确列出 B0 `groupBy` 缺少加载证据，而不是只返回状态。后来确认该次 Turn 是认证失败，旧桥接器未正确分类。

T45 补充：运行预算现在只在目标 Revision 的 Git 工作区核验通过后预留。意外修改导致执行前拒绝时，不再扣减额度或创建空批次；恢复干净工作区后原额度可继续使用。冻结轮次现要求记录 `repeatability_basis`；波动或未知场景若将最终重复计划由默认 3 次缩为 2 次，还必须记录 `repeat_plan_basis`。Validation 现保存每个 Case 两侧的通过／失败／未知重复次数、完整覆盖数及计划次数，并汇总修复、退化、持续失败、稳定成功、持平波动和未知 Case 数；缺失配对或有效证据下仍为未知的 verdict 均计入未知，不凭已知子集给出通过。真实 B0 的一次 `groupBy` 表面失败后来查明为模型认证失败，不能据此推断 Case 结果波动；真实模型行为的重复性仍需独立校准。

T45 指标来源补充：默认 runner 给自己测得的耗时标记 `runner_clock`，给 Agent 写出的 `metrics.json` 费用／工具次数标记 `agent_sidecar`。正式门禁读取批次封存的项目配置 `metric_sources`，逐条核对 Record 的采集来源；缺少独立来源声明、来源不匹配或数值无效时，必需指标使 Validation 返回 `insufficient`。原始 sidecar 数值仍保留在 EvidenceRecord 供诊断。合成适配器从受控代码采集费用并标记冻结来源时，效率门禁可通过；真实计费或工具事件采集仍由接入方核查。

T46 知识与 holdout 补充：Knowledge 修订从本地 Execution 证据引用推导并封存参与优化的 `source_group_id`；导入的 Langfuse 等任务证据必须在知识引用上显式标注来源组，缺失时拒绝保存。后续同组 holdout 即使仍在原里程碑也被阻断，旧修订缺该字段时会从其引用重新推导。知识适用条件新增内容指纹，检索时与组件、契约、判定器身份一同核对；条件变化或身份未知返回 `needs_revalidation`。JSONL 数据集中的非字符串来源组现在返回显式映射错误。外部 Trace 的来源组归类仍由接入方核对；错误归类无法由工具自动识别。

## 新产物与兼容性

`.atk/project.json` 使用 `schema_version=2`；`datasets/`、`evidence/`、`assessments/`、`rounds/`、`knowledge/` 以显式 ID 引用。Assessment 的 `assessment.csv` 是唯一权威评分明细，manifest 保存指纹。旧版 `.atk` 不自动迁移，遇到旧目录会停止。对外仍保留 `atk install`；七个 Skill 经 `atk internal <operation> --request ... --output ...` 调用本地确定性操作。旧版入口和模板已经删除，此 checkout 属于破坏兼容的开发状态，尚未发布。

## 已运行的验证

- 离线单测：`UV_OFFLINE=1 uv run --frozen python scripts/check-release.py` 全量 136 项通过，同时通过 Ruff、七个 Skill 包校验、wheel／sdist 构建与独立安装烟测。新增回归覆盖 Agent sidecar 费用不得通过正式门禁、冻结的独立采集来源可通过效率门禁，以及候选路径别名与大小写冲突、初始化必须声明外部副作用、已声明写入缺保护记录时阻断重放。同文件更换映射／脱敏配置、Trace／Observation／Score 分文件关联、两种 profile 的非标准字段显式映射与冲突阻断、未知 Trace 引用、事件字段／时间规范化、token 用量与凭证区分、唯一根 Observation 的显式选择，以及 Assessment 必须引用自身记录的证据已有回归。此前还覆盖接入方基础设施退出码与普通 Agent 退出码区分、仅前者可重试、未知 Execution 状态不得封存完整批次、基础设施故障不可被错误评分器算作 Case 修复的测试，以及替代候选谱系及补丁引用测试、计划冻结／候选草稿／最终决策在 Round 状态写入失败后的续恢复和双活跃轮次阻断测试；覆盖执行前 Revision 异常不扣预算、候选文件恢复中断、末阶段收尾、Git `smudge` 真实文件字节漂移、合成远端服务运行版本前后核查、旧检查点复跑进程中断、准备命令超时清理子进程、单次 Agent 超时防止污染后续 Case、异常 runner 批次文件／组件清单测试、并发操作锁、磁盘写入失败、runner 提前退出与 JSONL 尾部截断、预分配身份校验、批次超时结束子进程组，以及 Python editable／Node 链接／误导入／旧 wheel 运行路径测试。此前覆盖还包括 Prompt 闭环、10 Case 四候选与最终 9/10、回退已采用后缀与重新验证、Case 输入变化后的双方重跑、跨轮换判据只重判原执行、跨进程提交恢复、Skill 直读／链接到源码／隔离副本／错误安装副本／过期构建／缺路径证据、已知故障配对、评分器校准、独立探针、固定组件漂移、暂存区与 HEAD 漂移、2 Case×2 次执行与授权重试、外部 Issue 新轮复验和 Git 中断恢复。新增的分布／覆盖统计在两 Case 重试、10 Case 最终对照和输入变化导致缺失配对的场景中通过断言。
- 本轮 `python scripts/check-release.py`：140 项通过，Ruff、七个 Skill 包校验、wheel／sdist 构建及独立安装烟测均通过。发布检查改用 `uv sync --frozen` 和 `uv run --frozen`，不再因 uv 索引 URL 规范化而改写 `uv.lock`；发布地址测试将有无尾斜杠视为等价。新增回归验证 Knowledge 引用本地或外部证据后阻断同组 holdout、外部证据缺来源组时拒绝保存、适用条件变化触发复核和非法 JSONL 来源组显式报错。
- 静态与打包：上述发布检查完整通过。Python 运行环境 3.13.13，uv 0.11.6；项目声明 Python >=3.11、运行时无第三方依赖。
- 实际导出只读导入：`projects/magic-workspace/traces/20260926/onl/SES_2103665448775192576` 下 8 个 Langfuse Trace bundle，来源文件集合指纹 `76bcd1bc43705473d2a5da6b42c7d05a106fec56c8a2c33d1faedd93979cc946`。临时目录中生成 8 条 Trace Record、1666 条证据索引；重复导入返回原批次；修正根 Observation 无父节点误报后，8 条均无结构缺口。原始内容未提交到 ATK 仓库。
- 导入器扩展后再用上述真实来源只读复验：仍为 8 条 Trace Record、1666 条证据索引、0 条结构不完整记录；同配置重复导入返回原批次。分文件关联与映射修订使用小型合成导出验证，尚无分文件的真实脱敏样例。
- Trace 时间映射补充后再次只读导入上述真实来源：8 条 Trace 的 `timestamp` 均可解析，记录同时保留原值；未从 latency 推算结束时间或任务成功状态。
- 严格脱敏结构复验：将上述实际导出的 Trace／Observation ID 一致替换、所有自由文本值替换为 `[REDACTED]`，仅在临时目录保存并重新导入；仍得到 8 条 Record、1666 条索引，脱敏文件集合指纹 `3e25b2269fc368b50e04392966dbe2ac2ccbabfb480e3154a700c55c37c1e9c0`。该副本验证实际层级结构；不用于内容语义判断。
- 真实本地 Agent：在 `projects/magic-workspace/official-toolkit` 的忽略目录 `.atk/` 中建立 `round-02486338-3d01-4e4b-b3a5-1f321e99ad18`。通过 `scripts/run-eval.sh --ids L1a-groupBy --port 9595` 执行 B0 和一个只改 `create-util/SKILL.md` 的候选；Magic run 分别为 `atk-vnext-f5595b97dd184af494e05b921ef565ad`、`atk-vnext-9aebf00c8bdd4d1f8e108c1aecac23b3`。两侧 `casePass=true`、可比、`skill-usage.json` 显示实际加载；注入工作区的 Skill SHA-256 分别等于 B0 `1f7a3bed016726678e85554cb7c9e629374fd7d9cbbc5f84f3184cf7483382b6` 与候选 `6a36eef49e569387edcb9c7cfd6cac8974363dedfe374e6ad49879abf4278858` 源文件。root／MCS／Official Toolkit commit、模型配置、运行时 Prompt、Case 和超时设置两侧相同；Agent 配置哈希随 Skill 改变。
- ATK 增量门禁 `validation-6e439430-d1fb-4e95-bb46-1730d43a2d56` 给出 `no_effect`；候选已 `reject` 并恢复，轮次以 `close_without_adoption` 结束。Official Toolkit 回到 B0 `5ccf3a39c325fc728b4185a229bf11ac8d088fcb` 且工作区干净；Magic 现有 `datasets/case-index.json` 内容哈希在两次运行前后均为 `68da116f2ee92cd234f699f0ddf80067b65e1ef5ec6d8d9ab5c56ca19980e87c`。仅本地忽略目录保留运行证据；未推送或发布。
- 新路径门禁真实复验：可信的 Magic 桥接器核对 Skill 工具触发、源文件与隔离工作区副本的 SHA-256，再写入副本 `resolved_path`、源文件 `staged_from_path` 和原始 `skill-usage.json` 引用。新轮 `round-7fe23d11-74f8-4b9a-8dd7-d201249ca9f8` 的 B0 批次 `batch-ffbbb8df-6ba0-49be-9d1f-5486bab74170` 与候选批次 `batch-f6617fa0-30ca-42f7-9374-527ca4bb7c05` 均封存、组件无漂移且实际触发，加载指纹分别为 `1f7a3bed016726678e85554cb7c9e629374fd7d9cbbc5f84f3184cf7483382b6`、`6a36eef49e569387edcb9c7cfd6cac8974363dedfe374e6ad49879abf4278858`。`validation-e72dae5e-2acf-4c3a-a239-48161956d7ce` 返回 `no_effect` 而非 `insufficient`，两侧该 Case 都通过；候选已拒绝并恢复，轮次以未采用关闭。Official Toolkit 仍为上述 B0 commit 且干净，Magic Case 索引哈希保持不变。桥接器及产物保存在 Official Toolkit 被忽略的 `.atk/`，未纳入发布包。
- Magic 两 Case 批量基线：用已授权的 `scripts/run-eval.sh --ids L1a-groupBy,L1a-toBoolean --timeout-min 7 --port 9595` 执行 `atk-vnext-2case-c420657186f8`。`summary.json` 显示 2/2 通过、`comparable=true`、0 个环境失败；两例的 `skill-usage.json` 均记录 `create-util` 实际加载，隔离工作区中的 Skill SHA-256 均与 Official Toolkit 源文件 `1f7a3bed016726678e85554cb7c9e629374fd7d9cbbc5f84f3184cf7483382b6` 相同。MCS 与 Official Toolkit 实际 commit 分别为 `0d372b13e0ded6bbee200e8fa8815b81469ebb09`、`5ccf3a39c325fc728b4185a229bf11ac8d088fcb`，两者工作区运行后干净。运行入口生成的 `datasets/case-index.json` 已恢复至原有内容哈希 `68da116f2ee92cd234f699f0ddf80067b65e1ef5ec6d8d9ab5c56ca19980e87c`；Magic 父仓库此前已有的其他改动未纳入提交。该批次仅验证 Magic 原生批量基线，未进入 ATK 的候选、Assessment 和决策链。
- ATK 真实两 Case 候选路径：新轮 `round-74b91967-be31-4b5d-864d-90f44f9bcd47` 使用 `L1a-groupBy` 作为目标、`L1a-toBoolean` 作为保护。B0 批次 `batch-2111f755-9abc-4912-adf7-5b153710c438` 原报告为 fail/pass，候选批次 `batch-d2ce59d1-116c-4604-9368-2c3ededc17c1` 为 pass/pass，均封存 2 条 Execution 且组件无漂移；候选仅改业务 Skill，已证明两例加载候选哈希 `6a36eef49e569387edcb9c7cfd6cac8974363dedfe374e6ad49879abf4278858`。追查 B0 的 Magic run `atk-vnext-da7436447aa74aea9d82fea4aabfa7c6/L1a-groupBy`：`turn.json` 的状态为 `failed`，约 2.5 秒结束，事件中有 `authentication_failed`，没有工具调用；旧桥接器仍把缺失产物输出为普通 `fail`。因此 B0 的该例实际为执行异常，不能声称候选改进。重新判定的 `validation-25b0eb1b-188c-4f79-a062-79440ac86dd2` 返回 `insufficient` 并写出加载缺口，结论保守但原因记录不完整；Issue 仍为 `inconclusive`，候选已拒绝并恢复，Round 以未采用关闭。Official Toolkit 保持 B0 commit 且工作区干净，Magic Case 索引哈希保持 `68da116f2ee92cd234f699f0ddf80067b65e1ef5ec6d8d9ab5c56ca19980e87c`。
- 认证失败接入修正：ATK 项目配置新增可选 `infrastructure_exit_codes`，runner 将这些退出码记录为 `infra_error`；正式对照已有门禁会将其视为证据不足，冻结额度内允许重试。合成测试覆盖退出码 75 与普通 Agent 退出码 3 的区分、仅前者可重试。Magic 本地忽略目录 `.atk/adapters/magic_eval.py` 现检查实际 Turn 状态与认证失败事件；不完整 Turn 和不完整／不可比的 Magic run 不再输出正常 Case 判定，项目配置以退出码 2、3、75 标识接入故障。回放既有保存的认证失败与成功 run，分别得到退出码 75、0；通过桥接器调用 `scripts/run-eval.sh` 的新单 Case `atk-vnext-ad667a0b0b034507b8770373363f3894/L1a-groupBy` 实际 `turn_status=completed`、`casePass=true`、`comparable=true`，并有 `create-util` 加载证据。Case 索引内容哈希仍为 `68da116f2ee92cd234f699f0ddf80067b65e1ef5ec6d8d9ab5c56ca19980e87c`；本地 `.atk/` 桥接器与运行证据仍被忽略，未纳入 ATK 发布包。
- 新版正式两 Case 复验：用升级后的项目本地 runner 建立 `round-134c6f02-3d27-4773-8667-d5d004935d71`，冻结波动性未知、最终双方各 2 次的预算；本次仅运行一次两 Case B0，批次 `batch-43d50cc8-1860-4e3f-8302-b7f194ddde4b` 封存 2 条 Execution。Magic run `atk-vnext-9ef57b9cf3b34a95804acbfc98700d6f` 与 `atk-vnext-27d1cc48eb8d4bddbbc7bbfabc26507f` 均为失败 Turn，事件确认 `authentication_failed`；ATK 两例均记 `infra_error`，没有把 Magic 报告的 `casePass=false` 计作效果失败。未生成候选或效果 Assessment，Round 以 `decision-3f5b98e2-6e69-4544-be2b-5277f6d6546d` 的 `close_without_adoption` 关闭。Official Toolkit 工作区仍干净，Magic Case 索引内容哈希仍为 `68da116f2ee92cd234f699f0ddf80067b65e1ef5ec6d8d9ab5c56ca19980e87c`。接入层本身已实测，模型认证故障阻断了本次真实效果对照；按用户允许的边界停止重复付费重跑。
- T12 本地运行路径复验：用 uv 离线安装一个最小 Python 包为 editable，Agent 从包的实际 `__file__` 定位源码并在 B0／候选两侧自动读取业务 Skill；用 Node 26 的本地链接调用同样的两侧链路。两种方式的 Skill 和 Agent 入口都写出实际绝对路径与 SHA-256，ATK 增量门禁通过；Node 链到工作区外的同内容副本、`PYTHONPATH` 误导入另一份包、向上级目录读取 Skill，或安装后不重建旧 wheel，门禁均返回 `insufficient`。Python fixture 使用 `-B` 防止 `__pycache__` 污染被测工作区；未配置时 ATK 正确阻断了未知文件写入。这些是隔离的本地合成工程测试，不代表 Magic Workspace 已切换该安装方式。

## 未完成的验收与限制

- **新增功能的验收边界**：T29／T43 的重试链、T45 的最终额度预留和效率门槛、T46 的跨轮 holdout 暴露、T47 的探针许可与额度、T42 的外部修复协议、T48 的提交中断恢复、T49 的缓存重建与原目录恢复已有合成测试。通用版本命令已在合成服务上验证，实际远端服务仍需由接入方提供只读、确实触达运行制品的命令与安全环境。目标 Agent 的外部写隔离由项目环境实际提供，`isolation_ref` 只记录核查依据，不是 OS 沙箱。候选可控制的 sidecar 指标现不能参与正式费用／工具门槛，但真实独立采集器及其证据引用仍需接入方审核；费用不可观测或来源不足时返回证据不足。
- T34 的最小真实链路已完成，决策走的是 `no_effect → reject → 恢复`；真实有效候选的 `keep → commit` 仍只有合成 E2E 覆盖。原始导出是否已全量脱敏未被证明，语义分析不得直接使用其未经审阅的自由文本；严格脱敏结构副本只在临时目录生成，不保留可分享原始 fixture。
- Magic Workspace 旧批次中的逻辑 `skill:create-util` 仍不能重用为新路径证据；新轮已按上述可信桥接逻辑重新执行两侧。真实 ATK 候选执行虽覆盖同一业务 Skill 的两 Case，但 B0 一例实为认证失败，原封存 Execution 无法改写为真实结果；新版两 Case B0 又遇两例认证失败。本轮不足以验证效果；Magic 原生两 Case 基线和先前单 Case 接入复跑不能替代有效的 ATK 正式对照。Python editable／Node 链接已在独立本地合成工程中验证，不能直接推及 Magic Workspace 的安装布局。
- T35–T39 有已知原因合成证据和门禁测试，但缺乏人工审核的真实业务配对；T42 的真实范围外交接、实际修复和新轮端到端复验未执行。当前 Codex 的语义判断不等于存储 Schema 验证。
- T12 的本地安装／误加载变体已有可运行测试，但真实业务安装布局未验收；T16–T27、T40–T41、T50–T52 的部分场景尚未逐项完整验收。10 Case 精确边际收益、项目外固定文件及合成服务版本的批间／批内漂移与不同被测边界的评分责任已验证。磁盘 `fsync`／替换失败已验证旧状态保留，`reject/defer` 中断、Round／操作记录落盘次序及 Git `smudge` 内容漂移有新增测试；操作中断完整矩阵和真实服务版本核查仍未跑通。runner、Agent 或准备命令的子进程若主动脱离其进程组，超时无法保证结束该进程，需由目标工程隔离策略处理。
- 未完整验证目标 Agent 的外部副作用隔离、真实业务多 Case 保护集、以及发布包在用户环境中的实际运行。不得以当前结果声称 vNext 达到方案的最终 Definition of Done 或线上普遍提升。

## 下一步开发顺序

1. 收口剩余 Git／磁盘故障异常矩阵和实际服务版本核查，确认恢复边界；真实业务安装布局在后续接入中复核。
2. 用可核验的已知缺陷配对材料完成 T35–T39 的语义诊断，并在真实本地 Agent 上完成一次范围外修复交接、新轮复验（T42）。
3. 模型认证恢复后，再按已冻结的重复依据新开 Magic Round 验证 ATK 正式两 Case 对照；此期间优先用离线工程收口真实非 Skill／多候选协议、外部副作用隔离与可信指标来源，避免重复运行失败模型调用。随后收口 W8 与最终 Definition of Done。
