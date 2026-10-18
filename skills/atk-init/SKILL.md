---
name: atk-init
description: 检查并接入已有本地 Agent，保留其原有加载行为。
---

# 接入已有 Agent

阅读 `../WORKFLOW.md`，遵循其中默认中文的产物规则。
Python 项目需核对原有日志初始化、logger/sink、级别、传播、线程/异步上下文及原生审计位置。
按已核验方式声明 `observability`（见使用指南）；新接入使用 `format=native` 和 `logs` 来源规则，
通过随包 `logging_capture.py` 增加标准 logging Handler 或 Loguru sink，保存 attempt 内原格式日志。
保留项目原出口、传播与级别；标准 logging 已转接 Loguru 时避免重复采集。stderr 是兜底来源。
用受控正常/异常/并发检查确认覆盖范围，记录于项目说明；文件存在或 root Handler 安装成功不证明全部日志可见。
不生成逐轮采集脚本，不默认更换日志库或扩大正文范围。
仅导入分析时，调用 `initialize_project`，设置 `analysis_only=true`，可选 `redact_keys`；无需 Git 或 runner。
无 Git 的获准探针需提供 `python`、`command`、`components`、`external_effects` 和 `runtime_notes`，
再在 Round 的 `analysis_plan` 中冻结许可与预算。后续用 `configure_runtime=true` 和完整 runtime 配置升级项目，
保留已有 Round、证据和脱敏键；升级前先恢复已暂停的分析 Round。

对可执行项目，创建 runner 前检查目标源码与项目指令，确认入口命令、Python／Node 环境、工作目录、输入映射、
会话隔离、缓存重置、外部副作用和日志。业务 Skill 还需查明发现目录、路径基准、实际加载阶段、缓存、外部副本，
以及如何观察候选文件指纹被实际加载或调用。明确记录未知项；文件存在不证明实际加载。
对每个必需加载组件，让目标 instrumentation 在每次 attempt 的 `loading.json` 中记录实际绝对路径 `resolved_path`，
以及 `component_id`、`state` 和 `fingerprint`；把配置路径抄进字段不构成证据。
Agent 加载隔离副本时，可信适配器可根据观察到的复制步骤记录绝对 `staged_from_path`。
保留加载器事件定位，不仅凭内容相同推断来源。

Case 含附件时，配置 Agent 命令读取默认 runner 的 `{attachments_file}` 参数。
这是每次 attempt 的附件绝对路径及 SHA-256 JSON 清单；不得把预期答案或评分规则作为 Agent 输入。

调用 `initialize_project` 时提供 `python`、argv 数组 `command`、`components`、`allowed_paths`、`protected_paths`、
`external_effects` 和详细 `runtime_notes`。只有核实无外部写入后才能将 `external_effects` 设为 `[]`。
操作生成 `.atk/project.json`、`.atk/runtime.md` 和 `.atk/adapters/runner.py`，并把 `.atk/` 加入本地 Git exclude。
日志辅助模块按模式生成：native 仅 `logging_capture.py`，旧 stderr 格式仅 `logging_json.py`，未配置观测时不生成。
检查生成的适配器，仅作保持请求／响应契约所需的项目调用适配。不要新建 Agent、运行完整数据集、提交已有修改或替换旧 `.atk` 数据。
若已有旧数据，停止并说明冲突。

在 `external_effects` 中将每项已知外部写入声明为命名对象。重放前逐项补充 `protection: {kind, evidence_ref}`：
`kind` 为 `test_environment`、`stub` 或 `approved_safeguard`；`evidence_ref` 指向 `runtime.md` 或其他本地记录中的已核验设置。
缺少此记录会阻断重放。核实保护设置本身；引用记录不等于操作系统沙箱。

需要成本或工具调用门禁时，检查适配器后配置 `metric_sources`，提供独立采集器名称及 `evidence_ref`。
适配器在每条 Record 的 `metric_sources` 中为度量值记录该名称。
默认 runner 的耗时来源为 `runner_clock`，Agent 写入的 `metrics.json` 来源为 `agent_sidecar`；
后者仅作诊断，不能满足正式成本或工具调用门禁，也不能被标为独立采集。

适配器可识别基础设施失败（如模型认证失败）时，将其独立非零退出码作为 `infrastructure_exit_codes` 传入。
这些码仅用于无法作为效果证据的 attempt；普通 Agent 失败使用其他退出码。

固定远程组件的实际版本重要时，配置只读 argv 数组 `version_command`，已知时提供 `expected_version`，可选
`version_timeout_seconds`。命令只输出不含秘密的版本标识。ATK 在批次前后执行它；记录命令如何触达实际服务，
不能仅凭本地源码元数据推断服务版本。

## 接入产物与收尾

初始化请求、响应放系统临时目录，核对返回状态后清理；首次调用前不预建 `.atk/initialization/`。
按项目需要编写适配器，维护测试遵循目标项目既有规范；不默认新增 README、输入结构报告、
`verification.json`、独立门禁回执或逐轮采集脚本。数据整理交给 atk-dataset。

迭代测试在自动清理的临时目录进行，不累积 `run-N`、pytest 夹具、网络 guard 或合成索引。
Python 自检可在子进程设置 `PYTHONDONTWRITEBYTECODE=1`，避免新增字节码缓存混入接入产物，不改写用户环境配置。
适配和配置稳定后，在 `.atk/self-check/` 直接运行最后一轮已获准且隔离已核验的合成/测试检查；
不搬运含绝对路径的输出，不覆盖未知的旧现场，不绕过正式重放的外部副作用门禁。
核对执行结果、必需日志/阶段、实际加载组件、保留文件的引用和指纹，不能只看退出码。

- 全部必需检查通过：先把命令、范围、结果及实际版本/指纹写入 `runtime.md`，再清理本次自检目录和临时请求。
  摘要明确“原始现场已清理”；检查清理结果，移除失效引用，清理失败时说明残留，不称已清理。
- 失败或中断：在 `self-check/` 保留一套有用现场和简短 `checks.log`，记录命令、退出码/中断状态、未通过项和位置。
  保留 runner 原始 Record、加载证据及其引用的日志、结果和审计；不另存重复正文或无关夹具。
- 未执行：在说明中写原因，不创建空自检目录；unknown 或部分通过不能按成功清理。

仅清理本次生成且未被正式对象引用的临时材料；已有项目数据、封存证据及恢复信息保持原有保护。
故障修复后重新核验相关必需检查，再按成功规则清理本次现场。
`runtime.md` 是人的唯一接入说明入口：开头写状态、运行入口、验证摘要、限制和下一步；失败时链接实际现场。
配置以 `project.json` 为准，不复制完整配置或输出；保留外部保护依据对应章节，不链接已清理文件。
稳定后又改动 adapter、runner/helper 或配置时须重新验证，不沿用旧通过结论。
