# Codex Agent 迭代调优服务 — 产品需求文档

## 1. 产品概述
- **产品名称**：Agent tune kit
- **目标用户**：开发者
- **使用方式**：通过手工触发的 Codex Skill 扩展，使用 Codex 命令与手工执行命令结合完成完整流程
- **目标**：提供一套通用、可复用的服务，用于对不同类型 Agent 进行迭代调优，通过手工执行 Codex Skill 完成测试、异常筛选、归因分析、调优，并在统一的 `agent-tuning/` 目录下按调优版本生成隔离的结果产物与最终 Markdown 报告。
- **特点**：
  - 门槛低：开发者可直接手动执行 Skill
  - 通用性强：支持多类型 Agent，数据集格式不限
  - 全流程由 Codex Skill 完成，手工控制执行顺序
  - 单轮迭代，多轮迭代可手工触发
  - 支持调优结果版本管理：每轮结果自动使用 `v1`、`v2` 等版本目录隔离
  - 用户不需要关心当前版本号，各功能模块自动创建、识别和读取当前版本结果
  - 输出报告 Markdown 格式，便于阅读、追溯和版本管理
  - **数据不确定时提示机制**：模块在处理不确定数据（如字段命名或格式不一致）时，需要引导 Agent 与用户确认，避免误解

---

## 2. 流程设计

### 2.1 前置准备
- **功能**：
  - 用户需要准备需要调优的 Agent 服务以及可以用于评估的数据集
  - 系统需要在项目内维护统一父目录 `agent-tuning/`
  - 所有共享脚本需要存储在 `agent-tuning/runner/`
  - 所有版本化结果需要存储在 `agent-tuning/results/{version}/`
  - 用户不需要手工指定当前调优版本，版本由各模块自动创建或识别

### 2.2 批量测试脚本生成模块（Codex Skill）
- **功能**：
  - 根据 Agent 类型和数据集生成 Python 测试脚本 `test_runner.py`
  - 包含输入输出记录逻辑和日志管理
  - **数据确认机制**：
    - 对测试数据中存在潜在字段不一致或缺失时，Skill 先与用户确认使用的字段和格式，之后再开始实现 test_runner.py 脚本
- **输出**：
  - `agent-tuning/runner/test_runner.py`
- **版本目录兼容要求**：
  - `agent-tuning/runner/` 中的脚本为跨版本共享脚本，不需要按 `v1`、`v2` 复制
  - `test_runner.py` 运行时需要自动创建新的结果版本目录
  - 新版本号通过扫描 `agent-tuning/results/` 下已有 `vN` 目录自动生成，例如不存在版本时创建 `v1`，已有 `v1` 时创建 `v2`
  - `test_runner.py` 不应要求用户输入当前版本号
  - 如果发现版本目录异常或存在无法自动判断的冲突，Skill 才需要提示用户确认
- **依赖处理**：
  - 尽量避免使用复杂的额外依赖
  - 是针对本地已有项目进行调优，尽可能使用项目原本的依赖和虚拟环境

### 2.3 批量执行模块（用户手工执行生成的测试脚本）
- **功能**：
  - 用户手动运行 `agent-tuning/runner/test_runner.py`
  - 简单机制实现批量测试执行和结果收集
  - 每次执行自动创建一个新的结果版本目录
- **输出**：
  - 结果输出为自动创建的当前版本目录下的 `results.csv`
  - 如果服务中存在执行日志，则采集并存储为同版本目录下的 `app.log`
- **版本目录兼容要求**：
  - 批量执行模块需要自动识别下一版本号，例如 `v1`、`v2`
  - 每轮执行结果必须写入 `agent-tuning/results/{version}/`
  - 不同版本的 `results.csv`、`app.log` 互不覆盖
  - 用户不需要指定版本号或结果目录

### 2.4 异常筛选模块（Codex Skill 或脚本执行）
- **功能**：
  - 两种模式：
    1. **规则模式**：Skill 生成筛选脚本，如果筛选脚本存在则用户直接运行，执行后输出异常数据 CSV 文件 `abnormal_cases.csv`
    2. **大模型模式**：通过 Skill 触发大模型进行异常筛选，基于当前版本目录下的 `results.csv` 将异常数据筛选生成新的 CSV 文件 `abnormal_cases.csv`
- **数据确认机制**：
  - 在筛选过程中，如果输入数据存在未识别或不明确字段，Skill 提示用户确认，以避免误判
- **输出**： 
  - 规则模式下生成共享筛选脚本 `agent-tuning/runner/filter_abnormal.py` 用于后续重复执行；
  - 当前版本目录下的异常筛选数据 `abnormal_cases.csv`
- **版本目录兼容要求**：
  - 筛选模块需要自动识别当前版本目录，默认选择 `agent-tuning/results/` 下最新且包含 `results.csv` 的版本目录
  - 筛选模块需要从当前版本目录读取 `results.csv`
  - 筛选模块需要将 `abnormal_cases.csv` 写入同一个当前版本目录
  - `agent-tuning/runner/filter_abnormal.py` 为跨版本共享脚本，不需要按版本复制
  - 用户不需要指定版本号或结果目录

### 2.5 异常归因分析与最终报告生成（Codex Skill）
- **功能**：
  - 对异常数据进行归因分析
  - 生成最终 Markdown 报告，包含：
    - 执行摘要
    - 测试结果统计
    - 异常数据清单
    - 归因分析
- **输出文件**：
  - 当前版本目录下的异常报告 `report.md`
- **版本目录兼容要求**：
  - 报告生成模块需要自动识别当前版本目录，默认选择 `agent-tuning/results/` 下最新且包含 `results.csv` 的版本目录
  - 报告生成模块需要读取当前版本目录下的 `results.csv`、`abnormal_cases.csv` 和 `app.log`
  - 报告需要写入同一当前版本目录的 `report.md`
  - 报告内容需要标注本次调优版本号，便于后续追溯
  - 用户不需要指定版本号或结果目录

### 2.6 Agent 调优模块（Codex Skill）
- **功能**：
  - 根据归因报告生成调优方案
  - 根据调优方案，对原有 Agent 进行调优
- **版本目录兼容要求**：
  - 调优模块需要自动识别当前版本目录，默认选择 `agent-tuning/results/` 下最新且包含 `report.md` 的版本目录
  - 调优模块需要以当前版本目录下的 `report.md` 作为本轮调优依据
  - 调优完成后，下一轮测试执行时由批量执行模块自动创建新的版本目录，例如从 `v1` 调优后自动进入 `v2`
  - 当前阶段只要求版本化结果隔离与追溯，不要求实现跨版本对比、汇总或回滚
  - 用户不需要指定版本号或结果目录
- **数据确认机制**：
  - 在调优操作涉及不确定参数或不一致配置时，Skill 提示用户确认修改内容
- **输出**：
  - 调整后的 Agent 服务

---

## 3. 技术栈
- **Codex Skill**：实现测试脚本生成、异常筛选、归因分析和 Agent 调优
- **数据标准化**：所有模块输出统一为 CSV 文件，Markdown 报告
- **版本化结果目录**：通过 `agent-tuning/results/{version}/` 隔离多轮调优结果，`agent-tuning/runner/` 作为跨版本共享脚本目录
- **自动版本识别**：各模块通过扫描 `agent-tuning/results/` 自动创建或识别当前版本，减少用户手工传参
- **可扩展性**：新增 Agent 处理步骤可以通过增加 Agent 优化步骤来完成

---

## 4. 版本管理要求
- **统一父目录**：
  - 所有调优相关产物统一放在 `agent-tuning/` 下
  - `agent-tuning/runner/` 存放跨版本共享脚本
  - `agent-tuning/results/` 存放按版本隔离的结果
- **版本命名**：
  - 每一轮调优结果使用 `v1`、`v2`、`v3` 等目录表示
  - 版本号由系统自动生成，用户不需要手工指定
- **当前版本创建规则**：
  - 批量执行模块每次开始新一轮测试时自动创建下一版本目录
  - 下一版本号通过扫描 `agent-tuning/results/` 下已有 `vN` 目录决定
  - 如果不存在任何版本目录，则创建 `agent-tuning/results/v1/`
  - 如果最大版本为 `v3`，则下一轮创建 `agent-tuning/results/v4/`
- **当前版本读取规则**：
  - 异常筛选模块默认读取最新且包含 `results.csv` 的版本目录
  - 报告生成模块默认读取最新且包含 `results.csv` 的版本目录，并在同目录生成 `report.md`
  - Agent 调优模块默认读取最新且包含 `report.md` 的版本目录
  - 只有当目录损坏、多个候选版本状态冲突或缺少必要输入文件时，才需要提示用户确认
- **目录职责**：
  - `agent-tuning/runner/`：存放跨版本共享脚本，例如 `test_runner.py`、`filter_abnormal.py`
  - `agent-tuning/results/{version}/`：存放某一轮调优的测试结果、日志、异常数据和报告
- **兼容要求**：
  - 所有读取结果的模块都需要自动从当前版本目录读取输入
  - 所有生成结果的模块都需要自动写入当前版本目录
  - 各版本结果之间不能互相覆盖
  - 脚本逻辑应通过自动版本创建和自动版本识别复用，不为每个版本生成重复 runner 文件
- **未来扩展**：
  - 跨版本结果对比
  - 跨版本汇总报告
  - 版本回滚或基线版本标记
  - 当前阶段仅预留目录结构和兼容能力，不要求实现以上跨版本管理功能

---

## 5. 使用示例（手工执行）
1. 完成数据准备，包含数据集以及待调优的 Agent 服务
2. 执行批量测试脚本生成 Skill，生成共享脚本 `agent-tuning/runner/test_runner.py`，存在问题时需要确认输入字段
3. 用户手动运行 `agent-tuning/runner/test_runner.py`，脚本自动创建新版本目录，例如 `agent-tuning/results/v1/`
4. 执行异常筛选模块：
   - 规则模式：运行 Skill 生成的共享筛选脚本，自动读取当前版本的 `results.csv` 并输出 `abnormal_cases.csv`
   - 大模型模式：通过 Skill 触发大模型，自动读取当前版本的 `results.csv` 并生成 `abnormal_cases.csv`
5. 执行归因分析 Skill，自动读取当前版本结果并生成最终 Markdown 报告，例如 `agent-tuning/results/v1/report.md`
6. 执行 Agent 调优 Skill，自动读取当前版本 `report.md` 并完成 Agent 调优
7. 如需继续下一轮调优，用户再次运行 `agent-tuning/runner/test_runner.py`，脚本自动创建下一版本目录，例如 `agent-tuning/results/v2/`

---

## 6. 输出文件结构
```text
/agent-tuning/
├── runner/
│   ├── test_runner.py          # 跨版本共享测试脚本
│   └── filter_abnormal.py      # 跨版本共享异常数据过滤脚本
└── results/
    ├── v1/
    │   ├── results.csv          # v1 测试结果
    │   ├── app.log              # v1 执行日志（可以没有）
    │   ├── abnormal_cases.csv   # v1 异常数据
    │   └── report.md            # v1 最终测试报告
    └── v2/
        ├── results.csv          # v2 测试结果
        ├── app.log              # v2 执行日志（可以没有）
        ├── abnormal_cases.csv   # v2 异常数据
        └── report.md            # v2 最终测试报告
```

---

## 7. 产品交付要求
1. **Codex Skill**：
   - 模块化 Skill 模板，覆盖测试脚本生成、异常筛选、归因分析/报告、Agent 调优
   - 异常筛选支持规则模式和大模型模式
   - 内置数据不确定性提示机制，引导用户确认字段和格式
   - 所有模块兼容 `agent-tuning/results/{version}/` 输出结构
   - 所有模块自动创建或识别当前版本，常规流程中不要求用户指定版本号
   - `agent-tuning/runner/` 目录中的脚本为跨版本共享脚本，不按版本重复生成
2. **可安装部署**：
   - Codex Skill 可注册至 Codex 系统
