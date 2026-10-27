# Codex Agent 迭代调优服务 — 产品需求文档

## 1. 产品概述
- **产品名称**：Agent Tune Kit
- **目标用户**：开发者
- **使用方式**：作为 Codex 插件以 Codex Skill 形式提供，用户在 Codex 内手动触发各 Skill，并结合本地手工命令完成完整流程
- **MVP 交付边界（2026-05-24）**：本仓库当前交付为本地 Codex 插件（包含 `.codex-plugin/plugin.json`、个人 marketplace 安装/冒烟/状态工具与 Skill 模板包）；本阶段不发布 public marketplace，不提供 brand assets/截图、自动升级、端到端一键编排、示例 Agent/数据集、自动回滚、旧安装命令兼容或完整 E2E 测试套件
- **目标**：提供一套通用、可复用的服务，用于对已有的本地 Agent 服务进行迭代调优，通过手工执行 Codex Skill 完成测试、异常筛选、归因分析、跨版本调优验证、调优，并在统一的 `.atk/` 目录下按调优版本生成隔离的结果产物与最终 Markdown 报告
- **适用场景与开放设计**：
  - **Agent 形态**：本地实现的、可被代码直接调用的大模型 Agent 服务；Skill 通过阅读 Agent 源码理解其调用方式、参数与日志，不预设接口 Schema
  - **数据集**：默认以 CSV 为主（依据列名推断输入字段与预期结果），其它格式由 `eval_runner.py` 在生成时由 Skill 自适配，不预设 Schema
  - **异常定义**：由用户在异常筛选 Skill 中以自然语言/规则指定，或由 Skill 基于数据集中的预期结果与 Agent 实际输出自行推断
  - **调优手段**：不预设边界，Skill 根据归因结果自行选择调优策略（修改 Prompt、代码、参数、工具配置等）；本期通过用户的 git 工作流进行回滚兜底
- **特点**：
  - 门槛低：开发者可直接手动执行 Skill
  - 通用性强：对 Agent 形态与数据集格式不做强约束，由 Skill 阅读源码与数据自动适配
  - 全流程由 Codex Skill 驱动，用户手工控制执行顺序
  - 单轮迭代 = 完整跑完 2.2 → 2.6 一次；多轮迭代 = 用户手工重复整套流程，产生 `v2`、`v3` 等结果
  - 支持调优结果版本管理：每轮结果自动使用 `v1`、`v2` 等版本目录隔离
  - 用户不需要关心当前版本号，各功能模块自动创建、识别和读取当前版本结果
  - 支持相邻版本调优验证：从 `v2` 起，报告生成阶段默认对比当前版本与上一版本，验证上一轮提出的调优措施是否在本轮真实生效
  - 输出报告 Markdown 格式，便于阅读、追溯和版本管理
  - **数据不确定时提示机制**：模块在处理不确定数据（如字段命名或格式不一致）时，需要引导用户确认，避免误解

---

## 2. 流程设计

### 2.1 前置准备
- **功能**：
  - 用户需要提供已有的待调优 Agent 服务以及可以用于评估的数据集
  - 系统需要在项目内维护统一父目录 `.atk/`
  - 用户可先通过 `$atk-dataset` 从业务描述、样例或验收规则构建初始 `.atk/datasets/dataset.csv`
  - 当已有数据集缺少清晰预期结果语义时，用户可通过 `$atk-dataset` 为现有 `.atk/datasets/dataset.csv` 在原有预期字段中补充有依据的答案或验收条件
  - 用户提供或构建的数据集需要在生成测试脚本时由 `$atk-init` 校验/规范化写入 `.atk/datasets/`，并保证存在 `atk_id`
  - 所有共享脚本需要存储在 `.atk/runner/`
  - 所有版本化结果需要存储在 `.atk/results/{version}/`
  - 用户不需要手工指定当前调优版本，版本由各模块自动创建或识别

### 2.1.0 ATK Dataset：统一准备与修订评估数据（按需）
- 入口 `$atk-dataset`：按已有资料完成导入/构建样本、补充可信判断依据或根据反馈局部修订，不要求用户选择模式。
- 默认只保留 `.atk/datasets/dataset.csv`。已有可用数据直接复用，不自动迁移、扩写或覆盖。
- 保留已有有效 `atk_id`、行顺序、原始输入、字段名及未涉及的值。只为缺少 ID 的新数据补充非空、唯一、正整数 ID。
- 直接使用 expected、label、acceptance_criteria、ground_truth 等现有字段，不强制统一为 ground_truth，也不要求全局答案风格。
- 缺少事实时留空或保留原值并说明缺口；输入可执行的样本可进入评测，无法支持的判定交由 atk-diagnose 保持 unknown。
- 直接对话反馈无需先打开 HTML 和导出文件。原 dataset_review.csv 格式继续支持；核对来源及样本后修订，明确请求不重复确认。
- 按需使用脚本 update_dataset.py 原子写回指定答案字段；未改变的 CSV 不重写。历史结果和判定不随数据修订改写。
- 可选调用 atk-visualize-dataset 浏览，不作为数据准备完成条件。无 Agent 执行、结果版本、准备报告或 manifest。
- 未接入或输入映射改变时继续 atk-init；已接入且映射兼容时继续 atk-eval。标准修订不等于 Agent 效果改善。

### 2.2 批量测试脚本生成模块（Codex Skill）
- **功能**：
  - Skill 阅读待调优 Agent 的源码与用户提供的数据集，生成 Python 测试脚本 `eval_runner.py`
  - Skill 在生成脚本前将用户提供的数据集写入 `.atk/datasets/` 作为 ATK 可持续运行数据集，生成的脚本读取该项目内数据集，避免外部数据集移动导致后续执行失败
  - 共享引擎批量读取数据集、调用 Agent，并逐项写入 `eval_results.csv`；项目 runner 只保留输入构造、调用、分组、输出映射及留存策略，不复制引擎实现
  - **V2 日志与证据**：按声明采集 Python `logging`、可选 Loguru 及项目显式事件，写入每个 `executions/<id>/events.jsonl`；`app.log` 是同目录下的日志浏览视图，可能为空。逐样本 `log_path` 指向该执行目录；同病例的多个样本可共享一次执行与日志，不承诺逐术式隔离。采集边界与覆盖缺口回源执行证据契约
  - **V1 兼容日志**：共享 V1 接入仍支持版本级 `app.log` 和可信 `logs/row_*.log`。逐行日志仅覆盖活动行上下文内的同进程 Python logging，子进程、未传播上下文的后台工作等不在其范围；禁止将旧 V1 路径要求套用到 V2
  - **数据集适配**：以 CSV 为主，列名由 Skill 推断；若数据集为其它格式，由 Skill 自行扩展读取逻辑
  - **数据集快照命名与去重**：init 阶段的 ATK 可持续运行数据集固定为 `.atk/datasets/dataset.csv`，表达这是 ATK 评测流程使用的项目内数据集，便于在不同业务流程中复用。该文件不要求与用户原始文件字节级一致：若源 CSV 缺少 `atk_id`，Skill 需追加 `atk_id` 列，并按源数据行号从 `1` 开始填充；若源 CSV 已有 `atk_id`，仅在其值非空、唯一且为正整数时复用。若该文件不存在则写入规范化后的数据集；若规范化后内容完全一致则复用；若已存在但内容不同，则需在覆盖前确认。内容比较应使用可靠摘要（如 `sha256`），可先用文件大小做快速预筛。
  - **`eval_results.csv` 字段约定**：
    - 原则上**完整保留用户输入数据集的所有原始列**（列名、列顺序均不改动）；ATK 数据集必须包含固定列 `atk_id`，当源数据缺失时由 Skill 追加并使用 1-based 源行号填充；在此基础上追加 Agent 运行产生的列
    - **强约束**：Agent 的实际输出必须写入固定列名 `agent_output`；若 Agent 返回多字段结构化结果，必须将完整结构化结果序列化为 JSON 字符串存入该列，不得额外追加 `agent_output_*` 前缀的辅助列
    - **逐行日志引用**：固定列 `log_path` 用于保存相对当前版本目录的逐行日志路径；无可信逐行日志时该列留空
    - 其它列（输入、预期结果等）不做命名强约束，下游 Skill 通过原数据集列名自行识别；`atk_id` 是 ATK 元数据，除非用户明确要求，不应传入待测 Agent
    - 引擎保留输出列按顺序为 `agent_output`、`output_status`、`error_type`、`error_message`、`log_path`，统一由共享引擎的 `OUTPUT_FIELDS` 定义。输入重名时，Skill 需按已有授权执行明确改名，授权不明则先确认，并同步适配器及判定规则中的字段引用；禁止静默改名、丢列或覆盖原值
    - 共享引擎的 V1/V2 和 `atk eval --check` 都在调用 Agent、分配结果版本前校验重名，错误须包含数据集路径及全部冲突列。写入器在打开输出文件前再次拒绝重复表头。数据准备阶段仍可原样保留这些业务字段；不回写历史结果，自管旧 runner 需单独核对或迁移
  - **数据确认机制**：当 Agent 接入方式、数据集字段、日志位置或上述列名冲突无法可靠推断时，Skill 与用户交互确认后再生成脚本
- **输出**：
  - `.atk/runner/eval_runner.py`
- **版本目录兼容要求**：
  - `.atk/runner/` 中的脚本为跨版本共享脚本，不需要按 `v1`、`v2` 复制
  - `eval_runner.py` 运行时需要自动创建新的结果版本目录（详见第 4 章版本管理规则）
  - `eval_runner.py` 不应要求用户输入当前版本号
- **依赖处理**：
  - 尽量避免引入额外依赖；针对本地已有项目调优时，优先复用项目自身的依赖与虚拟环境

### 2.3 批量执行模块（用户通过 `atk-eval` 执行生成的测试脚本）
- **功能**：
  - 用户触发 `atk-eval`，由该 Skill 执行 `.atk/runner/eval_runner.py`
  - 简单机制实现批量测试执行和结果收集
  - 通过 `--only-failures` 从当前评测目标的有效 failed 判定中选择 canonical 样本；仅 legacy 目标兼容其自身失败表，不搜索旧轮次，不直接执行结果 CSV
  - 支持 `--ids ID...` 显式补测 unknown 或技术失败，与 `--only-failures` 互斥；先校验全部 ID、按 canonical 顺序筛选，再应用 offset/limit，分组适配器保留完整病例上下文
  - V2 mapper 可声明逐样本 `output_status=ok/error` 及字符串错误详情；摘要独立统计技术失败和 ID，不自动转换为业务 failed
- **输出**：
  - 结果输出为目标版本目录下的 `eval_results.csv`
  - V2 保存 `run.json` 和独立 `executions/` 现场及日志；V1 兼容版本级日志。默认执行进度写入 stderr，`--no-progress` 可关闭，不污染 JSON stdout
- **版本目录创建/复用规则**：
  - 唯一规则回源[共享版本契约](shared-versioning-and-confirmation.md#current-version-vs-new-version-creation)；评测和显式本地证据导入均使用共享分配器及写占用
  - 只复用完全空目录；任何非空目录即使没有 CSV 也受保护，失败现场不自动清理。正常流程不要求用户指定版本号
- **异常样本回放规则**：
  - 先固定当前评测目标，再分配新版本。缺少有效失败判定、失败集为空或 ID 不在 canonical 数据集中时停止，不回退全量或更旧的失败表
  - `--limit` / `--offset` 在异常样本筛选完成后再应用，用于对异常子集做有界运行

### 2.4 统一异常识别与初步诊断（atk-diagnose）
- 单一入口组合规则检查与 Codex 语义判断。规则脚本 `.atk/runner/failure_rule.py` 按需准备、复用和执行，不要求用户选择模式。
- 使用现有行为标准与预期结果；无法确定标准的样本记为 unknown，不阻塞其余样本的判断。
- `failure_cases.csv` 仅保存确认失败行并保留源字段，继续供失败复测和浏览使用。
- `judgments.csv` 记录每个已执行 atk_id 的 passed/failed/unknown、reason 和源 eval_results.csv 的 results_sha256。
- 未检查、缺证据、判定过期或冲突均不能当作通过。规则只覆盖格式时，格式通过不代表业务成功。
- 查看重点失败的日志与相关代码，区分事实和原因假设；详细归因、跨轮复核及 report.md 仍归 atk-report。
- 覆盖率与未知样本单独报告；保持原结果不变，尊重用户编辑，不引入自动提交、回退或额外状态管理。

### 2.5 异常归因分析、跨版本调优验证与最终报告生成（Codex Skill）
- **功能**：
  - 对当前版本异常数据进行归因分析
  - 从 `v2` 起，自动对比当前版本与上一版本，验证上一轮 `tuning_plan.md` 中提出的调优措施是否在本轮真实生效（需要目标样本在本轮具有明确有效的判定；未知或未执行不能标为已解决）
  - 若当前版本为 `v1` 或不存在上一版本，则只生成单版本报告，并在报告中说明无上一版本可对比
  - 生成最终 Markdown 报告 `report.md`，包含：
    - 执行摘要
    - 测试结果统计
    - 异常数据清单
    - 归因分析
    - 跨版本调优验证章节（存在上一版本时）
- **跨版本验证逻辑（以"目标异常是否复现"为主判定）**：
  - 当前及上一评测目标按第 4 章及共享契约的版本选择规则确定
  - 报告 Skill 读取上一版本目录下的 `tuning_plan.md`，逐条获取上一轮宣称要解决的异常 / 目标样本
  - 在当前版本的 `eval_results.csv` 与 `failure_cases.csv` 中查找对应样本，判断该异常本轮是否仍然出现
  - 样本对应识别策略（由 AI 自行推理，无需用户预先指定标识列）：
    - 若数据集中存在明显可作为唯一标识的列（如 `case_id`、`id`、`query` 等），优先使用
    - 否则结合输入内容、预期结果、上一轮 `tuning_plan.md` / `report.md` 中描述的问题特征（异常类型、归因项等）进行语义匹配
    - 仅当上述推理均无法可靠定位时，才触发数据不确定时提示机制，由用户确认
  - 对上一轮每条目标异常，给出以下状态之一：
    - 已解决（本轮该样本不再被判定为异常）
    - 部分解决（同类异常仅部分样本恢复正常）
    - 未解决（本轮仍为异常）
    - 无法判断（样本/数据缺失或对应关系不明）
- **跨版本验证结论**：
  - 报告需明确给出"上一轮调优是否符合预期"的结论
  - 判断主依据：上一轮 `tuning_plan.md` 中目标异常的解决比例
  - 当 `tuning_plan.md` 缺失、目标样本无法匹配或数据残缺时，报告需说明置信边界并降级为单版本报告输出
  - 报告同时可附带异常总量变化等观察性信息，但仅作为参考，不作为是否符合预期的主判定
- **输出文件**：
  - 当前版本目录下的 `report.md`
- **版本目录兼容要求**：
  - 当前评测目标目录按第 4 章及共享契约识别
  - 读取当前版本目录下的 `eval_results.csv`、`failure_cases.csv`，若存在 `app.log` 一并读取
  - 若存在上一版本目录，读取其 `tuning_plan.md`、`report.md`、`eval_results.csv`、`failure_cases.csv`，若存在 `app.log` 一并读取
  - 报告写入当前版本目录的 `report.md`
  - 报告需要标注本次版本号；存在上一版本时同时标注对比版本号
  - 用户不需要指定版本号或结果目录
- **报告"跨版本调优验证"章节结构**：
  - 对比版本：当前版本、上一版本
  - 上一轮调优计划摘要（来源：上一版本 `tuning_plan.md`）
  - 上一轮目标异常逐条复核：已解决 / 部分解决 / 未解决 / 无法判断
  - 新增问题：本轮出现而上一轮未记录的异常或归因类型
  - 观察性指标变化（异常总数、比例等，可选）
  - 验证结论：上一轮调优是否符合预期，是否建议继续下一轮调优

### 2.5.1 异常样本可视化（可选 Codex Skill `atk-visualize-failures`）
- **功能**：
  - 从当前版本目录下的 `failure_cases.csv` 生成便于人工审阅的静态 HTML 浏览器 `failure_cases.html`
  - 通过插件内固定 Python 标准库脚本生成 HTML，避免模型临场生成可视化、LLM 摘要、项目内模板漂移或额外依赖
  - 该 Skill 是独立的可选审阅步骤，不并入 `atk-report`，也不改变报告生成、异常筛选或调优语义
  - 可在当前版本存在 `failure_cases.csv` 后随时执行；推荐在 `atk-report` 之后、`atk-optimize` 之前用于审阅具体异常
- **输入**：
  - 必需：当前版本目录下的 `failure_cases.csv`
  - 可选：同版本 `report.md`，仅提取有界摘要、归因或 3-5 条调优重点作为上下文
  - 可选（用于跨版本分析，只读、永不修改、缺失不阻塞）：
    - 历史版本 `v1..v(N-1)/failure_cases.csv` —— 推导每个 case 的跨版本失败/通过状态
    - 历史版本 `v1..v(N-1)/eval_results.csv` —— 仅流式读取 `atk_id` 列（每文件上限 64 MiB），用于区分四态状态（`passed` / `failed` / `not_tested` / `unknown`），并检测 `atk-eval --only-failures` 子集执行
    - 上一版本 `v(N-1)/tuning_plan.md` —— 仅解析 `## 目标异常清单` 段落（文件上限 256 KiB），用于计算上轮调优目标命中率
- **输出文件**：
  - 当前版本目录下的 `failure_cases.html`
  - 不生成 `report_summary.json`、其他 metadata JSON、sidecar 数据文件或依赖文件
- **版本目录兼容要求**：
  - 按第 4 章及共享契约识别当前评测目标
  - 必须读取：当前版本的 `failure_cases.csv`
  - 可选读取：同版本 `report.md`；以及历史版本 `v1..v(N-1)` 下的 `failure_cases.csv`、`eval_results.csv`（仅 `atk_id` 列）、`v(N-1)/tuning_plan.md`，全部 best-effort 非阻塞，任意缺失/超限/解析失败均降级处理而不报错
  - 仅向同一版本目录写入 `failure_cases.html`，不修改任何历史版本目录下的任何文件
  - 用户不需要指定版本号或结果目录；用户可通过 `--no-history` 显式关闭跨版本读取
- **HTML 生成要求**：
  - 使用 Python 标准库读取 CSV，保留所有原始列并兼容不同数据集字段
  - 对所有 CSV 与报告派生内容进行 HTML 转义
  - 使用包内 `agent_tune_kit/visualize/assets/failures/` 的内嵌 CSS/JS 构建（零 CDN、零外链、零运行时依赖），包含摘要计数、搜索/筛选、分页、expected-vs-actual 对比、长字段展开或详情视图
  - 当存在历史版本数据时，HTML 提供三标签视图（`总览` / `跨版本` / `Case 浏览`）：跨版本趋势（执行数 / 失败数 / 失败率双轴）、按版本演化的失败原因堆叠柱、三态/四态 per-case 状态（`passed` / `failed` / `not_tested` / `unknown`）、上一轮调优目标命中率（resolved / partial / unresolved / indeterminate）、顽固 case 列表（在 2 个及以上历史版本中均失败的 atk_id，表格显示上限 200 行），并在某版本执行样本数显著少于上一版本（< 50%）时显示 subset run 提示条，避免与全量执行版本直接同口径比较
  - 当存在 expected/expected_output、`agent_output`、failure/failure_reason/explanation/root-cause 类字段时优先展示，但不强制要求统一 Schema；前端提供临时角色切换以适配非标准字段
  - 同版本 `report.md` 缺失、格式异常或不可解析时继续生成 HTML，并说明报告上下文被跳过；报告解析是 best-effort 且 non-blocking
  - 所有跨版本读取均为 best-effort：任意历史文件缺失/超限/损坏/无 `atk_id` 列时，对应视图降级并提示原因，绝不影响 HTML 写入
  - `log_path` 等日志路径只在满足安全相对路径约束时生成可点击链接，否则仅作为证据文本展示

### 2.5.2 数据集可视化与质检（可选 Codex Skill `atk-visualize-dataset`）
- **功能**：
  - 从 `.atk/datasets/dataset.csv` 生成便于人工浏览与质检的静态 HTML 浏览器 `.atk/datasets/dataset.html`
  - 通过插件内固定 Python 标准库脚本生成 HTML，避免模型临场生成可视化、LLM 摘要、项目内模板漂移或额外依赖
  - 该 Skill 是独立的可选审阅步骤，不并入 `atk-dataset` 或 `atk-init`，也不改变数据集构建、校验或评估语义
  - 重点支持快速确认每行 ground_truth 是否符合预期，并发现 ground_truth 错误、缺失或不合理的样本
  - 可在 `.atk/datasets/dataset.csv` 存在后随时执行；推荐在 `atk-dataset` 之后、`atk-init` 之前用于浏览与质检
- **输入**：
  - 必需：`.atk/datasets/dataset.csv`（非版本化，位于 `.atk/datasets/`，不走 `.atk/results/vN`）
  - 自动识别 `atk_id` 列、输入列与 `expected|ground_truth|answer|label|target` 类期望列；无法识别时降级展示原始列并允许前端临时重映射
- **输出文件**：
  - `.atk/datasets/dataset.html`（单文件、离线、自包含）
  - 不生成 metadata JSON、sidecar 数据文件或依赖文件；审阅结果由前端经 Blob 下载为 `dataset_review.csv`，不写入仓库
- **HTML 生成要求**：
  - 使用 Python 标准库读取 CSV，保留所有原始列并兼容不同数据集字段
  - 对所有 CSV 派生内容进行 HTML 转义，并中和内联 JSON 中的 `</script>` 等风险序列
  - 使用插件持有的内嵌 HTML/CSS/JS 与自包含离线资源构建，保留 `agent_tune_kit/visualize/assets/dataset/vendor/echarts.min.js` 的单文件资产内联契约；零 CDN、零外链、零运行时依赖，用户不需要安装 Node、React、Vite 或任何前端依赖
  - 提供尽可能还原 `dataset-visualize` 视觉与交互的一体化静态页面：橙色/石板色 sticky header、产品化卡片布局，以及两标签视图（`数据列表` / `字段特征分析`）
  - `数据列表` 支持全字段搜索、动态分类 facet 过滤、字段显示控制、排序、分页、质量标签过滤、详情抽屉，并突出「输入 vs ground_truth」对照
  - `字段特征分析` 提供字段角色映射、总行/总列/ground_truth/问题样本 KPI、分类分布摘要和逐字段统计；首版以核心视觉还原和静态阅读体验优先，不迁移浏览器上传、Gemini/AI Studio、数据集编辑或源码级 React 要求
  - **数据集质检 lint**：逐行计算并提供过滤标签，包括空 ground_truth、空输入、重复/缺失/非正整数 `atk_id`、冲突样本（相同输入不同 ground_truth）、完全重复样本（输入+ground_truth 相同）、ground_truth 过短/过长（长度离群）
  - **客户端审阅**：每行详情提供一个面向预期结果修正的自由文本反馈框，按 `atk_id` 和行号存入 `localStorage`；「导出审阅结果」经 Blob 下载 `dataset_review.csv`（仅含 `atk_id`、`row_number`、`review_feedback`，且只导出有反馈的行），完全离线、无后端；原始数据内容通过 `atk_id` / `row_number` 回连 `.atk/datasets/dataset.csv`，不在审阅导出中重复；审阅结果可通过 `$atk-dataset` 作为用户对现有预期结果的修正意见回写到 `.atk/datasets/dataset.csv`
  - 数据集为空、表头无法解析或目标文件已存在且未确认覆盖时给出明确提示并安全退出，不破坏已有文件

### 2.6 Agent 调优模块（Codex Skill）
- **功能**：
  - 以当前版本目录下的 `report.md` 作为本轮调优依据
  - 由 Skill 自行决定调优手段（Prompt、代码、参数、工具配置等），不预设边界
  - 若 `report.md` 包含跨版本调优验证章节，优先针对未解决问题与新增问题制定本轮计划
  - 调优执行不需要用户对每项改动二次确认；回滚由用户的 git 工作流兜底
- **调优计划落盘**：
  - 调优完成后，Skill 在**当前版本目录**写入 `tuning_plan.md`，作为下一轮报告 Skill 进行跨版本验证的输入
  - `tuning_plan.md` 采用**固定章节结构**（便于下一轮 Skill 稳定解析），但章节内容允许自由文本，不强制结构化字段：
    1. `## 目标异常清单`：逐条描述本轮要解决的异常，每条至少包含「问题简述 + 触发输入特征 / 预期结果 / 实际输出」之一，使下一轮 AI 能据此在新 `eval_results.csv` 中定位对应样本；若用户数据集存在天然的唯一标识列，Skill 应自动在描述中引用该标识值；无明显标识时无需强行造一个
    2. `## 调优手段`：本轮采取了哪些改动（Prompt、代码、参数、工具配置等）以及为何这么改
    3. `## 关联改动`：受影响的文件列表，建议附上 git commit hash（非强制）
  - 上述章节标题为强约束（一级 `##` + 中文标题原文），章节内部允许自由 Markdown
  - 建议（非强制）调优 Skill 在结束时引导用户创建一个 git commit，以便回滚和后续 diff 追溯
- **版本目录兼容要求**：
  - 调优模块按第 4 章及共享契约识别当前评测目标目录，并要求该目录已存在 `report.md`，否则提示用户
  - `tuning_plan.md` 写入与 `report.md` 同一版本目录
  - 下一轮测试执行时由批量执行模块自动创建新的版本目录
  - 当前阶段不要求实现自动回滚、基线版本管理或跨多版本趋势汇总
  - 用户不需要指定版本号或结果目录
- **输出**：
  - 调整后的 Agent 服务
  - 当前版本目录下的 `tuning_plan.md`

---

## 3. 技术栈
- **Codex Skill**：实现测试脚本生成、统一异常诊断（按需组合规则与语义判断）、归因分析与报告、可选异常样本 HTML 可视化、Agent 调优
- **运行环境**：Python；优先复用待调优 Agent 项目自身的虚拟环境与依赖
- **数据标准化**：表格类产物统一为 CSV，报告与计划统一为 Markdown
- **版本化结果目录**：通过 `.atk/results/{version}/` 隔离多轮调优结果，`.atk/runner/` 作为跨版本共享脚本目录
- **自动版本识别**：各模块通过扫描 `.atk/results/` 自动创建或识别当前版本，减少用户手工传参
- **相邻版本对比**：报告 Skill 通过上一评测目标的 `tuning_plan.md` 与当前目标的结果及有效判定完成上一轮调优验证，对比结果写入当前版本 `report.md`
- **回滚策略**：依赖用户的 git 工作流，本期不在产品内实现自动回滚

---

## 4. 版本管理要求
- **统一父目录**：
  - 所有调优相关产物统一放在 `.atk/` 下
  - `.atk/datasets/` 存放 ATK 可运行数据集；由 `$atk-dataset` 按需创建或修订，复用原有字段与 ID；`$atk-init` 负责输入映射与 runner 接入
  - `.atk/runner/` 存放跨版本共享脚本
  - `.atk/results/` 存放按版本隔离的结果
- **版本命名**：
  - 每一轮调优结果使用 `v1`、`v2`、`v3` 等目录表示（`v` + 正整数）
  - 版本号由系统自动生成，用户不需要手工指定
- **"当前版本"统一定义**：
  - 最新 Run 与当前评测目标分开识别。诊断、报告、调优、失败浏览和失败复测使用 `resolve_evaluation_version()`；仅跳过已验证的 evidence-only Run
  - 非 `vN` 命名目录被忽略；当前评测身份损坏或必需文件缺失时停止，不按“找到一个可用 CSV”回退
- **新版本创建规则（评测及显式证据导入）**：
  - 完整算法及写占用边界回源[共享版本契约](shared-versioning-and-confirmation.md#current-version-vs-new-version-creation)，实现回源共享 `allocate_next_results_version()`
  - 无版本时创建 v1；最大版本完全为空才可复用，非空则按最大编号加一，不补号。未知写占用阻止写入，不自动接管、清理或续写失败目录
- **各模块的输入 / 输出版本目录**：
  - 异常筛选：从当前版本读取 `eval_results.csv`，向同目录写入 `failure_cases.csv`
  - 报告生成：从当前评测目标读取结果、判定、失败表及可用执行证据；上一评测目标通过 `resolve_previous_version()` 识别，跳过 evidence-only；向当前目录写入 `report.md`
  - 异常可视化：从当前版本读取 `failure_cases.csv`，可选读取同版本 `report.md` 的有界摘要，向同目录写入 `failure_cases.html`
  - Agent 调优：从当前版本读取 `report.md`，向同目录写入 `tuning_plan.md`，并对 Agent 源码进行修改
- **相邻版本验证规则**：
  - 默认只比较当前评测目标与上一评测目标，不把 evidence-only Run 当作被评分版本
  - 跨版本验证结果写入当前版本 `report.md`，不新增独立对比报告文件
  - 当上一版本不存在或缺少 `tuning_plan.md` 时，报告 Skill 退化为单版本报告并说明原因
- **目录职责**：
  - `.atk/datasets/`：存放生成 runner 时使用的 ATK 可运行数据集；runner 读取这里的稳定副本，不依赖外部源路径
  - `.atk/runner/`：跨版本共享脚本，例如 `eval_runner.py`、`failure_rule.py`
  - `.atk/results/{version}/`：存放该轮调优的测试结果、日志、异常数据、报告与调优计划
- **兼容要求**：
  - 各版本结果之间不能互相覆盖
  - `runner/` 中的共享脚本通过自动版本识别复用，不为每个版本生成重复 runner 文件
- **本阶段非目标**：
  - 不实现自动回滚或恢复历史代码（依赖用户 git）
  - 不实现基线版本标记或基线版本管理
  - 不生成跨多版本趋势看板或汇总报告
  - 不强制统一数据集字段或指标 Schema
- **未来扩展**：
  - 基线版本标记
  - 跨版本汇总报告
  - 版本回滚
  - 多版本趋势分析

---

## 5. 使用示例（手工执行）
1. 需要准备或修订评估数据时执行 `$atk-dataset`；已有合适数据可直接接入或评测，浏览与反馈导出均为可选。
2. 执行**批量测试脚本生成 Skill**，生成 `.atk/runner/eval_runner.py`；遇到 Agent 接入或字段不确定时按需与用户确认
3. 执行 `atk-eval`，由共享引擎调用项目适配器，首次运行写入 `.atk/results/v1/`；V2 同时保存 run/execution 现场
4. 执行 `atk-diagnose`，按需组合规则与语义判断，生成 `failure_cases.csv` 和 `judgments.csv`，给出初步定位。
5. 执行**归因分析与报告生成 Skill**，生成 `.atk/results/v1/report.md`
   - 当前版本为 `v1` 时为单版本报告，说明无上一版本可对比
6. 可选执行**异常样本可视化 Skill (`atk-visualize-failures`)**：基于当前 `failure_cases.csv` 生成 `.atk/results/v1/failure_cases.html`，同版本 `report.md` 作为可选、非阻塞上下文
7. 执行**Agent 调优 Skill**：基于 `report.md` 完成调优，并在 `v1/` 下写入 `tuning_plan.md`；建议用户随后做一次 git commit
8. 进入下一轮：再次执行 `atk-eval`，自动创建 `.atk/results/v2/`
9. 重新执行异常筛选与报告生成 Skill，`v2/report.md` 自动读取 `v1/tuning_plan.md`，逐条核验上一轮目标异常在 `v2` 是否仍然出现，并给出"上一轮调优是否符合预期"的结论

---

## 6. 输出文件结构

当前正式文件树、按需产物及阅读用途统一见 [README 的完整输出结构](../README.md#完整输出结构)。
V2 使用独立 execution 现场；V1 旧结果和日志继续可读。仅导入证据的 Run 不要求评分 CSV，
有目录或封存文件不等于业务通过；字段、版本及错误语义以[共享契约](shared-versioning-and-confirmation.md)为准。

---

## 7. 产品交付要求
1. **Codex Skill**：
   - 模块化 Skill 模板，覆盖：数据集构建、现有数据集 ground truth 补齐、测试脚本生成、统一异常诊断（规则准备和复用为内部行为）、归因分析与报告生成、可选异常样本 HTML 可视化、Agent 调优
   - 内置数据不确定性提示机制，遇到无法可靠推断的字段、Agent 接入方式、异常标准或样本对应关系时引导用户确认
   - 所有模块兼容 `.atk/results/{version}/`，按共享契约区分最新 Run、当前及上一评测目标
   - 所有模块在常规流程中不要求用户指定版本号
   - `.atk/runner/` 中的脚本为跨版本共享脚本，不按版本重复生成
   - 调优 Skill 必须在当前版本目录产出 `tuning_plan.md`；报告 Skill 在存在上一版本时必须读取其 `tuning_plan.md` 并产出跨版本调优验证章节（以"目标异常是否复现"为主判定）
2. **可安装部署**：
   - Codex Skill 可注册至 Codex 系统

判定覆盖与未知状态遵循 `docs/shared-versioning-and-confirmation.md` 的 Judgment coverage：缺少失败记录不表示通过。
