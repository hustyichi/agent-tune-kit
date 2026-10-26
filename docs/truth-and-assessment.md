# 真值复核、校准与判定追溯

CLI 负责固定校验与保存；Skill 确定业务标准、整理来源并解释语义分歧。
记录只验证内容对应关系，不认证审阅者身份，也不自动证明资料权威或答案正确。
旧数据和旧三列反馈保持兼容，不自动升级为已确认。

## 形成标准

从资料和代表样本提出：必须满足、允许变化、硬失败、何时未知。答案可以是精确值、
可接受结果或产物验收条件，不要求唯一参考文本或工具调用顺序。展示来源定位、支持结论的
证据及冲突，只向用户集中确认真正的业务取舍；明确授权的批量确认无需逐条点击。

长期共识仍放 `.atk/context.md`。复核只绑定 `## Agent Behavior Standard` 与
`## Ground Truth Standard` 两节及其下级内容，普通调优记录不影响摘要。
没有这两节时快照为空，表示没有额外项目级标准，不表示已完成评分校准。

## 复核数据

```sh
atk dataset inspect --direct --field expected --input-fields input context --json
atk visualize dataset --no-open
atk dataset update --review-path dataset_review.csv --field expected --json
```

`inspect` 返回数据集字节 SHA-256、路径身份 `dataset_scope`、相关 `standard` 快照和逐行 `truth`。
对话确认可由 Skill 据此构造临时复核 CSV，不需要 HTML。新版导出字段：

| 字段 | 含义 |
|---|---|
| `atk_id,row_number,review_feedback` | 保留旧字段；row_number 仅辅助展示 |
| `review_status` | pending / confirmed / disputed；留空表示只有修订意见 |
| `field` | 原答案字段，不强制改名为 ground_truth |
| `input_fields` | JSON 字符串数组，明确绑定的输入和上下文字段 |
| `source,evidence,reviewer` | 来源及版本定位、支持结论的证据或业务裁决、复核人声明 |
| `dataset_sha256,dataset_scope,standard_sha256` | 从本次 inspect/页面取得，不能给旧反馈重盖新摘要 |

确认需要非空答案、来源、依据和复核人。复核文件绑定导出时数据、路径及标准；文件移动或
变化后重新核对，不能仅按相同 ID 套用。旧三列反馈可用于定向修订，但不能证明有效确认。
同一 ID 可以按不同 field 分别确认；确认 label 不会确认 acceptance_criteria。

可选保留列 `atk_truth` 使用 JSON：`schema:1`，`fields` 按答案字段索引，每项包含
`status,input_fields,source,evidence,reviewer,reviewed_at,standard,subject_sha256`。
standard 包含 `text,sha256`，由模块计算；不要手写或通过 `--field atk_truth` 修改。
输入、答案或相关标准变化后，读取时派生 stale；未绑定备注变化不使确认失效。
同批生成的新答案不继承针对旧答案的确认，降为 pending；答案和复核记录一次原子写入。
浏览器本地选择导入后才生效，缓存按数据集和标准摘要隔离；复核前核对所选输入字段。
重复同样声明不制造新复核时间。来源引用可变时，应在 source 中明确不可变版本或必要证据。

`atk_truth`、答案与隐藏断言不是 Agent 输入；共享 managed runner 删除 atk_truth，接入时仍需
核验业务输入白名单和实际文件可读范围。历史旧 runner 不会自动改写。

## 判定与历史

`judgments.csv` 保留四个基本字段，新增可选 `assessment` JSON 列：

```json
{"schema":1,"basis":"truth","input_fields":["input"],"truth_fields":["expected"],"standard":{"text":"实际标准","sha256":"digest(text)"},"judge":{"kind":"semantic","identity":"实际可得的裁判身份；未知应明示","instructions":"实际采用的判定指令"}}
```

摘要占位符不能直接使用。`digest` 是 `agent_tune_kit.datasets.truth.digest`，即稳定 JSON 编码
的 SHA-256；标准对象可直接复用复核记录。`results_sha256` 仍是原结果文件字节 SHA-256。
规则裁判保存 kind=rule、identity、source 完整代码和 digest(source)，不假设 Git 已提交。

- truth 依据的确定判断必须引用已确认、未过期且标准一致的字段。
- independent 用于有独立证据的明确约束，须提供非空标准；格式通过不能代表整体通过。
- 有 atk_truth 而缺 assessment 的确定判断被拒绝；缺依据保留 unknown。
- 无新增契约的旧结果仍可读，但不能声称可信性已验证或裁判已校准。
- reason 说明条件与观察证据；已有 evidence_refs 如适用继续保存并按现场契约校验。

仍使用 `atk results apply-judgments` 校验并保存。替换前将原非空判定归档到
`judgment-history/<原文件SHA256>.csv`，保留原标准和裁判内容；失效判定不能被失败表恢复。
标准修订后，可在 assessment 加 `reference_snapshot`（新的已确认样本行，含 atk_truth），
其 atk_id 和 input_fields 必须与原结果一致。这样可以用新标准重判旧输出，无需调用 Agent；
原 eval_results 和封存现场不变，不能把重判当作 Agent 改善。
可选 `calibration` 保存校准结果 path 和文件字节 sha256；引用丢失或变化会使依赖判断失效。
移交时一并保留校准证据；摘要不证明校准质量合格。

## 项目规则与校准

规则可在 `judge(row)` 外声明 `ATK_CRITERIA`（实际标准文本）、`ATK_INPUT_FIELDS`、
`ATK_TRUTH_FIELDS` 和 `ATK_BASIS`（默认 truth）。run-rule 据此保存代码/标准快照。
未覆盖或未确认的依据保留 unknown；旧规则不能绕过带 atk_truth 样本的复核要求。

独立校准 CSV 保留业务输入及 agent_output，并包含唯一 atk_id、expected_verdict、expected_reason、
source、reviewer、criteria。人工金标字段不传给规则 judge。至少覆盖正反例、边界与证据不足；
不把优化失败集当作充分的独立校准证据。

```sh
atk results calibrate --samples-path calibration.csv --rule-path .atk/runner/failure_rule.py --output-path calibration-r1.csv --json
atk results calibrate --samples-path calibration.csv --judgments-path semantic.csv --judge-id actual-judge --instructions '实际指令' --output-path calibration-r2.csv --json
```

规则在项目 Python 环境运行，可指定 --python；项目代码是受信任代码，不提供沙箱。
语义判断由当前 Skill 完成，提交完整且唯一的 atk_id,verdict,reason，不新增模型调用层。
语义预测应先于读取人工金标；已经看过金标时说明这是非盲校准，不能声称独立准确率。
输出必须是新文件，保留金标、实际判断、理由、裁判快照与源摘要；不会创建评测版本或运行 Agent。
摘要列出匹配、误通过（failed→passed）、误失败（passed→failed）、未知、错误和所有分歧 ID。
裁判错误不等于 Agent 失败。标准或裁判改变时重校准；合格阈值由业务决定，不发明统一合格率。

## 报告

inspect 的 truth_counts 仅汇总各行已记录字段状态，不证明全部业务条件已覆盖。
复核覆盖、判定覆盖与通过/失败/未知分别展示。compare.current_status 是本轮目标状态，
comparison_differences 列出标准、裁判、字段或参考的差异/缺口；新增契约有差异时 status 为无法判断。
相同标准也仍需核对实验条件，不能直接推断因果改善。现场方案如已启用，复用其 context 和证据引用，
不向已封存现场追加后来修订的判定标准。
