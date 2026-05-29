# Codex Agent 迭代调优服务 — 产品需求文档

## 1. 产品概述
- **产品名称**：Agent Tune Kit
- **目标用户**：开发者
- **使用方式**：作为 Codex 插件以 Codex Skill 形式提供，用户在 Codex 内手动触发各 Skill，并结合本地手工命令完成完整流程
- **MVP 交付边界（2026-05-24）**：本仓库当前交付为本地 Codex 插件（包含 `.codex-plugin/plugin.json`、个人 marketplace 安装/冒烟/状态/回滚工具与 Skill 模板包）；本阶段不发布 public marketplace，不提供 brand assets/截图、自动升级、端到端一键编排、示例 Agent/数据集、自动回滚、旧安装命令兼容或完整 E2E 测试套件
- **目标**：提供一套通用、可复用的服务，用于对本地 Agent 服务进行迭代调优，通过手工执行 Codex Skill 完成测试、异常筛选、归因分析、跨版本调优验证、调优，并在统一的 `.atk/` 目录下按调优版本生成隔离的结果产物与最终 Markdown 报告
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
  - 用户需要准备需要调优的 Agent 服务以及可以用于评估的数据集
  - 系统需要在项目内维护统一父目录 `.atk/`
  - 用户提供的数据集需要在生成测试脚本时规范化写入 `.atk/datasets/`，并保证存在 `atk_id`
  - 所有共享脚本需要存储在 `.atk/runner/`
  - 所有版本化结果需要存储在 `.atk/results/{version}/`
  - 用户不需要手工指定当前调优版本，版本由各模块自动创建或识别

### 2.2 批量测试脚本生成模块（Codex Skill）
- **功能**：
  - Skill 阅读待调优 Agent 的源码与用户提供的数据集，生成 Python 测试脚本 `eval_runner.py`
  - Skill 在生成脚本前将用户提供的数据集写入 `.atk/datasets/` 作为 ATK 可持续运行数据集，生成的脚本读取该项目内数据集，避免外部数据集移动导致后续执行失败
  - 脚本需要能够：批量读取数据集、调用本地 Agent、记录每条样本的输入/输出/预期结果到 `eval_results.csv`、按需采集 Agent 运行日志到 `app.log`，并在可信场景下写入逐行日志引用 `agent_output_log_path`
  - **日志采集方案**：由 Skill 在阅读 Agent 源码后自行决定采集方式（例如 stdout 重定向、读取 Agent 写入的日志文件、Hook 日志框架等），并将逻辑固化在生成的 `eval_runner.py` 中；若 Agent 无可识别日志，则不生成 `app.log`
  - **逐行日志方案**：当可识别的日志源是同进程 Python `logging` 时，生成的 runner 可默认使用 stdlib `contextvars` 与 ATK 自有 `logging.Handler` 路由器，为每条源数据行写入 `.atk/results/vN/logs/row_{source_index:06d}.log`，并在 `eval_results.csv` 的 `agent_output_log_path` 中写入相对 POSIX 路径；即使该行没有日志记录，也要创建被引用的空文件。逐行日志只能包含 ATK 行上下文处于活动状态时发出的记录；stdout/stderr、子进程、多进程和行结束后的后台日志不进入逐行日志。若生成的并发逐行日志开关被禁用，`--concurrency > 1` 必须在运行输出中显式降级并使用 `app.log` 作为回退证据。
  - **数据集适配**：以 CSV 为主，列名由 Skill 推断；若数据集为其它格式，由 Skill 自行扩展读取逻辑
  - **数据集快照命名与去重**：init 阶段的 ATK 可持续运行数据集固定为 `.atk/datasets/original.csv`，让后续异常集、回归集等语义化数据集可以使用稳定命名。该文件不要求与用户原始文件字节级一致：若源 CSV 缺少 `atk_id`，Skill 需追加 `atk_id` 列，并按源数据行号从 `1` 开始填充；若源 CSV 已有 `atk_id`，仅在其值非空、唯一且为正整数时复用。若该文件不存在则写入规范化后的数据集；若规范化后内容完全一致则复用；若已存在但内容不同，则需在覆盖前确认。内容比较应使用可靠摘要（如 `sha256`），可先用文件大小做快速预筛。
  - **`eval_results.csv` 字段约定**：
    - 原则上**完整保留用户输入数据集的所有原始列**（列名、列顺序均不改动）；ATK 数据集必须包含固定列 `atk_id`，当源数据缺失时由 Skill 追加并使用 1-based 源行号填充；在此基础上追加 Agent 运行产生的列
    - **强约束**：Agent 的实际输出必须写入固定列名 `agent_output`；若 Agent 返回多字段结构化结果，可序列化为 JSON 字符串存入该列，或额外追加 `agent_output_*` 前缀的辅助列
    - **逐行日志引用**：固定列 `agent_output_log_path` 用于保存相对当前版本目录的逐行日志路径；无可信逐行日志时该列留空
    - 其它列（输入、预期结果等）不做命名强约束，下游 Skill 通过原数据集列名自行识别；`atk_id` 是 ATK 元数据，除非用户明确要求，不应传入待测 Agent
    - 若用户原数据集已存在名为 `agent_output` 或 `agent_output_log_path` 的列，Skill 需提示用户并与其确认改名方案后再生成脚本
  - **数据确认机制**：当 Agent 接入方式、数据集字段、日志位置或上述列名冲突无法可靠推断时，Skill 与用户交互确认后再生成脚本
- **输出**：
  - `.atk/runner/eval_runner.py`
- **版本目录兼容要求**：
  - `.atk/runner/` 中的脚本为跨版本共享脚本，不需要按 `v1`、`v2` 复制
  - `eval_runner.py` 运行时需要自动创建新的结果版本目录（详见第 4 章版本管理规则）
  - `eval_runner.py` 不应要求用户输入当前版本号
- **依赖处理**：
  - 尽量避免引入额外依赖；针对本地已有项目调优时，优先复用项目自身的依赖与虚拟环境

### 2.3 批量执行模块（用户通过 `atk-run` 执行生成的测试脚本）
- **功能**：
  - 用户触发 `atk-run`，由该 Skill 执行 `.atk/runner/eval_runner.py`
  - 简单机制实现批量测试执行和结果收集
  - 仅在「上一轮已有结果」时才新建版本目录；否则直接复用当前最新版本目录重跑
- **输出**：
  - 结果输出为目标版本目录下的 `eval_results.csv`
  - 如果服务中存在执行日志，则采集并存储为同版本目录下的 `app.log`
- **版本目录创建/复用规则**：
  - 扫描 `.atk/results/` 下所有 `vN` 目录，取数字最大的目录 `vMax`
  - 若不存在任何 `vN` 目录：创建 `v1` 并写入结果
  - 若 `vMax` 目录下**已存在 `eval_results.csv`**：新建 `v{Max+1}` 并写入结果（即使中间版本被删，也不补号）
  - 若 `vMax` 目录下**不存在 `eval_results.csv`**（例如上一次脚本跑挂未产出结果）：直接复用 `vMax` 目录，覆盖该目录下可能残留的中间文件
  - 脚本崩溃时不主动清理已创建的目录，由用户手工处理
  - 用户不需要指定版本号或结果目录

### 2.4 异常筛选模块（规则初始化、规则执行与大模型入口）
- **模式与入口**：用户根据需求选择不同 Skill 入口调用；规则模式将脚本初始化和脚本执行拆开，大模型模式直接写结果
  1. **规则初始化 Skill (`atk-init-failure-rule`)**：与用户交互确认筛选规则（如字段比较、阈值、关键字等）后，生成或更新共享脚本 `.atk/runner/failure_rule.py`。脚本已存在时，Skill 默认复用并允许用户决定是否更新规则
  2. **规则执行 Skill (`atk-find-failures-by-rule`)**：只执行已有 `.atk/runner/failure_rule.py`，从当前版本 `eval_results.csv` 输出 `failure_cases.csv`；如果脚本不存在，则提示先运行 `atk-init-failure-rule`
  3. **大模型模式 Skill (`atk-find-failures`)**：由 Skill 直接读取当前版本目录下的 `eval_results.csv` 与数据集中的预期结果，结合用户给定的判断说明（或由 Skill 基于预期结果自行推断）筛选异常样本，输出 `failure_cases.csv`
- **写入行为**：规则执行与大模型模式写入的 `failure_cases.csv` 文件名一致；若当前版本目录下已存在该文件，默认覆盖、不做备份或合并，并按各 Skill 的确认规则在覆盖前提示风险
- **异常定义来源**：
  - 规则模式由用户在 Skill 交互中显式给出
  - 大模型模式由用户给出自然语言说明，或在用户未指定时由 Skill 基于"Agent 输出 vs 预期结果"自行推断
- **数据确认机制**：当 `eval_results.csv` 字段含义不明、缺少预期结果或无法推断异常标准时，Skill 与用户确认后再继续
- **输出**：
  - 规则初始化生成共享规则脚本 `.atk/runner/failure_rule.py`
  - 规则执行或大模型模式生成当前版本目录下的异常筛选数据 `failure_cases.csv`
- **版本目录兼容要求**：
  - 规则执行和大模型模式均自动选择当前版本目录（见第 4 章规则），从中读取 `eval_results.csv`，向同一目录写入 `failure_cases.csv`
  - `.atk/runner/failure_rule.py` 为跨版本共享脚本，不按版本复制
  - 用户不需要指定版本号或结果目录

### 2.5 异常归因分析、跨版本调优验证与最终报告生成（Codex Skill）
- **功能**：
  - 对当前版本异常数据进行归因分析
  - 从 `v2` 起，自动对比当前版本与上一版本，验证上一轮 `tuning_plan.md` 中提出的调优措施是否在本轮真实生效（即上一轮要解决的异常在本轮是否不再出现）
  - 若当前版本为 `v1` 或不存在上一版本，则只生成单版本报告，并在报告中说明无上一版本可对比
  - 生成最终 Markdown 报告 `report.md`，包含：
    - 执行摘要
    - 测试结果统计
    - 异常数据清单
    - 归因分析
    - 跨版本调优验证章节（存在上一版本时）
- **跨版本验证逻辑（以"目标异常是否复现"为主判定）**：
  - 当前版本与上一版本均按第 4 章"数字最大的 vN 目录"规则确定
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
  - 当前版本目录按第 4 章规则识别（数字最大的 `vN`）
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
  - 可在当前版本存在 `failure_cases.csv` 后随时执行；推荐在 `atk-report` 之后、`atk-tune` 之前用于审阅具体异常
- **输入**：
  - 必需：当前版本目录下的 `failure_cases.csv`
  - 可选：同版本 `report.md`，仅提取有界摘要、归因或 3-5 条调优重点作为上下文
- **输出文件**：
  - 当前版本目录下的 `failure_cases.html`
  - 不生成 `report_summary.json`、其他 metadata JSON 或依赖文件
- **版本目录兼容要求**：
  - 按第 4 章规则识别当前版本（数字最大的 `vN`）
  - 仅读取当前版本的 `failure_cases.csv` 和可选同版本 `report.md`
  - 仅向同一版本目录写入 `failure_cases.html`
  - 用户不需要指定版本号或结果目录
- **HTML 生成要求**：
  - 使用 Python 标准库读取 CSV，保留所有原始列并兼容不同数据集字段
  - 对所有 CSV 与报告派生内容进行 HTML 转义
  - 使用内嵌 CSS/JS，包含摘要计数、搜索/筛选、分页、expected-vs-actual 对比、长字段展开或详情视图
  - 当存在 expected/expected_output、`agent_output`、failure/failure_reason/explanation/root-cause 类字段时优先展示，但不强制要求统一 Schema；前端提供临时角色切换以适配非标准字段
  - 同版本 `report.md` 缺失、格式异常或不可解析时继续生成 HTML，并说明报告上下文被跳过；报告解析是 best-effort 且 non-blocking
  - `agent_output_log_path` 等日志路径只在满足安全相对路径约束时生成可点击链接，否则仅作为证据文本展示

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
  - 调优模块按第 4 章规则识别当前版本目录（数字最大的 `vN`），并要求该目录已存在 `report.md`，否则提示用户
  - `tuning_plan.md` 写入与 `report.md` 同一版本目录
  - 下一轮测试执行时由批量执行模块自动创建新的版本目录
  - 当前阶段不要求实现自动回滚、基线版本管理或跨多版本趋势汇总
  - 用户不需要指定版本号或结果目录
- **输出**：
  - 调整后的 Agent 服务
  - 当前版本目录下的 `tuning_plan.md`

---

## 3. 技术栈
- **Codex Skill**：实现测试脚本生成、异常筛选（规则模式 / 大模型模式两个入口）、归因分析与报告、可选异常样本 HTML 可视化、Agent 调优
- **运行环境**：Python；优先复用待调优 Agent 项目自身的虚拟环境与依赖
- **数据标准化**：表格类产物统一为 CSV，报告与计划统一为 Markdown
- **版本化结果目录**：通过 `.atk/results/{version}/` 隔离多轮调优结果，`.atk/runner/` 作为跨版本共享脚本目录
- **自动版本识别**：各模块通过扫描 `.atk/results/` 自动创建或识别当前版本，减少用户手工传参
- **相邻版本对比**：报告 Skill 通过 `vN-1` 的 `tuning_plan.md` 与 `vN` 的 `eval_results.csv` / `failure_cases.csv` 完成上一轮调优验证，对比结果写入当前版本 `report.md`
- **回滚策略**：依赖用户的 git 工作流，本期不在产品内实现自动回滚

---

## 4. 版本管理要求
- **统一父目录**：
  - 所有调优相关产物统一放在 `.atk/` 下
  - `.atk/datasets/` 存放由 `atk-init` 写入的 ATK 可运行数据集
  - `.atk/runner/` 存放跨版本共享脚本
  - `.atk/results/` 存放按版本隔离的结果
- **版本命名**：
  - 每一轮调优结果使用 `v1`、`v2`、`v3` 等目录表示（`v` + 正整数）
  - 版本号由系统自动生成，用户不需要手工指定
- **"当前版本"统一定义**：
  - 除批量执行模块外，所有模块均把 `.atk/results/` 下**形如 `vN` 且 N 为正整数的目录中数字最大的那个**视为"当前版本"，不附加"必须包含某文件"的过滤
  - 非 `vN` 命名的目录被忽略
  - 若当前版本缺少模块所需的输入文件，模块**不回退到更早版本**，而是提示用户确认或修复
- **新版本创建规则（批量执行模块专用）**：
  - 判定依据是「当前最大 `vN` 目录下是否已存在 `eval_results.csv`」：
    - 不存在任何 `vN`：创建 `v1`
    - 当前最大 `vN` 下**已有 `eval_results.csv`**：新建 `v{N+1}`（即使中间版本被删，例如现存 `v1`、`v3`，仍创建 `v4`，不补号）
    - 当前最大 `vN` 下**没有 `eval_results.csv`**（例如上一次脚本跑挂未产出）：直接复用 `vN`，在原目录写入新结果
  - 脚本运行失败时不主动清理目录，由用户手工处理
- **各模块的输入 / 输出版本目录**：
  - 异常筛选：从当前版本读取 `eval_results.csv`，向同目录写入 `failure_cases.csv`
  - 报告生成：从当前版本读取 `eval_results.csv`、`failure_cases.csv`、可选 `app.log`；若存在上一版本（数字次大的 `vN`）则读取其 `tuning_plan.md`、`report.md`、`eval_results.csv`、`failure_cases.csv`；向当前版本写入 `report.md`
  - 异常可视化：从当前版本读取 `failure_cases.csv`，可选读取同版本 `report.md` 的有界摘要，向同目录写入 `failure_cases.html`
  - Agent 调优：从当前版本读取 `report.md`，向同目录写入 `tuning_plan.md`，并对 Agent 源码进行修改
- **相邻版本验证规则**：
  - 默认只比较当前版本 `vN` 与上一版本（数字次大的 `vN`，通常为 `vN-1`，存在跳号时为实际存在的次大版本）
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
1. 完成数据准备：本地待调优的 Agent 服务 + 评估数据集（默认 CSV）
2. 执行**批量测试脚本生成 Skill**，生成 `.atk/runner/eval_runner.py`；遇到 Agent 接入或字段不确定时按需与用户确认
3. 执行 `atk-run`，由其运行 `.atk/runner/eval_runner.py`，脚本自动创建 `.atk/results/v1/` 并写入 `eval_results.csv`（及可选 `app.log`）
4. 执行异常筛选（按需选择规则路径或大模型路径）：
   - **规则路径**：先执行 `atk-init-failure-rule` 交互确认规则并生成 `.atk/runner/failure_rule.py`，再执行 `atk-find-failures-by-rule` 运行该脚本并输出 `failure_cases.csv`
   - **大模型路径**：执行 `atk-find-failures`，直接读取当前版本 `eval_results.csv` 并输出 `failure_cases.csv`
5. 执行**归因分析与报告生成 Skill**，生成 `.atk/results/v1/report.md`
   - 当前版本为 `v1` 时为单版本报告，说明无上一版本可对比
6. 可选执行**异常样本可视化 Skill (`atk-visualize-failures`)**：基于当前 `failure_cases.csv` 生成 `.atk/results/v1/failure_cases.html`，同版本 `report.md` 作为可选、非阻塞上下文
7. 执行**Agent 调优 Skill**：基于 `report.md` 完成调优，并在 `v1/` 下写入 `tuning_plan.md`；建议用户随后做一次 git commit
8. 进入下一轮：再次执行 `atk-run`，自动创建 `.atk/results/v2/`
9. 重新执行异常筛选与报告生成 Skill，`v2/report.md` 自动读取 `v1/tuning_plan.md`，逐条核验上一轮目标异常在 `v2` 是否仍然出现，并给出"上一轮调优是否符合预期"的结论

---

## 6. 输出文件结构
```text
/.atk/
├── datasets/
│   └── original.csv             # atk-init 写入的 ATK 可运行数据集（含 atk_id）
├── runner/
│   ├── eval_runner.py          # 跨版本共享测试脚本
│   └── failure_rule.py          # 跨版本共享失败判定规则脚本（规则模式使用）
└── results/
    ├── v1/
    │   ├── eval_results.csv          # v1 测试结果
    │   ├── app.log              # v1 执行日志（可选）
    │   ├── failure_cases.csv   # v1 异常数据
    │   ├── failure_cases.html  # v1 异常样本可视化（可选）
    │   ├── report.md            # v1 最终报告（无上一版本时为单版本报告）
    │   └── tuning_plan.md       # v1 调优计划（由调优 Skill 生成，下一轮报告读取）
    └── v2/
        ├── eval_results.csv          # v2 测试结果
        ├── app.log              # v2 执行日志（可选）
        ├── failure_cases.csv   # v2 异常数据
        ├── failure_cases.html  # v2 异常样本可视化（可选）
        ├── report.md            # v2 最终报告（含基于 v1/tuning_plan.md 的跨版本验证）
        └── tuning_plan.md       # v2 调优计划
```

---

## 7. 产品交付要求
1. **Codex Skill**：
   - 模块化 Skill 模板，覆盖：测试脚本生成、异常筛选（规则模式与大模型模式两个独立入口）、归因分析与报告生成、可选异常样本 HTML 可视化、Agent 调优
   - 内置数据不确定性提示机制，遇到无法可靠推断的字段、Agent 接入方式、异常标准或样本对应关系时引导用户确认
   - 所有模块兼容 `.atk/results/{version}/` 结构，按"数字最大的 `vN`"统一识别当前版本
   - 所有模块在常规流程中不要求用户指定版本号
   - `.atk/runner/` 中的脚本为跨版本共享脚本，不按版本重复生成
   - 调优 Skill 必须在当前版本目录产出 `tuning_plan.md`；报告 Skill 在存在上一版本时必须读取其 `tuning_plan.md` 并产出跨版本调优验证章节（以"目标异常是否复现"为主判定）
2. **可安装部署**：
   - Codex Skill 可注册至 Codex 系统
